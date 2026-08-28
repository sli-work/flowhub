"""LangGraph execution boundary for versioned Expert Deployments."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import ExpertApproval, ExpertChatMessage, ExpertChatSession, ExpertDeployment, ExpertRun, ExpertRunEvent, ExpertVersion, LlmProvider, LlmProviderModel, Project, TaskItem, User, WorkItem
from flowhub_api.services.crypto import decrypt_secret

logger = logging.getLogger("flowhub_api")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:12]}"


class GraphState(TypedDict, total=False):
    run_id: str
    prompt: str
    output: str
    write_intent: bool
    approved: bool
    history: str


def checkpoint_dsn() -> str:
    from flowhub_api.core.config import get_settings

    return get_settings().sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def setup_checkpointer() -> None:
    """Run LangGraph checkpoint migrations once during application startup.

    Request transactions may already hold locks on FlowHub tables; running these
    migrations inside a Run request can block PostgreSQL indefinitely.
    """
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
        await checkpointer.setup()


def build_graph(provider: LlmProvider, model: LlmProviderModel, system_prompt: str):
    async def context_node(state: GraphState) -> dict:
        return {"prompt": state["prompt"]}

    async def approval_node(state: GraphState) -> dict:
        decision = interrupt({"tool": "flowhub.native.write", "risk": "write_commit", "scope": state["prompt"]})
        return {"approved": bool(decision)}
    async def model_node(state: GraphState) -> dict:
        llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=15, max_retries=0)
        history = (state.get("history") or "").strip()
        messages = [("system", system_prompt)]
        if history:
            messages.append(("system", f"以下是会话历史（含已压缩的早期轮次）：\n{history}"))
        messages.append(("human", state["prompt"]))
        response = await llm.ainvoke(messages)
        return {"output": str(response.content)}

    def route_after_context(state: GraphState) -> str:
        return "approval" if state.get("write_intent") else "model"

    graph = StateGraph(GraphState)
    graph.add_node("context", context_node)
    graph.add_node("approval", approval_node)
    graph.add_node("model", model_node)
    graph.add_edge(START, "context")
    graph.add_conditional_edges("context", route_after_context, {"approval": "approval", "model": "model"})
    # 受治理写入：审批通过后继续调用模型生成产出（此前 approval → END 导致 write_intent 运行永远没有模型输出）
    graph.add_edge("approval", "model")
    graph.add_edge("model", END)
    return graph


async def add_event(session: AsyncSession, run_id: str, sequence: int, kind: str, status: str, title: str, payload: dict, duration_ms: int = 0) -> None:
    session.add(ExpertRunEvent(id=new_id("ere"), run_id=run_id, sequence=sequence, kind=kind, status=status, title=title, payload=payload, duration_ms=duration_ms, created_at=now_iso()))


async def validate_version(session: AsyncSession, version: ExpertVersion) -> list[str]:
    blockers: list[str] = []
    if not version.system_prompt.strip():
        blockers.append("请填写 System Prompt")
    model = await session.get(LlmProviderModel, version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not model.enabled or not provider or provider.status != "healthy" or not provider.credential_configured:
        blockers.append("请选择健康且已配置凭据的 Provider 与模型")
    return blockers


async def execute_run(session: AsyncSession, run: ExpertRun, version: ExpertVersion, user: User, history: str = "", provider_model_id: str | None = None) -> None:
    # 测试会话可以固定使用指定模型；正式 Deployment/普通测试保持版本配置不变。
    model = await session.get(LlmProviderModel, provider_model_id or version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not provider:
        run.status = "failed"
        run.error = "Provider 配置不存在"
        run.finished_at = now_iso()
        await add_event(session, run.id, 1, "trace", "failed", "加载 Provider 失败", {"error": run.error})
        return
    await add_event(session, run.id, 1, "trace", "succeeded", "加载 Expert Version", {"version": version.version})
    await add_event(session, run.id, 2, "trace", "succeeded", "加载权限与 Skill", {"skills": version.skills, "user": user.id})
    try:
        async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
            graph = build_graph(provider, model, version.system_prompt).compile(checkpointer=checkpointer)
            result = await graph.ainvoke({"run_id": run.id, "prompt": run.input, "write_intent": run.status == "interrupted", "history": history}, {"configurable": {"thread_id": run.id}})
        if "__interrupt__" in result:
            expires = datetime.now(UTC) + timedelta(minutes=30)
            session.add(ExpertApproval(id=new_id("eap"), run_id=run.id, tool_name="flowhub.native.write", risk="write_commit", scope=run.input, status="pending", expires_at=expires.isoformat(timespec="seconds"), created_at=now_iso()))
            await add_event(session, run.id, 3, "approval", "pending", "等待 LangGraph interrupt 审批", {"risk": "write_commit", "scope": run.input})
            return
        run.output = str(result.get("output", ""))
        run.status = "succeeded"
        await add_event(session, run.id, 3, "model", "succeeded", "LangGraph 模型节点完成", {"output": run.output[:1000]})
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = "模型服务暂不可用，请检查 Provider 连接或稍后重试"
        await add_event(session, run.id, 3, "model", "failed", "模型调用失败", {"error": run.error})
    finally:
        run.finished_at = now_iso()


async def resume_approved_run(session: AsyncSession, approval: ExpertApproval, user: User) -> ExpertRun:
    run = await session.get(ExpertRun, approval.run_id)
    if run is None:
        raise ValueError("关联 Run 不存在")
    version = await session.get(ExpertVersion, run.expert_version_id)
    if version is None:
        raise ValueError("关联 Expert Version 不存在")
    model = await session.get(LlmProviderModel, version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not provider:
        raise ValueError("Provider 配置不存在")
    run.status = "running"
    await add_event(session, run.id, 4, "approval", "succeeded", "审批通过，恢复 LangGraph Run", {"approved_by": user.id})
    try:
        async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
            graph = build_graph(provider, model, version.system_prompt).compile(checkpointer=checkpointer)
            result = await graph.ainvoke(Command(resume=True), {"configurable": {"thread_id": run.id}})
    except Exception as exc:  # noqa: BLE001
        # 审批后的模型生成可能因 Provider 不可达失败：标记运行失败，任务由人工兜底
        run.status, run.error = "failed", "模型服务暂不可用，请检查 Provider 连接或稍后重试"
        run.finished_at = now_iso()
        await add_event(session, run.id, 5, "model", "failed", "审批后模型调用失败", {"error": type(exc).__name__})
        return run
    # 审批后继续模型节点：有真实产出则记录（替代此前的固定文案），供节点表单回填
    output = str(result.get("output", "") or "") if isinstance(result, dict) else ""
    run.output = output or "受治理写入动作已在 LangGraph 恢复路径中执行一次。"
    run.status, run.finished_at = "succeeded", now_iso()
    await add_event(session, run.id, 5, "tool", "succeeded", "LangGraph 恢复完成", {"tool": approval.tool_name})
    return run


async def start_deployment_run(session: AsyncSession, deployment_id: str, prompt: str, user: User, *, write_intent: bool = False, task_id: str | None = None) -> ExpertRun:
    """Start a workflow-bound Expert Deployment using its pinned version."""
    deployment = await session.get(ExpertDeployment, deployment_id)
    if deployment is None or deployment.status != "active":
        raise ValueError("Expert Deployment 不存在或未启用")
    version = await session.get(ExpertVersion, deployment.expert_version_id)
    if version is None:
        raise ValueError("Deployment 固定的 Expert Version 不存在")
    run = ExpertRun(
        id=new_id("run"), expert_id=deployment.expert_id, expert_version_id=version.id,
        deployment_id=deployment.id, requested_by=user.id, status="interrupted" if write_intent else "running",
        task_id=task_id, input=prompt, trace_id=new_id("trace"), started_at=now_iso(),
    )
    session.add(run)
    await session.flush()
    await execute_run(session, run, version, user)
    return run


# ---------- 节点表单 AI 填充：schema 输出契约 / 解析矫正 / 文档生成 ----------

def build_schema_output_instruction(schema: list[dict]) -> str:
    """把节点 FormField[] 转成模型可执行的严格 JSON 输出契约。"""
    if not schema:
        return ""
    lines = ["## 输出契约（严格遵守）", "基于以上任务书完成节点产出，只输出一个 JSON 对象，不要输出任何其他文字。字段如下："]
    for f in schema:
        key, ftype = f.get("key", ""), f.get("type", "input")
        required = "必填" if f.get("required") else "选填"
        desc = f.get("placeholder") or f.get("hint") or ""
        if ftype in ("select", "radio"):
            opts = "；".join(f"{o.get('label')}={o.get('value')}" for o in (f.get("options") or []))
            lines.append(f"- {key}（{f.get('label')}，{required}，单选，只能取以下 value 之一：{opts}）")
        elif ftype == "multiselect":
            opts = "；".join(f"{o.get('label')}={o.get('value')}" for o in (f.get("options") or []))
            lines.append(f"- {key}（{f.get('label')}，{required}，多选，输出 value 数组，只能取：{opts}）")
        elif ftype in ("upload", "file"):
            lines.append(f"- {key}（{f.get('label')}，{required}，文件产出）：输出该文档的完整 Markdown 正文")
        elif ftype == "number":
            lines.append(f"- {key}（{f.get('label')}，{required}，数字）：{desc}")
        elif ftype == "date":
            lines.append(f"- {key}（{f.get('label')}，{required}，日期 YYYY-MM-DD）：{desc}")
        else:
            lines.append(f"- {key}（{f.get('label')}，{required}，文本）：{desc}")
    return "\n".join(lines)


def parse_schema_output(schema: list[dict], raw: str) -> tuple[dict, list[str]]:
    """解析模型输出为表单值：容错提取 JSON，按字段类型矫正（选项约束/数值/数组）。
    upload/file 字段的字符串视为文档正文，由调用方转成文档引用。解析失败返回 ({}, warnings)。"""
    warnings: list[str] = []
    if not raw or not raw.strip():
        return {}, ["模型无输出"]
    match = None
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start >= 0 and end > start:
            match = raw[start:end + 1]
            break
    if not match:
        return {}, ["模型输出中未找到 JSON"]
    try:
        parsed = json.loads(match)
    except Exception:  # noqa: BLE001
        return {}, ["模型输出无法解析为 JSON"]
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    if not isinstance(parsed, dict):
        return {}, ["模型输出 JSON 结构不符合预期"]
    values: dict = {}
    for f in schema:
        key, ftype = f.get("key", ""), f.get("type", "input")
        if key not in parsed:
            if f.get("required"):
                warnings.append(f"「{f.get('label', key)}」缺少生成值")
            continue
        raw_value = parsed[key]
        if ftype in ("select", "radio"):
            option_values = [o.get("value") for o in (f.get("options") or [])]
            if raw_value in option_values:
                values[key] = raw_value
            else:
                by_label = next((o for o in (f.get("options") or []) if o.get("label") == raw_value), None)
                if by_label:
                    values[key] = by_label.get("value")
                else:
                    warnings.append(f"「{f.get('label', key)}」的值不在可选项内，已留空")
        elif ftype == "multiselect":
            option_values = [o.get("value") for o in (f.get("options") or [])]
            label_map = {o.get("label"): o.get("value") for o in (f.get("options") or [])}
            items = raw_value if isinstance(raw_value, list) else [raw_value]
            picked = [label_map.get(x, x) for x in items if label_map.get(x, x) in option_values]
            values[key] = picked
        elif ftype == "number":
            try:
                values[key] = float(raw_value) if not float(str(raw_value)).is_integer() else int(float(raw_value))
            except (TypeError, ValueError):
                warnings.append(f"「{f.get('label', key)}」不是有效数字，已留空")
        else:
            text = str(raw_value).strip()
            if text:
                values[key] = text
            elif f.get("required"):
                warnings.append(f"「{f.get('label', key)}」生成为空")
    return values, warnings


async def create_document_from_text(session: AsyncSession, *, wi_id: str, project: str, name: str, content: str, uploader: User) -> dict:
    """把 AI 生成的文本产出落库为工作项文档（MinIO 可用时写对象），返回表单引用 {id, name}。"""
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings
    from flowhub_api.models import DocItem

    doc_id = f"d{uuid4().hex[:8]}"
    filename = name if "." in name else f"{name}.md"
    data = content.encode("utf-8")
    doc = DocItem(
        id=doc_id, name=filename, project=project or "未归档",
        version="v1", level="L2", scan="已扫描", uploader=uploader.name,
        size=f"{len(data) // 1024}KB" if len(data) > 1024 else f"{max(len(data), 1)}B",
        time="刚刚", kind="节点表单附件", wi=wi_id or None,
    )
    minio = get_minio()
    if minio:
        bucket = get_settings().minio_bucket
        doc.object_name = f"{doc.id}/{filename}"
        minio.put_object(bucket, doc.object_name, BytesIO(data), len(data))
    session.add(doc)
    return {"id": doc.id, "name": doc.name}


async def generate_task_form_values(session: AsyncSession, *, task: TaskItem, schema: list[dict], prompt: str, user: User, deployment_id: str) -> tuple[ExpertRun, dict, list[str]]:
    """运行绑定的 Expert 生成表单值：run（task 关联）→ 解析矫正 → upload 字段生成文档。
    返回 (run, values, warnings)；run.status != succeeded 时 values 为空并附错误 warning。"""
    run = await start_deployment_run(session, deployment_id, prompt, user, task_id=task.id)
    if run.status != "succeeded":
        return run, {}, [run.error or "Expert 运行未成功"]
    values, warnings = parse_schema_output(schema, run.output)
    # upload/file 字段的字符串值 = 文档正文 → 转为文档引用
    for f in schema:
        key, ftype = f.get("key", ""), f.get("type", "")
        if ftype in ("upload", "file") and isinstance(values.get(key), str) and values[key].strip():
            ref = await create_document_from_text(
                session, wi_id=task.wi_id, project=task.project,
                name=f"{f.get('label', key)}-{task.id}", content=values[key], uploader=user,
            )
            values[key] = [ref]
    return run, values, warnings



# 会话历史组装 + token 预算制增量压缩（Claude Code 式）：
# - 预算 = provider.max_context_tokens × 80%（默认 100 万窗口 → 80 万 token）→ 换算 UTF-8 字节
# - marker 之前的早期消息固化进 summary（只压缩一次）；上下文 = 摘要 + marker 之后的消息
# - 压缩在后台异步执行（compact_session_async），不阻塞用户当前问答
BYTES_PER_TOKEN = 3                  # UTF-8 字节 → token 近似（中文 1 token ≈ 3 字节）
DEFAULT_MAX_CONTEXT_TOKENS = 1_000_000
BUDGET_RATIO = 0.8
FULL_ROUNDS = 6                      # 推进 marker 时保留最近 N 轮全文（近因完整）
ROUND_SUMMARY_CHARS = 80


def context_budget_bytes(max_context_tokens: int | None = None) -> int:
    """上下文预算（UTF-8 字节）= 模型窗口 × 80% × 字节/token。"""
    tokens = (max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS) * BUDGET_RATIO
    return int(tokens * BYTES_PER_TOKEN)


def build_session_history(
    rows: list[tuple[int, str, str]],  # (sequence, role, content) 升序
    summary: str = "",
    marker: int = 0,
    budget_bytes: int | None = None,
) -> tuple[str, str, int, bool]:
    """推进式压缩（后台用）：返回 (context_text, new_summary, new_marker, compacted)。

    触发：摘要 + marker 后消息字节 > 预算（且增量 > FULL_ROUNDS 轮）；
    压缩：合并提炼 → 新摘要替换旧摘要（marker 推进）。
    """
    pairs = [(seq, r, c) for seq, r, c in rows if r in ("user", "assistant") and c.strip()]
    recent = [p for p in pairs if p[0] > marker]
    if not recent:
        return (summary or ""), summary, marker, bool(summary)
    budget = budget_bytes if budget_bytes is not None else context_budget_bytes()
    total_bytes = len(summary.encode("utf-8")) + sum(len(c.encode("utf-8")) for _, _, c in recent)
    if total_bytes <= budget or len(recent) <= FULL_ROUNDS * 2:
        recent_text = "\n".join(f"{'用户' if r == 'user' else '助手'}: {c}" for _, r, c in recent)
        text = (summary.rstrip() + "\n" if summary else "") + recent_text
        return text, summary, marker, bool(summary)
    keep = recent[-(FULL_ROUNDS * 2):]
    to_summarize = recent[:-(FULL_ROUNDS * 2)] if len(recent) > FULL_ROUNDS * 2 else []
    new_marker = max((p[0] for p in to_summarize), default=marker)
    summary_lines = []
    for i in range(0, len(to_summarize), 2):
        chunk = to_summarize[i:i + 2]
        q = next((c for _, r, c in chunk if r == "user"), "")
        a = next((c for _, r, c in chunk if r == "assistant"), "")
        summary_lines.append(f"[早期对话] 问: {q[:ROUND_SUMMARY_CHARS]} 答: {a[:ROUND_SUMMARY_CHARS]}")
    new_summary = (summary.rstrip() + "\n" if summary else "") + "\n".join(summary_lines)
    keep_text = "\n".join(f"{'用户' if r == 'user' else '助手'}: {c}" for _, r, c in keep)
    text = new_summary + "\n--- 近期对话（全文）---\n" + keep_text
    return text, new_summary, new_marker, True


def session_history_preview(
    rows: list[tuple[int, str, str]],
    summary: str,
    marker: int,
    budget_bytes: int | None = None,
) -> tuple[str, bool]:
    """预览（主请求用）：不推进 marker，服务当前轮。返回 (context_text, needs_compact)。"""
    pairs = [(seq, r, c) for seq, r, c in rows if r in ("user", "assistant") and c.strip()]
    recent = [p for p in pairs if p[0] > marker]
    if not recent:
        return (summary or ""), False
    budget = budget_bytes if budget_bytes is not None else context_budget_bytes()
    total_bytes = len(summary.encode("utf-8")) + sum(len(c.encode("utf-8")) for _, _, c in recent)
    recent_text = "\n".join(f"{'用户' if r == 'user' else '助手'}: {c}" for _, r, c in recent)
    text = (summary.rstrip() + "\n" if summary else "") + recent_text
    needs_compact = total_bytes > budget and len(recent) > FULL_ROUNDS * 2
    return text, needs_compact


async def compact_session_async(session_id: str, provider_max_tokens: int | None = None) -> None:
    """Claude Code 式后台压缩：不阻塞用户问答。幂等：marker 只进不退。"""
    from sqlalchemy import select as sa_select

    from flowhub_api.db.session import SessionFactory as _SF

    await asyncio.sleep(0.5)  # 等主请求事务稳定（避免与写消息竞态）
    async with _SF() as s:
        chat = await s.get(ExpertChatSession, session_id)
        if chat is None:
            return
        msgs = (await s.execute(sa_select(ExpertChatMessage).where(
            ExpertChatMessage.session_id == session_id).order_by(ExpertChatMessage.sequence))).scalars().all()
        rows = [(m.sequence, m.role, m.content) for m in msgs]
        _, new_summary, new_marker, compacted = build_session_history(
            rows, summary=chat.compaction_summary, marker=chat.compaction_sequence,
            budget_bytes=context_budget_bytes(provider_max_tokens),
        )
        if compacted and new_marker > chat.compaction_sequence:
            chat.compaction_sequence = new_marker
            chat.compaction_summary = new_summary
            await s.commit()
            logger.info("compact_session_async: %s marker→%d", session_id[:12], new_marker)


def _extract_keywords(text: str) -> list[str]:
    """从用户问题抽关键词：英文词 + 中文 2-gram（用于模糊匹配任务/工作项 title）。"""
    words = re.findall(r"[a-zA-Z0-9]+", text)
    cjk = "".join(c for c in text if "\u4e00" <= c <= "\u9fff")
    bigrams = [cjk[i : i + 2] for i in range(len(cjk) - 1)] if len(cjk) >= 2 else ([cjk] if cjk else [])
    stop_words = {"吗", "呢", "吧", "的", "了", "是", "在", "我", "你", "他", "她", "它", "吗", "啊", "什么", "怎么", "如何", "这个", "那个"}
    keywords = [w for w in words if len(w) >= 2 and w.lower() not in stop_words] + [b for b in bigrams if b not in stop_words]
    # 去重保序
    seen, out = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:10]


async def run_native_flowhub_chat(session: AsyncSession, prompt: str, user: User, provider_model_id: str | None = None, history: str = "", on_token: Callable[[str], Awaitable[None]] | None = None) -> tuple[str, list[dict]]:
    """Default LangGraph path for a chat without an Expert Deployment.

    It exposes safe, read-only FlowHub capabilities. Business writes remain a
    dedicated Expert/Deployment operation so they can be versioned and approved.
    """
    class NativeState(TypedDict):
        prompt: str
        answer: str
        tool_trace: list[dict]

    async def native_tools(state: NativeState) -> dict:
        tasks = (await session.execute(select(TaskItem).where(TaskItem.assignee == user.name))).scalars().all()
        work_items = (await session.execute(select(WorkItem))).scalars().all()
        projects = (await session.execute(select(Project).order_by(Project.updated.desc()))).scalars().all()
        trace: list[dict] = []
        # === 核心：把"目录"（标题列表）带进 context，让模型能识别用户问的具体任务 ===
        task_titles = [f"#{t.id[:8]} {t.title}({t.status}/{t.node})" for t in tasks[:25]]
        wi_titles = [f"#{w.id[:8]} {w.title}({w.status}/{w.type})" for w in work_items[:25]]
        proj_names = [f"{p.name}({p.status})" for p in projects[:10]]
        context = (
            f"用户 {user.name}。"
            f"我的任务({len(tasks)}，前 {len(task_titles)}): {'; '.join(task_titles) or '无'}。"
            f"工作项({len(work_items)}，前 {len(wi_titles)}): {'; '.join(wi_titles) or '无'}。"
            f"项目({len(projects)}，前 {len(proj_names)}): {'; '.join(proj_names) or '无'}。"
        )
        text = state["prompt"]
        # === 关键词模糊匹配：把用户问的具体任务/工作项的详情带进上下文 ===
        keywords = _extract_keywords(text)
        hits_task = [t for t in tasks if any(k in (t.title or "") for k in keywords)][:5]
        hits_wi = [w for w in work_items if any(k in (w.title or "") for k in keywords)][:5]
        matched_detail = ""
        if hits_task or hits_wi:
            trace.append({"tool": "flowhub.search.by_keyword", "status": "succeeded",
                          "summary": f"按关键词 {keywords} 匹配 {len(hits_task)} 任务 / {len(hits_wi)} 工作项"})
            lines_d = []
            for t in hits_task:
                lines_d.append(f"[任务] {t.id}「{t.title}」状态={t.status} 节点={t.node} 负责人={t.assignee} 更新={t.updated_at}")
            for w in hits_wi:
                lines_d.append(f"[工作项] {w.id}「{w.title}」状态={w.status} 类型={w.type} 进度={w.progress}")
            matched_detail = "\n".join(lines_d)
            answer = f"按你的问题匹配到以下条目：\n{matched_detail}"
        elif any(word in text for word in ("项目", "项目列表")):
            trace.append({"tool": "flowhub.project.list", "status": "succeeded", "summary": f"读取 {len(projects)} 个项目"})
            names = "、".join(f"{project.name}（{project.status}）" for project in projects[:10]) or "暂无项目"
            answer = f"当前项目：{names}。"
        elif any(word in text for word in ("问题", "issue", "故障")):
            issues = [item for item in work_items if item.type == "issue"]
            trace.append({"tool": "flowhub.work_item.list", "status": "succeeded", "summary": f"筛选到 {len(issues)} 条问题工作项"})
            names = "、".join(f"{item.id} {item.title}（{item.status}）" for item in issues[:10]) or "暂无问题工作项"
            answer = f"当前问题工作项：{names}。"
        elif any(word in text for word in ("任务", "待办", "工单")):
            trace.append({"tool": "flowhub.task.list", "status": "succeeded", "summary": f"读取 {len(tasks)} 条当前用户任务"})
            names = "、".join(f"{t.id} {t.title}（{t.status}）" for t in tasks[:15]) or "暂无任务"
            answer = f"你当前有 {len(tasks)} 条可见任务：{names}。系统共有 {len(work_items)} 个工作项。"
        elif any(word in text for word in ("流程", "状态")):
            trace.append({"tool": "flowhub.work_item.summary", "status": "succeeded", "summary": f"读取 {len(work_items)} 个工作项状态"})
            answer = f"FlowHub 当前有 {len(work_items)} 个工作项。"
        elif any(word in text for word in ("创建", "提交", "删除", "发布", "写入")):
            trace.append({"tool": "flowhub.write.request", "status": "approval_required", "summary": "默认对话不直接执行写入"})
            answer = "默认 FlowHub 对话仅提供只读能力。请选择已发布 Expert Deployment 执行受治理写入操作，系统会在 LangGraph 审批节点中断等待确认。"
        else:
            # 兜底也把目录列出来（用户/LLM 都能看到有哪些可问的）
            trace.append({"tool": "flowhub.context.directory", "status": "succeeded",
                          "summary": f"返回可见目录 {len(tasks)} 任务 / {len(work_items)} 工作项 / {len(projects)} 项目"})
            answer = (
                f"FlowHub 默认对话（只读）。你当前可见目录：\n{context}\n"
                "请告诉我你想了解哪个具体条目，或问'我的任务/工作项/项目'获取列表。"
            )
        trace = [{"kind": "tool", **item} for item in trace]
        if provider_model_id:
            model = await session.get(LlmProviderModel, provider_model_id)
            provider = await session.get(LlmProvider, model.provider_id) if model else None
            if not model or not provider or provider.status != "healthy" or not provider.credential_configured:
                return {"answer": "所选 Provider / 模型不可用，请检查 Provider 凭据和健康状态。", "tool_trace": trace}
            try:
                llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=15, max_retries=0)
                # 把"目录 + 关键词命中详情 + 已得 answer"都喂给 LLM，让它能基于真实数据回答
                history_text = (history or "").strip()
                human_parts = []
                if history_text:
                    human_parts.append(f"会话历史（早期轮次可能已压缩）：\n{history_text}")
                human_parts.append(f"{context}\n\n已查询结果：\n{answer}\n\n用户问题：{text}")
                messages = [("system", "你是 FlowHub 默认助手。仅基于提供的 FlowHub 只读上下文回答；不可声称已执行创建、提交、删除或发布。需要写操作时，提示用户选择已发布 Expert。回答简洁，给出任务/工作项 ID 便于用户定位。"), ("human", "\n\n".join(human_parts))]
                if on_token:
                    chunks: list[str] = []
                    stream = llm.astream(messages)
                    try:
                        async for chunk in stream:
                            token = str(chunk.content or "")
                            if token:
                                chunks.append(token)
                                await on_token(token)
                    finally:
                        close_stream = getattr(stream, "aclose", None)
                        if close_stream is not None:
                            await close_stream()
                    answer = "".join(chunks)
                else:
                    response = await llm.ainvoke(messages)
                    answer = str(response.content)
            except Exception as exc:
                trace.append({"kind": "model", "tool": "默认 Provider 模型", "status": "failed", "summary": "模型服务暂不可用；已回退到 FlowHub 本地查询结果"})
        return {"answer": answer, "tool_trace": trace}

    graph = StateGraph(NativeState)
    graph.add_node("native_tools", native_tools)
    graph.add_edge(START, "native_tools")
    graph.add_edge("native_tools", END)
    result = await graph.compile().ainvoke({"prompt": prompt, "answer": ""})
    return result["answer"], result.get("tool_trace", [])
