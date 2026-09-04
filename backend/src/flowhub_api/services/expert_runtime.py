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
from flowhub_api.services.repo_mirror import repo_map_section, schedule_mirror_build

logger = logging.getLogger("flowhub_api")

# 首版 + 两次基于质量问题的完整修订。这个上限在质量和等待时间之间折中，且保证图不会无界回环。
MAX_QUALITY_ATTEMPTS = 3
QUALITY_POLICIES = {
    "fast": {"review": False, "max_attempts": 1},
    "balanced": {"review": True, "max_attempts": 2},
    "accurate": {"review": True, "max_attempts": 3},
}


def quality_policy(mode: str | None) -> dict[str, bool | int]:
    """质量档位：快模式单次生成，平衡模式最多修订一次，准确模式最多修订两次。"""
    return dict(QUALITY_POLICIES.get(mode or "balanced", QUALITY_POLICIES["balanced"]))


def evidence_review_prompt(question: str, evidence: str, answer: str) -> str:
    """让 Judge 只判定事实是否有来源支撑，避免“文风建议”触发无效循环。"""
    return (
        "你是事实核验器。只能使用下方证据核验回答；不要凭常识补全。"
        "逐项检查回答中的事实主张：没有证据、与证据矛盾、或把不确定性说成确定事实时，列入 issues。"
        "不要评价措辞或篇幅。仅输出 JSON："
        '{"pass":true|false,"issues":["无证据或矛盾的具体主张"]}，最多三项。\n\n'
        f"用户问题：{question}\n\n证据：\n{evidence[:12000]}\n\n回答：\n{answer[:12000]}"
    )


def should_refine_answer(attempt: int, validation_issues: list[str], max_attempts: int = MAX_QUALITY_ATTEMPTS) -> bool:
    """Return whether an answer should enter another bounded revision pass."""
    return bool(validation_issues) and attempt < max_attempts


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
    flowhub_context: str
    tool_trace: list
    attempt: int
    validation_issues: list[str]


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


async def flowhub_read_snapshot(session: AsyncSession, user: User, prompt: str, project_name: str | None = None) -> dict:
    """FlowHub 基础能力（LangGraph 运行时内置，与是否加载 Expert 无关）：
    按操作者权限读取可见的任务 / 工作项 / 项目目录，并按用户问题关键词命中详情。

    project_name：会话绑定的项目（名称）。传入时追加该项目绑定仓库的"地图层"上下文
    （目录概览 + README + 依赖清单，见 repo_mirror.repo_map_section），allow_clone=False
    保证聊天请求不阻塞在首次 clone 上（镜像由会话绑定时的后台任务预构建）。

    只读；业务写入仍走受治理路径。返回 {context, trace, pre_answer}。"""
    tasks = (await session.execute(select(TaskItem).where(TaskItem.assignee == user.name))).scalars().all()
    work_items = (await session.execute(select(WorkItem))).scalars().all()
    projects = (await session.execute(select(Project).order_by(Project.updated.desc()))).scalars().all()
    trace: list[dict] = []
    # === 核心：把"目录"（标题列表）带进 context，让模型能识别用户问的具体任务 ===
    task_titles = [f"#{t.id[:8]} {t.title}({t.status}/{t.node})" for t in tasks[:25]]
    wi_titles = [f"#{w.id[:8]} {w.title}({w.status}/{w.type})" for w in work_items[:25]]
    proj_names = [f"{p.name}({p.status})" for p in projects[:10]]
    context = (
        f"操作者 {user.name}。"
        f"我的任务({len(tasks)}，前 {len(task_titles)}): {'; '.join(task_titles) or '无'}。"
        f"工作项({len(work_items)}，前 {len(wi_titles)}): {'; '.join(wi_titles) or '无'}。"
        f"项目({len(projects)}，前 {len(proj_names)}): {'; '.join(proj_names) or '无'}。"
    )
    # === 关键词模糊匹配：把用户问的具体任务/工作项的详情带进上下文 ===
    keywords = _extract_keywords(prompt)
    hits_task = [t for t in tasks if any(k in (t.title or "") for k in keywords)][:5]
    hits_wi = [w for w in work_items if any(k in (w.title or "") for k in keywords)][:5]
    if hits_task or hits_wi:
        trace.append({"tool": "flowhub.search.by_keyword", "status": "succeeded",
                      "summary": f"按关键词 {keywords} 匹配 {len(hits_task)} 任务 / {len(hits_wi)} 工作项"})
        lines_d = []
        for t in hits_task:
            lines_d.append(f"[任务] {t.id}「{t.title}」状态={t.status} 节点={t.node} 负责人={t.assignee} 截止={t.due}")
        for w in hits_wi:
            lines_d.append(f"[工作项] {w.id}「{w.title}」状态={w.status} 类型={w.type} 进度={w.progress}")
        matched = "\n".join(lines_d)
        answer = f"按你的问题匹配到以下条目：\n{matched}"
    elif any(word in prompt for word in ("项目", "项目列表")):
        trace.append({"tool": "flowhub.project.list", "status": "succeeded", "summary": f"读取 {len(projects)} 个项目"})
        names = "、".join(f"{project.name}（{project.status}）" for project in projects[:10]) or "暂无项目"
        answer = f"当前项目：{names}。"
    elif any(word in prompt for word in ("问题", "issue", "故障")):
        issues = [item for item in work_items if item.type == "issue"]
        trace.append({"tool": "flowhub.work_item.list", "status": "succeeded", "summary": f"筛选到 {len(issues)} 条问题工作项"})
        names = "、".join(f"{item.id} {item.title}（{item.status}）" for item in issues[:10]) or "暂无问题工作项"
        answer = f"当前问题工作项：{names}。"
    elif any(word in prompt for word in ("任务", "待办", "工单")):
        trace.append({"tool": "flowhub.task.list", "status": "succeeded", "summary": f"读取 {len(tasks)} 条当前用户任务"})
        names = "、".join(f"{t.id} {t.title}（{t.status}）" for t in tasks[:15]) or "暂无任务"
        answer = f"你当前有 {len(tasks)} 条可见任务：{names}。系统共有 {len(work_items)} 个工作项。"
    elif any(word in prompt for word in ("流程", "状态")):
        trace.append({"tool": "flowhub.work_item.summary", "status": "succeeded", "summary": f"读取 {len(work_items)} 个工作项状态"})
        answer = f"FlowHub 当前有 {len(work_items)} 个工作项。"
    else:
        trace.append({"tool": "flowhub.context.directory", "status": "succeeded",
                      "summary": f"返回可见目录 {len(tasks)} 任务 / {len(work_items)} 工作项 / {len(projects)} 项目"})
        answer = f"FlowHub 可见目录：\n{context}"
    if project_name:
        repo_section = await repo_map_section(session, project_name, allow_clone=False)
        # 自愈重试：部分仓库镜像缺失/不可用时，后台再次触发构建（schedule_mirror_build 内部
        # 立即返回，不阻塞当前消息）。后端重启会丢失在途构建任务，靠这里在下一轮消息自愈重连。
        if not repo_section or "镜像构建中" in repo_section or "镜像不可用" in repo_section:
            await schedule_mirror_build(project_name)
        if repo_section:
            context = f"{context}\n{repo_section}"
            answer = f"{answer}\n\n{repo_section}"
            trace.append({"tool": "flowhub.repo.map", "status": "succeeded",
                          "summary": f"读取项目「{project_name}」绑定仓库的代码地图（目录/README/依赖清单）"})
        else:
            trace.append({"tool": "flowhub.repo.map", "status": "succeeded",
                          "summary": f"项目「{project_name}」暂无可用仓库上下文（未绑定或镜像构建中）"})
    return {"context": context, "trace": trace, "pre_answer": answer}


def build_graph(provider: LlmProvider, model: LlmProviderModel, system_prompt: str, flowhub_snapshot=None, emitter=None, quality_mode: str = "accurate"):
    """构建 LangGraph 运行图。

    flowhub_snapshot：async (prompt) -> {context, trace, pre_answer}，FlowHub 基础能力钩子。
    无论是否加载 Expert 都会注入（execute_run 恒传），使运行图具备按权限操作 FlowHub 的基础能力。
    emitter：async (event: "token"|"trace", payload: dict) -> None，可选；传入时节点内边执行边推送
    （token 逐段、trace 实时），事件仍由调用方照常落 ExpertRunEvent，emitter 只负责"发出去"。
    """
    policy = quality_policy(quality_mode)

    async def _emit(event: str, payload: dict) -> None:
        if emitter is None:
            return
        try:
            await emitter(event, payload)
        except Exception:  # noqa: BLE001 — 推送失败不影响运行
            logger.debug("expert run emitter 发送失败", exc_info=True)

    async def context_node(state: GraphState) -> dict:
        update: dict = {"prompt": state["prompt"]}
        if flowhub_snapshot is not None:
            try:
                snap = await flowhub_snapshot(state["prompt"])
                update["flowhub_context"] = f"{snap['context']}\n\n预查询结果：\n{snap['pre_answer']}".strip()
                update["tool_trace"] = snap.get("trace") or []
                for item in snap.get("trace") or []:
                    await _emit("trace", {"kind": "tool", **item})
            except Exception as exc:  # noqa: BLE001
                update["flowhub_context"] = ""
                update["tool_trace"] = [{"tool": "flowhub.context", "status": "failed", "summary": f"FlowHub 上下文获取失败：{exc}"}]
                await _emit("trace", {"kind": "tool", "tool": "flowhub.context", "status": "failed", "summary": f"FlowHub 上下文获取失败：{exc}"})
        return update

    async def approval_node(state: GraphState) -> dict:
        await _emit("trace", {"kind": "approval", "tool": "flowhub.native.write", "status": "approval_required", "summary": "受治理写入：等待人工审批后继续"})
        decision = interrupt({"tool": "flowhub.native.write", "risk": "write_commit", "scope": state["prompt"]})
        return {"approved": bool(decision)}
    async def model_node(state: GraphState) -> dict:
        # timeout 只约束"两次读到字节之间"的间隔而非总时长；子任务任务书+schema 的长生成实测 60-90s，
        # 15s 会在网关停顿时误杀 → 表现为"模型服务暂不可用"。放宽到 120s 并允许 2 次重试。
        llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=120, max_retries=2)
        history = (state.get("history") or "").strip()
        deliverable_rule = "产出约定：仅当本次回答是用户明确要求的完整交付物（文档/方案/用例等）时，在回答最后另起一行写「【交付文件】文档标题」声明交付（系统会据此生成可下载文档）；普通问答不要声明。"
        messages = [("system", f"{system_prompt}\n\n{deliverable_rule}\n事实约束：涉及 FlowHub 数据、任务状态、代码或项目结论时，只能依据提供的上下文；上下文没有依据时必须明确说明不确定，不得编造。")]
        if history:
            messages.append(("system", f"以下是会话历史（含已压缩的早期轮次）：\n{history}"))
        flowhub_context = (state.get("flowhub_context") or "").strip()
        if flowhub_context:
            messages.append(("system", "以下是 FlowHub 实时上下文（按操作者权限只读获取，可直接引用其中的任务/工作项/项目信息）：\n" + flowhub_context))
        issues = state.get("validation_issues") or []
        if issues:
            messages.append(("system", "上一版产出未通过质量校验。请基于原回答完整修正以下问题，不要解释修正过程：\n- " + "\n- ".join(issues)))
        messages.append(("human", state["prompt"]))
        if emitter is not None:
            chunks: list[str] = []
            stream = llm.astream(messages)
            try:
                async for chunk in stream:
                    token = str(chunk.content or "")
                    if token:
                        chunks.append(token)
                        await _emit("token", {"text": token})
            finally:
                close_stream = getattr(stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            output = "".join(chunks)
        else:
            response = await llm.ainvoke(messages)
            output = str(response.content)
        return {"output": output, "attempt": int(state.get("attempt", 0)) + 1}

    async def validate_node(state: GraphState) -> dict:
        """LLM-as-judge quality gate; bounded by route_after_validation to avoid loops."""
        if not policy["review"]:
            return {"validation_issues": []}
        output = (state.get("output") or "").strip()
        if not output:
            return {"validation_issues": ["回答为空，请输出可直接使用的完整结果。"]}
        llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=120, max_retries=1)
        review = await llm.ainvoke([("human", evidence_review_prompt(state["prompt"], state.get("flowhub_context", ""), output))])
        try:
            review_text = str(review.content).strip()
            if review_text.startswith("```"):
                review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            payload = json.loads(review_text)
            issues = [str(item) for item in (payload.get("issues") or []) if str(item).strip()][:3]
            return {"validation_issues": [] if payload.get("pass") and not issues else (issues or ["请核对回答是否完整准确地满足用户要求。"]) }
        except (json.JSONDecodeError, AttributeError):
            # 校验器不可用时不阻断原回答；运行事件仍保留主模型结果。
            return {"validation_issues": []}

    def route_after_context(state: GraphState) -> str:
        return "approval" if state.get("write_intent") else "model"

    def route_after_validation(state: GraphState) -> str:
        return "model" if should_refine_answer(int(state.get("attempt", 0)), state.get("validation_issues") or [], int(policy["max_attempts"])) else END

    graph = StateGraph(GraphState)
    graph.add_node("context", context_node)
    graph.add_node("approval", approval_node)
    graph.add_node("model", model_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "context")
    graph.add_conditional_edges("context", route_after_context, {"approval": "approval", "model": "model"})
    # 受治理写入：审批通过后继续调用模型生成产出（此前 approval → END 导致 write_intent 运行永远没有模型输出）
    graph.add_edge("approval", "model")
    graph.add_edge("model", "validate")
    graph.add_conditional_edges("validate", route_after_validation, {"model": "model", END: END})
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


async def execute_run(session: AsyncSession, run: ExpertRun, version: ExpertVersion, user: User, history: str = "", provider_model_id: str | None = None, emitter=None, project_name: str | None = None) -> None:
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
    if emitter is not None:
        await emitter("trace", {"kind": "trace", "tool": "加载 Expert Version", "status": "succeeded", "summary": f"版本 {version.version}"})
        await emitter("trace", {"kind": "trace", "tool": "加载权限与 Skill", "status": "succeeded", "summary": f"Skills: {', '.join(version.skills) or '无'}"})
    try:
        # FlowHub 基础能力：无论是否加载 Expert，运行图都具备按操作者权限操作 FlowHub 的只读能力
        snapshot = lambda prompt: flowhub_read_snapshot(session, user, prompt, project_name)  # noqa: E731
        async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
            graph = build_graph(provider, model, version.system_prompt, flowhub_snapshot=snapshot, emitter=emitter).compile(checkpointer=checkpointer)
            result = await graph.ainvoke({"run_id": run.id, "prompt": run.input, "write_intent": run.status == "interrupted", "history": history}, {"configurable": {"thread_id": run.trace_id}})
        for offset, item in enumerate(result.get("tool_trace") or [], start=25):
            await add_event(session, run.id, offset, "tool", item.get("status", "succeeded"), f"FlowHub · {item.get('tool', '')}", {"summary": item.get("summary", "")})
        if "__interrupt__" in result:
            expires = datetime.now(UTC) + timedelta(minutes=30)
            session.add(ExpertApproval(id=new_id("eap"), run_id=run.id, tool_name="flowhub.native.write", risk="write_commit", scope=run.input, status="pending", expires_at=expires.isoformat(timespec="seconds"), created_at=now_iso()))
            await add_event(session, run.id, 3, "approval", "pending", "等待 LangGraph interrupt 审批", {"risk": "write_commit", "scope": run.input})
            return
        run.output = str(result.get("output", ""))
        run.status = "succeeded"
        await add_event(session, run.id, 3, "model", "succeeded", "LangGraph 模型节点完成", {"output": run.output[:1000]})
        await _snapshot_parsed_for_task(session, run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Expert Run %s 模型调用失败", run.id)
        reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        run.status = "failed"
        run.error = f"模型服务暂不可用，请检查 Provider 连接或稍后重试（{reason}）"
        await add_event(session, run.id, 3, "model", "failed", "模型调用失败", {"error": reason})
        if emitter is not None:
            await emitter("trace", {"kind": "model", "tool": "模型调用", "status": "failed", "summary": run.error})
    finally:
        run.finished_at = now_iso()


async def _snapshot_parsed_for_task(session: AsyncSession, run: ExpertRun) -> None:
    """Run 成功且关联流程任务时，按节点 schema 预解析产出存 parsed 快照。

    快照 = {values, warnings}：前端 Run 卡片据此渲染字段预览（用户不必看 JSON 墙），
    采纳接口直接消费（幂等，重复采纳不再重复生成文档）。解析失败存空 values + warnings。"""
    if not run.task_id:
        return
    task = await session.get(TaskItem, run.task_id)
    if task is None:
        return
    # 解析任务所属项目绑定的激活模板 → 节点 schema（与 routes.tasks._resolve_task_template 同逻辑）
    from flowhub_api.models import GlobalTemplate, Project

    project = (await session.execute(select(Project).where(Project.name == task.project))).scalar_one_or_none()
    binding = next((b for b in project.template_bindings if b.status == "active"), None) if project else None
    tpl = await session.get(GlobalTemplate, binding.template_id) if binding else None
    if tpl is None:
        return
    from flowhub_api.services.workflow import WorkflowService

    cfg = await WorkflowService(session)._node_cfg_of(tpl, task.node_id) or {}
    schema = cfg.get("schema") or []
    if not schema:
        run.parsed = None
        return
    values, warnings = parse_schema_output(schema, run.output or "")
    run.parsed = {"values": values, "warnings": warnings}


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
        snapshot = lambda prompt: flowhub_read_snapshot(session, user, prompt)  # noqa: E731
        async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
            graph = build_graph(provider, model, version.system_prompt, flowhub_snapshot=snapshot).compile(checkpointer=checkpointer)
            result = await graph.ainvoke(Command(resume=True), {"configurable": {"thread_id": run.trace_id}})
    except Exception as exc:  # noqa: BLE001
        # 审批后的模型生成可能因 Provider 不可达失败：标记运行失败，任务由人工兜底
        logger.exception("Expert Run %s 审批后模型调用失败", run.id)
        run.status, run.error = "failed", f"模型服务暂不可用，请检查 Provider 连接或稍后重试（{type(exc).__name__}: {str(exc)[:200]}）"
        run.finished_at = now_iso()
        await add_event(session, run.id, 5, "model", "failed", "审批后模型调用失败", {"error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        return run
    # 审批后继续模型节点：有真实产出则记录（替代此前的固定文案），供节点表单回填
    output = str(result.get("output", "") or "") if isinstance(result, dict) else ""
    run.output = output or "受治理写入动作已在 LangGraph 恢复路径中执行一次。"
    run.status, run.finished_at = "succeeded", now_iso()
    await add_event(session, run.id, 5, "tool", "succeeded", "LangGraph 恢复完成", {"tool": approval.tool_name})
    return run


async def start_deployment_run(session: AsyncSession, deployment_id: str, prompt: str, user: User, *, write_intent: bool = False, task_id: str | None = None, context: str = "") -> ExpertRun:
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
        task_id=task_id, input=prompt, context=context, trace_id=new_id("trace"), started_at=now_iso(),
    )
    session.add(run)
    await session.flush()
    await execute_run(session, run, version, user)
    return run


async def schedule_deployment_run(session: AsyncSession, deployment_id: str, prompt: str, user: User, *, task_id: str | None = None, on_finished=None, context: str = "", replace_run_id: str | None = None) -> ExpertRun:
    """后台执行版 start_deployment_run：当前事务只登记 Run（running）即返回，LLM 执行放入事件循环后台任务。

    任务提交/生成不应被 30s+ 的模型调用阻塞（此前内联执行导致提交请求挂起数十秒）。
    后台任务用自己的独立 Session 执行并落库；on_finished(session, run) 在同一后台 Session
    内回调（如自动节点的采纳流转），与 Run 结果一起提交。调用方 commit 时 Run 行随之持久化。

    replace_run_id：覆盖式重跑——复用该 Run 行（重置状态/产出/上下文，清空旧事件与挂起审批，
    换新 trace_id 使 LangGraph 线程全新），而非新建记录；产出历史以最新一轮为准。
    """
    from flowhub_api.db.session import SessionFactory

    deployment = await session.get(ExpertDeployment, deployment_id)
    if deployment is None or deployment.status != "active":
        raise ValueError("Expert Deployment 不存在或未启用")
    version = await session.get(ExpertVersion, deployment.expert_version_id)
    if version is None:
        raise ValueError("Deployment 固定的 Expert Version 不存在")

    run: ExpertRun
    if replace_run_id is not None:
        run = await session.get(ExpertRun, replace_run_id)
        if run is None or (task_id is not None and run.task_id != task_id):
            raise ValueError("要覆盖的 Run 不存在或不属于该任务")
        # 覆盖重置：同一条记录变为新一轮执行；trace_id 换新，LangGraph checkpoint 线程不串旧状态
        run.expert_id = deployment.expert_id
        run.expert_version_id = version.id
        run.deployment_id = deployment.id
        run.requested_by = user.id
        run.status = "running"
        run.input = prompt
        run.context = context
        run.output = ""
        run.error = ""
        run.parsed = None
        run.trace_id = new_id("trace")
        run.started_at = now_iso()
        run.finished_at = ""
        for ev in (await session.execute(select(ExpertRunEvent).where(ExpertRunEvent.run_id == run.id))).scalars():
            await session.delete(ev)
        for ap in (await session.execute(select(ExpertApproval).where(ExpertApproval.run_id == run.id, ExpertApproval.status == "pending"))).scalars():
            await session.delete(ap)
        await session.flush()
    else:
        # started_at 为 ISO 秒级精度：同任务内连续创建的 Run 可能同秒，保证严格递增以稳定「最新置顶」排序
        started = now_iso()
        if task_id is not None:
            latest = (await session.execute(
                select(ExpertRun.started_at).where(ExpertRun.task_id == task_id).order_by(ExpertRun.started_at.desc()).limit(1)
            )).scalar()
            if latest and started <= latest:
                started = (datetime.fromisoformat(latest) + timedelta(seconds=1)).isoformat(timespec="seconds")
        run = ExpertRun(
            id=new_id("run"), expert_id=deployment.expert_id, expert_version_id=version.id,
            deployment_id=deployment.id, requested_by=user.id, status="running",
            task_id=task_id, input=prompt, context=context, trace_id=new_id("trace"), started_at=started,
        )
        session.add(run)
        await session.flush()

    version_id, user_id = version.id, user.id

    async def _bg():
        # 等待调用方事务提交使 Run 新状态可见（请求路径在 schedule 后立即 commit，窗口毫秒级；
        # 覆盖式重跑时 Run 行早已存在——必须等到 trace_id 变为本轮新值才能读，否则会用旧状态执行）。
        # 用标量查询轮询：session.get 会命中首轮缓存的 identity map 旧快照，永远看不到提交后的更新。
        async with SessionFactory() as bg:
            committed = False
            for _ in range(50):
                tid = (await bg.execute(
                    select(ExpertRun.trace_id).where(ExpertRun.id == run.id)
                )).scalar_one_or_none()
                if tid == run.trace_id:
                    committed = True
                    break
                await asyncio.sleep(0.1)
            if not committed:
                logging.warning("后台 Expert Run %s 未找到记录或状态未提交（调用方事务回滚？），跳过执行", run.id)
                return
            bg_run = await bg.get(ExpertRun, run.id)
            try:
                bg_version = await bg.get(ExpertVersion, version_id)
                bg_user = await bg.get(User, user_id)
                if bg_version is None or bg_user is None:
                    raise ValueError("Expert Version 或用户不存在")
                await execute_run(bg, bg_run, bg_version, bg_user)
                # 无论协助还是自动节点，Run 已落到终态后都不能继续显示为 Expert 待处理。
                # 自动节点随后仍可由 on_finished 使用 status 决定采纳或人工兜底。
                if bg_run.task_id:
                    bg_task = await bg.get(TaskItem, bg_run.task_id)
                    if bg_task is not None:
                        bg_task.expert_pending = False
                if on_finished is not None:
                    await on_finished(bg, bg_run)
            except Exception as exc:  # noqa: BLE001
                bg_run.status, bg_run.error = "failed", str(exc) or "后台执行失败"
                bg_run.finished_at = now_iso()
                if bg_run.task_id:
                    bg_task = await bg.get(TaskItem, bg_run.task_id)
                    if bg_task is not None:
                        bg_task.expert_pending = False
                if on_finished is not None:
                    try:
                        await on_finished(bg, bg_run)
                    except Exception:  # noqa: BLE001
                        pass
            await bg.commit()

    task_ref = asyncio.create_task(_bg())
    _BG_TASKS.add(task_ref)
    task_ref.add_done_callback(_BG_TASKS.discard)
    return run


_BG_TASKS: set[asyncio.Task] = set()  # 持强引用防后台任务被 GC


async def recover_interrupted_runs(session: AsyncSession) -> int:
    """将进程重启前遗留的内存后台 Run 收敛为失败，避免任务页永久轮询。

    Expert 节点执行器目前在 API 进程内以 asyncio task 运行；进程退出时无法恢复
    原协程，因此不能把旧记录继续显示为 running。自动节点同时恢复到人工可处理状态。
    """
    stale_runs = (await session.execute(
        select(ExpertRun).where(ExpertRun.status == "running")
    )).scalars().all()
    for run in stale_runs:
        run.status = "failed"
        run.error = "服务重启导致本次 Expert 执行中断，请重新生成"
        run.finished_at = now_iso()
        if run.task_id:
            task = await session.get(TaskItem, run.task_id)
            if task is not None:
                task.expert_pending = False
                if task.status == "pending_confirmation":
                    task.status = "assigned"
    if stale_runs:
        await session.commit()
        logger.warning("已回收 %d 条因服务重启中断的 Expert Run", len(stale_runs))
    return len(stale_runs)


# ---------- 节点表单 AI 填充：schema 输出契约 / 解析矫正 / 文档生成 ----------

def build_schema_output_instruction(schema: list[dict]) -> str:
    """把节点 FormField[] 转成模型可执行的严格 JSON 输出契约。"""
    if not schema:
        return ""
    lines = [
        "## 输出契约（严格遵守）",
        "基于以上任务书完成节点产出，只输出一个 JSON 对象，不要输出任何其他文字。",
        "不要在字段值中重复字段名、不要使用 JSON/Markdown 代码围栏；每个字段只放该字段本身的内容。字段如下：",
    ]
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
        elif ftype in ("textarea", "upload", "file"):
            kind = "文件产出" if ftype in ("upload", "file") else "多行文本"
            lines.append(
                f"- {key}（{f.get('label')}，{required}，{kind}）：输出纯 Markdown 正文；"
                "可使用标题、列表、表格和段落，但不要加代码围栏、字段名前缀或重复的总标题。"
            )
        elif ftype == "number":
            lines.append(f"- {key}（{f.get('label')}，{required}，数字）：{desc}")
        elif ftype == "date":
            lines.append(f"- {key}（{f.get('label')}，{required}，日期 YYYY-MM-DD）：{desc}")
        else:
            lines.append(f"- {key}（{f.get('label')}，{required}，单行纯文本）：{desc}")
    return "\n".join(lines)


def _parse_json_block(block: str):
    """解析单个候选块：标准 JSON 失败后回退 ast（兼容模型输出的单引号 Python 风格 dict）。"""
    import ast

    text = block.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        try:
            return ast.literal_eval(text)
        except Exception:  # noqa: BLE001
            return None


def _candidate_blocks(raw: str) -> list[str]:
    """候选 JSON 块：优先 ```json 围栏块，再兜底「首个 { 到最后一个 }」整段。"""
    blocks = [m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.S)]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        blocks.append(raw[start:end + 1])
    return blocks


_TEXT_KEYS = ("content", "text", "markdown", "body", "value")


def _normalize_markdown(text: str) -> str:
    """清理模型偶发包裹在字段值外层的 Markdown 代码围栏，保留正文语义。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = re.fullmatch(r"```(?:markdown|md|text)?\s*\n?(.*?)\n?```", normalized, re.S | re.I)
    if fenced:
        normalized = fenced.group(1).strip("\n")
    # 不逐行 rstrip：行尾两个空格是 Markdown 的硬换行语法，必须原样保留。
    return normalized


def _coerce_text(raw_value, depth: int = 0) -> str:
    """把模型输出的任意值拍平为可读文本：dict/list 不再 str() 灌 repr。

    优先取 content 类 key 的字符串；否则按「### key\\n值」小节拼接；
    嵌套过深时取最长字符串叶子兜底。"""
    if isinstance(raw_value, str):
        return raw_value.strip()
    if depth >= 3:
        return ""
    if isinstance(raw_value, dict):
        for k in _TEXT_KEYS:
            v = raw_value.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        sections: list[str] = []
        for k, v in raw_value.items():
            if isinstance(v, str) and v.strip():
                sections.append(f"### {k}\n{v.strip()}")
            elif isinstance(v, (dict, list)):
                nested = _coerce_text(v, depth + 1)
                if nested:
                    sections.append(f"### {k}\n{nested}")
        return "\n\n".join(sections)
    if isinstance(raw_value, list):
        parts = [_coerce_text(v, depth + 1) for v in raw_value]
        return "\n\n".join(p for p in parts if p)
    if raw_value is None:
        return ""
    return str(raw_value)


def _plain_text_fallback(schema: list[dict], text: str) -> dict:
    """非 JSON 产出的全文回填：首个必填 textarea 优先，其次任一 textarea/input；
    upload/file 字段同回全文（调用方转文档）。无可回填字段返回 {}。"""
    if not text:
        return {}
    targets = [f for f in schema if f.get("type") in ("textarea", "input", "upload", "file")]
    primary = next((f for f in targets if f.get("required") and f.get("type") == "textarea"), None)
    if primary is None:
        primary = next((f for f in targets if f.get("type") == "textarea"), None)
    if primary is None:
        primary = next((f for f in targets if f.get("required")), None)
    if primary is None:
        return {}
    values: dict = {}
    values[primary["key"]] = text
    for f in schema:
        if f.get("type") in ("upload", "file") and f["key"] not in values:
            values[f["key"]] = text
    return values


def parse_schema_output(schema: list[dict], raw: str) -> tuple[dict, list[str]]:
    """解析模型输出为表单值：容错提取 JSON（围栏块优先、单引号兼容），按字段类型矫正
    （选项约束/数值/数组），嵌套 dict/list 拍平为可读文本。
    upload/file 字段的字符串视为文档正文，由调用方转成文档引用。解析失败返回 ({}, warnings)。"""
    warnings: list[str] = []
    if not raw or not raw.strip():
        return {}, ["模型无输出"]
    schema_keys = {f.get("key", "") for f in schema}
    parsed = None
    best: tuple[int, object] = (-1, None)
    for block in _candidate_blocks(raw):
        candidate = _parse_json_block(block)
        if isinstance(candidate, list):
            candidate = candidate[0] if candidate and isinstance(candidate[0], dict) else None
        if not isinstance(candidate, dict):
            continue
        score = sum(1 for k in schema_keys if k and k in candidate)
        if score > best[0]:
            best = (score, candidate)
        if score == len(schema_keys):
            break
    parsed = best[1]
    if parsed is None:
        # 兜底：模型输出为纯文本/Markdown 评审报告（无任何 JSON 结构）时，
        # 把全文回填到首个必填长文本字段、upload 字段转文档，保证产出仍可采纳（由人工审核）
        fallback = _plain_text_fallback(schema, raw.strip())
        if fallback:
            return fallback, ["模型输出不是 JSON，已按全文回填，请人工核对字段内容"]
        return {}, ["模型输出无法解析为 JSON"]
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
            text = _coerce_text(raw_value)
            if ftype in ("textarea", "upload", "file"):
                text = _normalize_markdown(text)
            if text:
                values[key] = text
            elif f.get("required"):
                warnings.append(f"「{f.get('label', key)}」生成为空")
    return values, warnings


def build_normalize_prompt(schema: list[dict], raw: str) -> str:
    """AI 二次格式修正 prompt：把 Run 原始产出按节点表单契约重新归位与排版。

    硬约束「只调格式、不改内容」——修正器禁止增删改事实与语义；最终内容不变性
    由调用方的非文本字段程序校验兜底（见 normalize_run_output）。"""
    instruction = build_schema_output_instruction(schema)
    return (
        "## 任务：格式规范化\n"
        "你是格式规范化器。下面是一次任务产出与该节点的输出契约。请把产出重新整理为严格符合契约的 JSON：\n"
        "- 仅做格式调整：Markdown 结构（标题层级/列表/表格/断行）、去掉无关的代码围栏与寒暄杂讯、"
        "键名映射、值类型矫正、把内容按字段归位。\n"
        "- 禁止增删改任何事实与语义：不新增观点/数据/结论，不删减要点，不改写措辞。\n"
        "- 原 JSON 已含某字段时直接采用其内容做排版整理；缺字段时从产出正文中搬运对应内容，不得撰写新内容。\n"
        "- textarea、upload、file 字段的值必须是可直接渲染的纯 Markdown 正文；不要加 JSON/Markdown 代码围栏、字段名前缀或重复总标题。\n"
        "- input 字段保持单行纯文本，不加入 Markdown 标记。\n"
        "- 输出仍须严格遵守输出契约（只输出一个 JSON 对象）。\n\n"
        f"{instruction}\n\n## 原始产出\n{raw}"
    )


async def normalize_run_output(session: AsyncSession, run: ExpertRun, schema: list[dict]) -> tuple[dict, list[str]]:
    """AI 二次格式修正：把 Run 原始产出按节点 schema 契约做格式规范后重新解析。

    返回 (values, warnings)。模型调用或解析失败时抛异常，由调用方降级为原 parsed 快照采纳。
    结构化字段（select/radio/multiselect/number/date）修正前后值不一致时放弃该字段修正、保留原值
    （内容不变性程序兜底）；文本字段以修正结果为准（仅排版差异）。"""
    from flowhub_api.models import LlmProvider, LlmProviderModel

    if run.deployment_id is None:
        raise ValueError("Run 未关联 Deployment，无法定位格式修正模型")
    deployment = await session.get(ExpertDeployment, run.deployment_id)
    if deployment is None:
        raise ValueError("Run 关联的 Deployment 不存在")
    version = await session.get(ExpertVersion, deployment.expert_version_id)
    if version is None:
        raise ValueError("Deployment 固定的 Expert Version 不存在")
    model = await session.get(LlmProviderModel, version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not provider or provider.status != "healthy" or not provider.credential_configured:
        raise ValueError("格式修正模型不可用，请检查 Provider 配置")
    llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=120, max_retries=2)
    response = await llm.ainvoke([("human", build_normalize_prompt(schema, run.output or ""))])
    values, warnings = parse_schema_output(schema, str(response.content))
    if not values:
        raise ValueError(warnings[0] if warnings else "格式修正未解析出有效字段值")
    # 内容不变性校验：非自由文本字段（选项/数值/日期）修正前后必须一致，否则保留原值
    original = (run.parsed or {}).get("values") or {}
    changed_guarded = 0
    for f in schema:
        key, ftype = f.get("key", ""), f.get("type", "input")
        if ftype not in ("select", "radio", "multiselect", "number", "date"):
            continue
        if key in values and key in original and values[key] != original[key]:
            values[key] = original[key]
            changed_guarded += 1
    if changed_guarded:
        warnings.append(f"{changed_guarded} 个结构化字段修正前后值不一致，已保留原值")
    return values, warnings


def extract_split_proposals(raw: str) -> list[dict]:
    """从模型输出提取拆分子任务建议（JSON 的 split 数组；兼容顶层或嵌套）。"""
    if not raw:
        return []
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("split"), list):
        return [p for p in parsed["split"] if isinstance(p, dict) and str(p.get("title", "")).strip()]
    return []


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


def chat_file_entry(ref: dict) -> dict:
    """文档引用 → AiChat 消息 files 条目（前端下载/预览所需字段）。"""
    import mimetypes

    name = ref.get("name", "")
    return {
        "id": ref.get("id"), "name": name, "filename": name,
        "mimeType": mimetypes.guess_type(name)[0] or "text/markdown",
        "downloadUrl": f"/api/v1/documents/{ref.get('id')}/download",
    }


DELIVERABLE_MARK = "【交付文件】"

def extract_deliverable(output: str) -> tuple[str, str] | None:
    """提取模型显式声明的交付文件：回答中含「【交付文件】标题」行 → (标题, 正文)。无声明返回 None。

    普通问答（即使较长）不再自动落文档——只有模型按提示词约定声明交付时才生成文件。
    兼容模型把声明写在末尾以外位置的情况：取最后一次出现的声明行。"""
    lines = (output or "").strip().splitlines()
    decl_idx = max((i for i, l in enumerate(lines) if DELIVERABLE_MARK in l), default=-1)
    if decl_idx < 0:
        return None
    title = lines[decl_idx].strip().replace(DELIVERABLE_MARK, "").strip().strip(":：")[:80]
    if not title:
        return None
    # 正文剔除全部声明行（避免重复声明混入交付文档）
    body = "\n".join(l for l in lines if DELIVERABLE_MARK not in l).strip()
    return title, body


async def save_chat_output_document(session: AsyncSession, *, project_name: str | None, output: str, user: User, create_file: bool = False) -> dict | None:
    """在用户授权后，根据模型的交付声明调用文档工具并保存为项目文档。

    模型必须输出「【交付文件】标题」才能触发 create_document_from_text；不再根据关键词、
    回答长度或用户是否勾选来猜测内容，避免普通会话产生意外附件。"""
    if not create_file or not project_name or not (output or "").strip():
        return None
    declared = extract_deliverable(output)
    if not declared:
        return None
    title, body = declared
    ref = await create_document_from_text(
        session, wi_id="", project=project_name,
        name=f"{title}.md", content=body, uploader=user,
    )
    return chat_file_entry(ref)


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


async def run_native_flowhub_chat(session: AsyncSession, prompt: str, user: User, provider_model_id: str | None = None, history: str = "", on_token: Callable[[str], Awaitable[None]] | None = None, on_trace: Callable[[dict], Awaitable[None]] | None = None, project_name: str | None = None, quality_mode: str = "balanced") -> tuple[str, list[dict]]:
    """Default LangGraph path for a chat without an Expert Deployment.

    It exposes safe, read-only FlowHub capabilities. Business writes remain a
    dedicated Expert/Deployment operation so they can be versioned and approved.
    on_trace：可选 async (item) -> None，工具/模型 trace 产生时实时推送（而非结束后一次性返回）。
    """

    policy = quality_policy(quality_mode)

    async def _emit_trace(item: dict) -> None:
        if on_trace is None:
            return
        try:
            await on_trace(item)
        except Exception:  # noqa: BLE001
            logger.debug("native chat on_trace 推送失败", exc_info=True)

    class NativeState(TypedDict):
        prompt: str
        answer: str
        tool_trace: list[dict]
        flowhub_context: str
        pre_answer: str
        attempt: int
        validation_issues: list[str]
        can_refine: bool

    async def native_tools(state: dict) -> dict:
        # 首轮读一次快照；修订轮复用相同上下文，避免反复查询且确保评审针对同一份事实。
        trace = list(state.get("tool_trace") or [])
        context = state.get("flowhub_context", "")
        answer = state.get("pre_answer", "")
        text = state["prompt"]
        if not context:
            # FlowHub 基础能力（只读快照）与 Expert 运行图共用同一实现；project_name 追加仓库地图上下文
            snap = await flowhub_read_snapshot(session, user, text, project_name)
            trace = [{"kind": "tool", **item} for item in snap["trace"]]
            for item in trace:
                await _emit_trace(item)
            context = snap["context"]
            answer = snap["pre_answer"]
            if any(word in text for word in ("创建", "提交", "删除", "发布", "写入")):
                write_item = {"kind": "tool", "tool": "flowhub.write.request", "status": "approval_required", "summary": "默认对话不直接执行写入"}
                trace.append(write_item)
                await _emit_trace(write_item)
                answer = "默认 FlowHub 对话仅提供只读能力。请选择已发布 Expert Deployment 执行受治理写入操作，系统会在 LangGraph 审批节点中断等待确认。"
                return {"answer": answer, "tool_trace": trace, "flowhub_context": context, "pre_answer": answer, "can_refine": False}
        if provider_model_id:
            model = await session.get(LlmProviderModel, provider_model_id)
            provider = await session.get(LlmProvider, model.provider_id) if model else None
            if not model or not provider or provider.status != "healthy" or not provider.credential_configured:
                return {"answer": "所选 Provider / 模型不可用，请检查 Provider 凭据和健康状态。", "tool_trace": trace, "flowhub_context": context, "pre_answer": answer, "can_refine": False}
            try:
                llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=120, max_retries=2)
                # 把"目录 + 关键词命中详情 + 已得 answer"都喂给 LLM，让它能基于真实数据回答
                history_text = (history or "").strip()
                human_parts = []
                if history_text:
                    human_parts.append(f"会话历史（早期轮次可能已压缩）：\n{history_text}")
                human_parts.append(f"{context}\n\n已查询结果：\n{answer}\n\n用户问题：{text}")
                messages = [("system", "你是 FlowHub 默认助手。仅基于提供的 FlowHub 只读上下文回答；不可声称已执行创建、提交、删除或发布。涉及状态、人员、日期、代码或项目事实时必须能在上下文中找到依据；没有依据时明确说明不确定，不得编造。需要写操作时，提示用户选择已发布 Expert。回答简洁，给出任务/工作项 ID 便于用户定位。只有当用户明确要求生成文档/方案/用例等完整交付物时，才在回答最后另起一行写「【交付文件】文档标题」声明交付；普通问答不要声明。"), ("human", "\n\n".join(human_parts))]
                issues = state.get("validation_issues") or []
                if issues:
                    messages.insert(1, ("system", "上一版回答未通过质量校验。请完整修正以下问题；只输出修订后的答案，不要解释修订过程：\n- " + "\n- ".join(issues)))
                # 质量门可能要求修订；先保留完整候选，确认最终版后才推送 token，避免前端把多个版本拼接。
                response = await llm.ainvoke(messages)
                answer = str(response.content)
            except Exception as exc:
                logger.exception("native chat 模型调用失败（回退本地查询结果）")
                fail_item = {"kind": "model", "tool": "默认 Provider 模型", "status": "failed",
                             "summary": f"模型服务暂不可用（{type(exc).__name__}: {str(exc)[:200]}）；已回退到 FlowHub 本地查询结果"}
                trace.append(fail_item)
                await _emit_trace(fail_item)
                return {"answer": answer, "tool_trace": trace, "flowhub_context": context, "pre_answer": answer, "can_refine": False}
        return {"answer": answer, "tool_trace": trace, "flowhub_context": context, "pre_answer": state.get("pre_answer", answer), "attempt": int(state.get("attempt", 0)) + 1, "can_refine": bool(provider_model_id)}

    async def validate_native_answer(state: dict) -> dict:
        if not policy["review"]:
            return {"validation_issues": [], "tool_trace": state.get("tool_trace") or []}
        trace = list(state.get("tool_trace") or [])
        if not state.get("can_refine") or not provider_model_id:
            return {"validation_issues": [], "tool_trace": trace}
        model = await session.get(LlmProviderModel, provider_model_id)
        provider = await session.get(LlmProvider, model.provider_id) if model else None
        if not model or not provider or provider.status != "healthy" or not provider.credential_configured:
            return {"validation_issues": [], "tool_trace": trace}
        running = {"kind": "quality", "tool": "flowhub.answer.quality_check", "status": "running", "summary": f"正在校验第 {state.get('attempt', 1)} 版回答"}
        await _emit_trace(running)
        try:
            llm = ChatOpenAI(model=model.model, base_url=provider.base_url, api_key=decrypt_secret(provider.api_key), temperature=0, timeout=120, max_retries=1)
            review = await llm.ainvoke([("human", evidence_review_prompt(state["prompt"], state.get("flowhub_context", ""), state.get("answer", "")))])
            review_text = str(review.content).strip()
            if review_text.startswith("```"):
                review_text = review_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            payload = json.loads(review_text)
            issues = [str(item) for item in (payload.get("issues") or []) if str(item).strip()][:3]
            issues = [] if payload.get("pass") and not issues else (issues or ["请核对回答是否完整准确地满足用户要求。"])
        except Exception:  # 校验故障不阻断可用答案
            logger.debug("native chat quality check skipped", exc_info=True)
            issues = []
        status = "succeeded" if not issues else "needs_revision"
        summary = "质量校验通过" if not issues else f"发现 {len(issues)} 项可修订问题，将生成下一版"
        quality_item = {"kind": "quality", "tool": "flowhub.answer.quality_check", "status": status, "summary": summary}
        trace.append(quality_item)
        await _emit_trace(quality_item)
        return {"validation_issues": issues, "tool_trace": trace}

    def route_after_native_validation(state: dict) -> str:
        return "native_tools" if should_refine_answer(int(state.get("attempt", 0)), state.get("validation_issues") or [], int(policy["max_attempts"])) else END

    graph = StateGraph(NativeState)
    graph.add_node("native_tools", native_tools)
    graph.add_node("validate", validate_native_answer)
    graph.add_edge(START, "native_tools")
    graph.add_edge("native_tools", "validate")
    graph.add_conditional_edges("validate", route_after_native_validation, {"native_tools": "native_tools", END: END})
    result = await graph.compile().ainvoke({"prompt": prompt, "answer": "", "tool_trace": [], "attempt": 0, "validation_issues": []})
    if on_token and result.get("answer"):
        await on_token(result["answer"])
    return result["answer"], result.get("tool_trace", [])
