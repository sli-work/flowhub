"""LangGraph execution boundary for versioned Expert Deployments."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tarfile
import zipfile
from datetime import UTC, datetime, timedelta
from collections.abc import Awaitable, Callable
from io import BytesIO
from typing import Annotated, TypedDict
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import ExpertApproval, ExpertChatMessage, ExpertChatSession, ExpertDeployment, ExpertRun, ExpertRunEvent, ExpertSkill, ExpertVersion, LlmProvider, LlmProviderModel, Project, TaskItem, User, WorkItem
from flowhub_api.services.crypto import decrypt_secret
from flowhub_api.services.repo_mirror import create_repo_tool_bundle, repo_map_section, schedule_mirror_build

from flowhub_api.services.runtime_model import make_model
from flowhub_api.services.runtime_quality import content_hash, review_candidate

logger = logging.getLogger("flowhub_api")

# 首版 + 两次基于质量问题的完整修订。这个上限在质量和等待时间之间折中，且保证图不会无界回环。
MAX_QUALITY_ATTEMPTS = 3
MAX_JUDGE_RETRIES = 2
MAX_SKILL_CONTEXT_CHARS = 24_000
MAX_REPO_TOOL_CALLS = 6
MAX_REPO_TOOL_CONTEXT_CHARS = 24_000
QUALITY_POLICIES = {
    "fast": {"review": False, "max_attempts": 1},
    "balanced": {"review": True, "max_attempts": 2},
    "accurate": {"review": True, "max_attempts": 3},
}


class RepoToolLoopUnavailable(RuntimeError):
    """The configured model/provider cannot accept FlowHub's tool schema."""


async def run_repo_tool_loop(llm, messages: list, bundle, *, on_trace: Callable[[dict], Awaitable[None]] | Callable[[dict], None] | None = None,
                             max_calls: int = MAX_REPO_TOOL_CALLS) -> tuple[str, int]:
    """Run a small Pi-style tool loop with FlowHub's explicit safety budget.

    Tool selection remains model-driven, while invocation, errors and the
    maximum call count remain deterministic and auditable in the server.
    """
    if not getattr(bundle, "tools", None):
        raise RepoToolLoopUnavailable("当前项目没有可用的仓库工具")
    try:
        tool_llm = llm.bind_tools(bundle.tools)
    except Exception as exc:  # provider compatibility differs across OpenAI-compatible gateways
        raise RepoToolLoopUnavailable(str(exc)) from exc
    tool_by_name = {tool.name: tool for tool in bundle.tools}
    transcript = list(messages)
    used = 0
    result_chars = 0
    rounds = 0

    async def emit(item: dict) -> None:
        if on_trace is None:
            return
        value = on_trace(item)
        if hasattr(value, "__await__"):
            await value

    while True:
        if used >= max_calls or rounds >= 8 or result_chars >= MAX_REPO_TOOL_CONTEXT_CHARS:
            await emit({
                "kind": "tool",
                "tool": "flowhub.repo.tools",
                "status": "blocked",
                "summary": "仓库工具调用已达到安全上限，正在基于已有证据总结",
            })
            response = await llm.ainvoke(transcript + [("human", "工具预算已耗尽。不要再调用工具，仅基于已收集证据给出结论；证据不足时明确说明。")])
            return str(getattr(response, "content", "") or "仓库工具调用已达到安全上限；现有证据不足，请缩小问题范围。"), used
        rounds += 1
        response = await tool_llm.ainvoke(transcript)
        calls = list(getattr(response, "tool_calls", None) or [])
        if not calls:
            return str(getattr(response, "content", "") or ""), used
        if used >= max_calls:
            for call in calls:
                await emit({"kind": "tool", "tool": str(call.get("name") or "unknown"), "status": "blocked",
                            "summary": f"仓库工具调用已达到 {max_calls} 次上限，已终止工具循环"})
            return (str(getattr(response, "content", "") or "")
                    or "仓库工具调用已达到安全上限；请基于已收集的代码证据重新提问或缩小范围。"), used
        transcript.append(response)
        for call in calls:
            name = str(call.get("name") or "")
            call_id = str(call.get("id") or f"repo-tool-{used + 1}")
            args = call.get("args") or {}
            tool = tool_by_name.get(name)
            if used >= max_calls or result_chars >= MAX_REPO_TOOL_CONTEXT_CHARS:
                content = json.dumps({"error": f"仓库工具调用已达到 {max_calls} 次上限；请基于已有证据回答。"}, ensure_ascii=False)
                status = "blocked"
            elif tool is None:
                used += 1
                content = json.dumps({"error": f"不允许调用工具 {name}"}, ensure_ascii=False)
                status = "blocked"
            else:
                used += 1
                try:
                    content = await tool.ainvoke(args)
                    status = "succeeded"
                except Exception as exc:  # return an observable tool result so the model can recover
                    content = json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, ensure_ascii=False)
                    status = "failed"
            content = str(content)
            remaining = MAX_REPO_TOOL_CONTEXT_CHARS - result_chars
            if remaining <= 0:
                content = "【仓库工具结果总预算已用尽；请基于已有证据回答。】"
                status = "blocked"
            elif len(content) > remaining:
                content = content[:remaining] + "\n【仓库工具结果因总预算已截断】"
            result_chars += len(content)
            await emit({"kind": "tool", "tool": name, "status": status,
                        "summary": f"仓库只读工具 {name}{' 已完成' if status == 'succeeded' else ' 未执行'}"})
            transcript.append(ToolMessage(content=content, tool_call_id=call_id))


def quality_policy(mode: str | None) -> dict[str, bool | int]:
    """质量档位：快模式单次生成，平衡模式最多修订一次，准确模式最多修订两次。"""
    return dict(QUALITY_POLICIES.get(mode or "balanced", QUALITY_POLICIES["balanced"]))


def evidence_review_prompt(question: str, evidence: str, answer: str) -> str:
    """让 Judge 只判定事实是否有来源支撑，避免“文风建议”触发无效循环。"""
    return (
        "你是事实核验器。只能使用下方证据核验回答；不要凭常识补全。"
        "逐项检查回答中的事实主张：没有证据、与证据矛盾、或把不确定性说成确定事实时，列入 issues。"
        "若证据包含【代码证据】，每条代码结论还必须带 [repo@commit:file:L行号 symbol] 引用；"
        "若证据声明未绑定代码仓库，可基于任务、表单、文档和项目上下文分析，但必须说明结论不含代码实现验证。不要评价措辞或篇幅。仅输出 JSON："
        '{"pass":true|false,"issues":["无证据或矛盾的具体主张"]}，最多三项。\n\n'
        f"用户问题：{question}\n\n证据：\n{evidence}\n\n回答片段（仅检查本片段事实）：\n{answer}"
    )


def should_refine_answer(attempt: int, validation_issues: list[str], max_attempts: int = MAX_QUALITY_ATTEMPTS) -> bool:
    """Return whether an answer should enter another bounded revision pass."""
    return bool(validation_issues) and attempt < max_attempts


def should_retry_judge(status: str, judge_attempt: int) -> bool:
    """Judge transport/protocol failures retry the Judge, not the answer model."""
    return status == "unavailable" and judge_attempt < MAX_JUDGE_RETRIES


def code_evidence_issues(question: str, evidence: str, answer: str) -> list[str]:
    """Deterministic guard for code-analysis answers, including no-repository cases."""
    is_code_question = bool(re.search(r"代码|仓库|函数|调用|模块|实现|文件|bug|接口|class|function", question, flags=re.I))
    if not is_code_question:
        return []
    if "【关联代码仓库】未绑定" in evidence:
        if not re.search(r"未绑定.*代码仓库|没有.*代码仓库", answer):
            return ["当前项目未绑定代码仓库，回答必须说明结论未经过代码实现验证。"]
        return []
    if "【代码证据" in evidence:
        allowed: set[tuple[str, str, str, str]] = set()
        for block in re.finditer(r"【代码证据｜([^｜]+)｜commit ([0-9a-f]{7,64})[^】]*】\s*(.*?)(?=【代码证据｜|\Z)", evidence, flags=re.I | re.S):
            repo, commit, body = block.group(1), block.group(2).lower(), block.group(3)
            for location in re.finditer(r"([A-Za-z0-9_./-]+):L(\d+)", body):
                allowed.add((repo, commit, location.group(1), location.group(2)))
        citations = list(re.finditer(r"\[([^@\]]+)@([0-9a-f]{7,64}):([^:\]]+):L(\d+)(?:[^\]]*)\]", answer, flags=re.I))
        if not citations:
            return ["代码结论缺少 [repo@commit:file:L行号 symbol] 证据引用。"]
        if not allowed or any((item.group(1), item.group(2).lower(), item.group(3), item.group(4)) not in allowed for item in citations):
            return ["代码结论引用未命中本轮仓库工具或图谱证据，不能使用伪造引用。"]
    return []


def build_repair_instruction(previous_output: str, issues: list[str]) -> str:
    """Build a bounded, evidence-preserving repair instruction for a prior draft."""
    return (
        "上一版回答未通过质量校验。请在不编造事实的前提下直接修订它；"
        "保留正确内容，逐项解决下列问题。只输出修订后的完整答案，不要解释修订过程。\n\n"
        f"## 上一版回答\n{previous_output[:12000]}\n\n"
        "## 必须修正的问题\n- " + "\n- ".join(issues[:3])
    )


def parse_quality_review(raw: str) -> tuple[bool, list[str], str]:
    """Parse the Judge contract without treating protocol failures as a pass.

    Returns ``(passed, issues, status)`` where status is one of ``passed``,
    ``needs_revision`` or ``unavailable``.  A malformed Judge response is an
    observable unavailable check and triggers a bounded repair/recheck pass.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) == 2 else ""
        text = text.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False, ["质量校验响应无效，请重新核验并只输出 JSON 结果。"], "unavailable"
    if not isinstance(payload, dict) or not isinstance(payload.get("pass"), bool):
        return False, ["质量校验响应缺少布尔 pass 字段，请重新核验。"], "unavailable"
    raw_issues = payload.get("issues", [])
    if not isinstance(raw_issues, list) or not all(isinstance(item, str) for item in raw_issues):
        return False, ["质量校验响应的 issues 字段无效，请重新核验。"], "unavailable"
    issues = [item.strip() for item in raw_issues if item.strip()][:3]
    if payload["pass"] and not issues:
        return True, [], "passed"
    return False, issues or ["请核对回答是否完整、准确且有证据支撑。"], "needs_revision"


def schema_validation_issues(schema: list[dict], values: dict) -> list[str]:
    """Deterministically enforce the node output contract after parsing."""
    issues: list[str] = []
    for field in schema:
        key = str(field.get("key") or "")
        if not key:
            continue
        label = str(field.get("label") or key)
        value = values.get(key)
        missing = value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, tuple, set)) and not value)
        if field.get("required") and missing:
            issues.append(f"「{label}」缺少有效必填值")
            continue
        if missing:
            continue
        field_type = field.get("type", "input")
        option_values = {option.get("value") for option in field.get("options") or []}
        if field_type in ("select", "radio") and value not in option_values:
            issues.append(f"「{label}」不在可选项内")
        elif field_type == "multiselect":
            if not isinstance(value, list) or any(item not in option_values for item in value):
                issues.append(f"「{label}」包含无效可选项")
        elif field_type == "number" and not isinstance(value, (int, float)):
            issues.append(f"「{label}」不是有效数字")
        elif field_type == "date":
            if not isinstance(value, str):
                issues.append(f"「{label}」不是 YYYY-MM-DD 日期")
            else:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    issues.append(f"「{label}」不是 YYYY-MM-DD 日期")
    return issues


def extract_entity_ids(prompt: str) -> set[str]:
    """Extract complete FlowHub IDs without degrading them into title keywords."""
    return {item.upper() for item in re.findall(r"\b(?:REQ|ISS|ISSUE|CHG|T)-[A-Za-z0-9-]+\b", prompt, flags=re.I)}


def _skill_markdown_from_archive(data: bytes, package_type: str) -> str:
    """Read SKILL.md in memory; never extract untrusted archive paths to disk."""
    try:
        if package_type == "zip":
            with zipfile.ZipFile(BytesIO(data)) as archive:
                member = next((item for item in archive.infolist() if item.filename.rsplit("/", 1)[-1] == "SKILL.md"), None)
                if member is None or member.file_size > MAX_SKILL_CONTEXT_CHARS * 4:
                    return ""
                return archive.read(member).decode("utf-8", errors="replace")[:MAX_SKILL_CONTEXT_CHARS]
        with tarfile.open(fileobj=BytesIO(data), mode="r:*") as archive:
            member = next((item for item in archive.getmembers() if item.isfile() and item.name.rsplit("/", 1)[-1] == "SKILL.md"), None)
            if member is None or member.size > MAX_SKILL_CONTEXT_CHARS * 4:
                return ""
            source = archive.extractfile(member)
            return source.read().decode("utf-8", errors="replace")[:MAX_SKILL_CONTEXT_CHARS] if source else ""
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return ""


async def load_expert_skill_context(session: AsyncSession, skill_ids: list[str]) -> tuple[str, list[str]]:
    """Load published, bound Skill instructions with a bounded in-memory archive reader."""
    selected = [str(item) for item in skill_ids if str(item).strip()]
    if not selected:
        return "", []
    skills = (await session.execute(
        select(ExpertSkill).where(
            or_(ExpertSkill.id.in_(selected), ExpertSkill.slug.in_(selected)),
            ExpertSkill.deleted.is_(False), ExpertSkill.status == "published",
        )
    )).scalars().all()
    if not skills:
        return "", []
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings

    minio = get_minio()
    sections: list[str] = []
    loaded: list[str] = []
    for skill in skills:
        if minio is None or not skill.object_name:
            continue
        try:
            response = minio.get_object(get_settings().minio_bucket, skill.object_name)
            data = response.read(MAX_SKILL_CONTEXT_CHARS * 4 + 1)
            response.close()
            response.release_conn()
            text = _skill_markdown_from_archive(data, skill.package_type)
            if text:
                sections.append(f"## 已绑定 Skill：{skill.name} ({skill.version})\n{text}")
                loaded.append(skill.id)
        except Exception:  # noqa: BLE001 -- a missing optional Skill must not fail the Expert run
            logger.warning("无法加载 Expert Skill %s", skill.id, exc_info=True)
    return "\n\n".join(sections)[:MAX_SKILL_CONTEXT_CHARS], loaded


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
    history: str | list
    flowhub_context: str
    tool_trace: list
    attempt: int
    validation_issues: list[str]
    quality_status: str
    judge_attempt: int
    retry_judge: bool


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
    is_admin = user.name == "系统管理员" or any(role.id in ("system_admin", "organization_admin") for role in user.roles)
    tasks = (await session.execute(select(TaskItem) if is_admin else select(TaskItem).where(TaskItem.assignee == user.name))).scalars().all()
    if is_admin:
        work_items = (await session.execute(select(WorkItem))).scalars().all()
        projects = (await session.execute(select(Project).order_by(Project.updated.desc()))).scalars().all()
    else:
        task_wi_ids = {task.wi_id for task in tasks}
        work_items = (await session.execute(
            select(WorkItem).where(or_(WorkItem.id.in_(task_wi_ids), WorkItem.assignee == user.name, WorkItem.creator == user.name))
        )).scalars().all()
        project_names = {item.project for item in work_items}
        projects = (await session.execute(
            select(Project).where(Project.name.in_(project_names)).order_by(Project.updated.desc())
        )).scalars().all()
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
    # 完整业务 ID 是最强证据：必须在模糊标题匹配之前解析，避免编号被词切分后丢失。
    entity_ids = extract_entity_ids(prompt)
    exact_tasks = [task for task in tasks if task.id.upper() in entity_ids]
    exact_work_items = [item for item in work_items if item.id.upper() in entity_ids]
    if exact_tasks or exact_work_items:
        trace.append({"tool": "flowhub.search.by_id", "status": "succeeded",
                      "summary": f"按完整 ID 精确匹配 {len(exact_tasks)} 任务 / {len(exact_work_items)} 工作项"})
        lines = [
            *[f"[任务 {task.id}]「{task.title}」状态={task.status} 节点={task.node} 负责人={task.assignee} 截止={task.due}" for task in exact_tasks],
            *[f"[工作项 {item.id}]「{item.title}」状态={item.status} 类型={item.type} 进度={item.progress}" for item in exact_work_items],
        ]
        answer = "按完整 ID 匹配到以下条目：\n" + "\n".join(lines)
        context += "\n" + answer
        if project_name:
            repo_section = await repo_map_section(session, project_name, user=user, allow_clone=False, query=prompt)
            if not repo_section or "镜像构建中" in repo_section or "镜像不可用" in repo_section or "Graphify 图谱构建中" in repo_section:
                await schedule_mirror_build(project_name)
            if repo_section:
                context = f"{context}\n{repo_section}"
                answer = f"{answer}\n\n{repo_section}"
                trace.append({"tool": "flowhub.repo.evidence" if "【代码证据" in repo_section else "flowhub.repo.map", "status": "succeeded",
                              "summary": f"读取项目「{project_name}」绑定仓库的{'问题相关代码证据' if '【代码证据' in repo_section else '代码地图'}"})
            else:
                trace.append({"tool": "flowhub.repo.map", "status": "succeeded",
                              "summary": f"项目「{project_name}」暂无可用仓库上下文（未绑定或镜像构建中）"})
        return {"context": context, "trace": trace, "pre_answer": answer}

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
        repo_section = await repo_map_section(session, project_name, user=user, allow_clone=False, query=prompt)
        # 自愈重试：部分仓库镜像缺失/不可用时，后台再次触发构建（schedule_mirror_build 内部
        # 立即返回，不阻塞当前消息）。后端重启会丢失在途构建任务，靠这里在下一轮消息自愈重连。
        if not repo_section or "镜像构建中" in repo_section or "镜像不可用" in repo_section or "Graphify 图谱构建中" in repo_section:
            await schedule_mirror_build(project_name)
        if repo_section:
            context = f"{context}\n{repo_section}"
            answer = f"{answer}\n\n{repo_section}"
            trace.append({"tool": "flowhub.repo.evidence" if "【代码证据" in repo_section else "flowhub.repo.map", "status": "succeeded",
                          "summary": f"读取项目「{project_name}」绑定仓库的{'问题相关代码证据' if '【代码证据' in repo_section else '代码地图'}"})
        else:
            trace.append({"tool": "flowhub.repo.map", "status": "succeeded",
                          "summary": f"项目「{project_name}」暂无可用仓库上下文（未绑定或镜像构建中）"})
    return {"context": context, "trace": trace, "pre_answer": answer}


def build_graph(provider: LlmProvider, model: LlmProviderModel, system_prompt: str, flowhub_snapshot=None, emitter=None,
                quality_mode: str = "accurate", repo_tools_factory=None, output_schema=None):
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
        await _emit("trace", {"kind": "approval", "tool": "flowhub.native.write", "status": "approval_required", "summary": "等待人工审批后继续生成"})
        decision = interrupt({"tool": "flowhub.native.write", "risk": "write_commit", "scope": state["prompt"]})
        return {"approved": decision is True}
    async def model_node(state: GraphState) -> dict:
        # timeout 只约束"两次读到字节之间"的间隔而非总时长；子任务任务书+schema 的长生成实测 60-90s，
        # 15s 会在网关停顿时误杀 → 表现为"模型服务暂不可用"。放宽到 120s 并允许 2 次重试。
        llm = make_model(ChatOpenAI, model, provider, decrypt_secret(provider.api_key), retries=2)
        history = state.get("history") or []
        attempt_id = int(state.get("attempt", 0)) + 1
        await _emit("draft_start", {"attemptId": attempt_id})
        deliverable_rule = "产出约定：仅当本次回答是用户明确要求的完整交付物（文档/方案/用例等）时，在回答最后另起一行写「【交付文件】文档标题」声明交付（系统会据此生成可下载文档）；普通问答不要声明。"
        if output_schema:
            deliverable_rule = build_schema_output_instruction(output_schema)
        messages = [("system", f"{system_prompt}\n\n{deliverable_rule}\n事实约束：涉及 FlowHub 数据、任务状态、代码或项目结论时，只能依据提供的上下文；上下文没有依据时必须明确说明不确定，不得编造。代码结论必须紧随使用 [repo@commit:file:L行号 symbol] 格式的【代码证据】引用；未绑定代码仓库时，改为基于任务、表单、文档与项目上下文分析，并明确结论未经过实现验证。")]

        flowhub_context = (state.get("flowhub_context") or "").strip()
        if flowhub_context:
            messages.append(("system", "以下是 FlowHub 实时上下文（按操作者权限只读获取，可直接引用其中的任务/工作项/项目信息）：\n" + flowhub_context))
        if repo_tools_factory is not None:
            messages.append(("system", "代码分析顺序必须是：先使用系统注入的 Graphify 预分析证据；仅在证据不足时依次使用 repo_find_symbol/repo_trace_symbol、repo_read_file、定向 repo_search，最后才可对已知目录使用 repo_list_files。不得从仓库根目录泛搜，也不得猜测源码。工具结果是唯一可用于代码结论的补充证据。"))
        issues = state.get("validation_issues") or []
        if issues:
            messages.append(("system", build_repair_instruction(state.get("output", ""), issues)))
        messages.append(("human", state["prompt"]))
        if history:
            from flowhub_api.services.context_budget import fit_history_messages
            history_rows = history if isinstance(history, list) else [("human", "会话历史（仅作背景）：\n" + history)]
            fitted = await fit_history_messages(history_rows, messages, llm)
            messages = messages[:-1] + fitted + messages[-1:]
        trace = list(state.get("tool_trace") or [])
        # Quality revisions must reuse the first pass evidence. Re-running the
        # tool loop here multiplied calls by the number of quality attempts.
        if repo_tools_factory is not None and int(state.get("attempt", 0)) == 0:
            try:
                bundle = await repo_tools_factory(state["prompt"])
                for item in bundle.traces:
                    await _emit("trace", {"kind": "tool", **item})
                if bundle.tools:
                    if bundle.evidence_context:
                        messages.append(("system", "以下是已完成的 Graphify 预分析；先基于它回答或决定最小的补充读取：\n"
                                         + "\n\n".join(bundle.evidence_context)[:MAX_REPO_TOOL_CONTEXT_CHARS]))
                    output, _ = await run_repo_tool_loop(
                        llm, messages, bundle,
                        on_trace=lambda item: _emit("trace", item),
                    )
                    trace.extend({"tool": item["tool"], "status": item["status"], "summary": item["summary"]}
                                 for item in bundle.traces)
                    tool_evidence = "\n\n".join(bundle.evidence_context)[:MAX_REPO_TOOL_CONTEXT_CHARS]
                    if emitter is not None and output:
                        await _emit("token", {"text": output, "attemptId": attempt_id})
                    return {"output": output, "tool_trace": trace,
                            "flowhub_context": f"{state.get('flowhub_context', '')}\n\n{tool_evidence}".strip(),
                            "attempt": int(state.get("attempt", 0)) + 1}
            except RepoToolLoopUnavailable as exc:
                trace.append({"tool": "flowhub.repo.tools", "status": "unavailable",
                              "summary": f"模型不支持仓库工具调用，已回退静态代码上下文：{str(exc)[:160]}"})
                await _emit("trace", {"kind": "tool", **trace[-1]})
            except Exception as exc:  # a tool loop must not take down ordinary Expert answers
                trace.append({"tool": "flowhub.repo.tools", "status": "failed",
                              "summary": f"仓库工具循环失败，已回退静态代码上下文：{type(exc).__name__}"})
                await _emit("trace", {"kind": "tool", **trace[-1]})
        if emitter is not None:
            chunks: list[str] = []
            stream = llm.astream(messages)
            try:
                async for chunk in stream:
                    token = str(chunk.content or "")
                    if token:
                        chunks.append(token)
                        await _emit("token", {"text": token, "attemptId": attempt_id})
            finally:
                close_stream = getattr(stream, "aclose", None)
                if close_stream is not None:
                    await close_stream()
            output = "".join(chunks)
        else:
            response = await llm.ainvoke(messages)
            output = str(response.content)
        return {"output": output, "tool_trace": trace, "attempt": int(state.get("attempt", 0)) + 1}

    async def validate_node(state: GraphState) -> dict:
        output = str(state.get("output") or "")
        # Node output is a machine-readable form contract. It may contain the
        # word "代码" in the task brief without being a code-analysis answer,
        # so schema validation is the deterministic gate for this path.
        issues = [] if output_schema else code_evidence_issues(
            state["prompt"], state.get("flowhub_context", ""), output,
        )
        if output_schema:
            values, warnings = parse_schema_output(output_schema, output)
            issues += schema_validation_issues(output_schema, values)
            if any("不是 JSON" in w or "无法解析" in w for w in warnings):
                issues.append("模型未按 JSON 输出契约生成")
        llm = make_model(ChatOpenAI, model, provider, decrypt_secret(provider.api_key), retries=0)
        async def invoke(question, evidence, answer):
            response = await llm.ainvoke([("human", evidence_review_prompt(question, evidence, answer))])
            return str(response.content)
        await _emit("trace", {"kind": "quality", "tool": "flowhub.answer.quality_check", "status": "running", "summary": "正在校验当前草稿"})
        result = await review_candidate(question=state["prompt"], evidence=state.get("flowhub_context", ""),
            answer=output, policy=policy, attempt=int(state.get("attempt", 0)),
            deterministic_issues=issues, invoke_review=invoke, parse_review=parse_quality_review)
        await _emit("trace", {"kind": "quality", "tool": "flowhub.answer.quality_check", "status": result["quality_status"], "summary": "；".join(result["validation_issues"]) or result["quality_status"]})
        return result

    def route_after_context(state: GraphState) -> str:
        return "approval" if state.get("write_intent") else "model"

    def route_after_validation(state: GraphState) -> str:
        if state.get("quality_status") != "needs_revision":
            return END
        return "model" if should_refine_answer(int(state.get("attempt", 0)), state.get("validation_issues") or [], int(policy["max_attempts"])) else END

    graph = StateGraph(GraphState)
    graph.add_node("context", context_node)
    graph.add_node("approval", approval_node)
    graph.add_node("model", model_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "context")
    graph.add_conditional_edges("context", route_after_context, {"approval": "approval", "model": "model"})
    # 受治理写入：审批通过后继续调用模型生成产出（此前 approval → END 导致 write_intent 运行永远没有模型输出）
    graph.add_conditional_edges("approval", lambda state: "model" if state.get("approved") else END, {"model": "model", END: END})
    graph.add_edge("model", "validate")
    graph.add_conditional_edges("validate", route_after_validation, {"model": "model", "validate": "validate", END: END})
    return graph


async def add_event(session: AsyncSession, run_id: str, sequence: int, kind: str, status: str, title: str, payload: dict, duration_ms: int = 0) -> None:
    await session.flush()
    sequence = int((await session.execute(select(func.max(ExpertRunEvent.sequence)).where(ExpertRunEvent.run_id == run_id))).scalar() or 0) + 1
    session.add(ExpertRunEvent(id=new_id("ere"), run_id=run_id, sequence=sequence, kind=kind, status=status, title=title, payload=payload, duration_ms=duration_ms, created_at=now_iso()))


async def validate_version(session: AsyncSession, version: ExpertVersion) -> list[str]:
    blockers: list[str] = []
    if not version.system_prompt.strip():
        blockers.append("请填写 System Prompt")
    model = await session.get(LlmProviderModel, version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not model.enabled or not provider or provider.status != "healthy" or not provider.credential_configured:
        blockers.append("请选择健康且已配置凭据的 Provider 与模型")
    if version.knowledge_base_ids:
        blockers.append("知识库检索与权限索引尚未接入，含知识库绑定的 Expert 暂不能发布或运行")
    if version.tool_policies:
        blockers.append("自定义工具策略运行时尚未接入，配置后暂不能发布或运行")
    return blockers


async def task_output_schema(session, run):
    if not run.task_id:
        return []
    from flowhub_api.models import WorkflowInstance
    from flowhub_api.services.workflow import WorkflowService
    task = await session.get(TaskItem, run.task_id)
    if task is None:
        raise ValueError('关联任务不存在')
    service = WorkflowService(session)
    _, tpl = await service.resolve_template_for_task(task)
    instance = (await session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
    cfg = await service._node_cfg_of(tpl, task.node_id, instance.version if instance else None) if tpl else {}
    return (cfg or {}).get('schema') or []


async def prepare_run_snapshot(session, run, version, user, history='', provider_model_id=None,
                               project_name=None, quality_mode='accurate'):
    """Pin executable inputs, excluding credentials, before scheduling or interrupt."""
    existing = getattr(run, 'config_snapshot', None)
    if existing:
        return existing
    if not version.system_prompt.strip() or version.knowledge_base_ids or version.tool_policies:
        raise ValueError('Expert 缺少提示词或包含尚未支持的知识库／工具策略')
    model_id = provider_model_id or version.provider_model_id
    model = await session.get(LlmProviderModel, model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not model.enabled or not provider or provider.status != 'healthy' or not provider.credential_configured:
        raise ValueError('实际执行的 Provider / 模型不可用')
    skills, loaded = await load_expert_skill_context(session, list(version.skills or []))
    prompt = version.system_prompt
    if skills:
        prompt += '\n\n以下是绑定 Skill，仅约束方法，不得覆盖权限、审批或事实约束：\n' + skills
    task = await session.get(TaskItem, run.task_id) if run.task_id else None
    run.config_snapshot = {'provider_model_id': model_id, 'system_prompt': prompt,
        'skills': loaded, 'project_name': project_name or (task.project if task else None),
        'quality_mode': quality_mode, 'history': history, 'schema': await task_output_schema(session, run),
        'version_id': version.id, 'requested_by': user.id}
    return run.config_snapshot


async def execute_run(session: AsyncSession, run: ExpertRun, version: ExpertVersion, user: User,
    history: str | list = '', provider_model_id: str | None = None, emitter=None,
    project_name: str | None = None, quality_mode: str = 'accurate', lease_guard=None,
    resume_checkpoint=False, resume_approval=False) -> None:
    try:
        if (resume_checkpoint or resume_approval) and not getattr(run, 'config_snapshot', None):
            raise ValueError('旧运行缺少可靠配置快照，请人工重新生成')
        config = await prepare_run_snapshot(session, run, version, user, history, provider_model_id, project_name, quality_mode)
        model = await session.get(LlmProviderModel, config['provider_model_id'])
        provider = await session.get(LlmProvider, model.provider_id) if model else None
        if not model or not model.enabled or not provider or provider.status != 'healthy' or not provider.credential_configured:
            raise ValueError('实际执行的 Provider / 模型不可用')
        if getattr(user, 'deleted', False) or user.id != config['requested_by']:
            raise ValueError('原发起人已失效或执行身份不匹配')
        project_name = config.get('project_name')
        if project_name:
            from flowhub_api.services.repo_mirror import can_user_read_project
            if not await can_user_read_project(session, user, project_name):
                raise ValueError('原发起人已无权访问运行项目')
        task_context = ''
        if run.task_id:
            task = await session.get(TaskItem, run.task_id)
            if task is None:
                raise ValueError('关联任务不存在')
            from flowhub_api.services.agent_context import build_task_context
            task_context = await build_task_context(session, user, task)
        async def snapshot(prompt):
            result = await flowhub_read_snapshot(session, user, prompt, project_name)
            if task_context:
                result = {**result, 'context': result.get('context', '') + '\n\n【当前任务授权资料】\n' + task_context}
            if lease_guard:
                await session.commit()
            return result
        async def repo_tools(prompt):
            bundle = await create_repo_tool_bundle(session, project_name, user=user, query=prompt, allow_clone=False)
            if lease_guard:
                await session.commit()
            return bundle
        if lease_guard:
            await lease_guard()
            await session.commit()
        async with asyncio.timeout(900):
            async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as checkpointer:
                graph = build_graph(provider, model, config['system_prompt'], flowhub_snapshot=snapshot,
                    emitter=emitter, quality_mode=config['quality_mode'], output_schema=config.get('schema'),
                    repo_tools_factory=repo_tools if project_name else None).compile(checkpointer=checkpointer)
                graph_config = {'configurable': {'thread_id': run.trace_id}}
                graph_input = {'run_id': run.id, 'prompt': run.input, 'write_intent': run.status == 'interrupted', 'history': config.get('history', '')}
                if resume_approval:
                    graph_input = Command(resume=True)
                elif resume_checkpoint:
                    saved = await graph.aget_state(graph_config)
                    if saved.values:
                        graph_input = None
                result = await graph.ainvoke(graph_input, graph_config)
        if lease_guard:
            await lease_guard()
        if '__interrupt__' in result:
            run.status, run.finished_at = 'interrupted', ''
            existing = (await session.execute(select(ExpertApproval).where(ExpertApproval.run_id == run.id))).scalar_one_or_none()
            if existing is None:
                session.add(ExpertApproval(id=new_id('eap'), run_id=run.id, tool_name='flowhub.generation.resume',
                    risk='generation', scope=run.input, status='pending', expires_at=(datetime.now(UTC) + timedelta(minutes=30)).isoformat(timespec='seconds'), created_at=now_iso()))
            await add_event(session, run.id, 1, 'approval', 'pending', '等待审批后继续生成', {})
            return
        run.output = str(result.get('output') or '')
        quality = str(result.get('quality_status') or 'needs_human_review')
        run.quality_result = {'status': quality, 'issues': list(result.get('validation_issues') or []), 'contentHash': content_hash(run.output)}
        run.status = 'succeeded' if run.output else 'failed'
        if not run.output:
            run.error = '模型未生成有效内容'
        await _snapshot_parsed_for_task(session, run)
        run.parsed = {**(run.parsed or {}), 'qualityStatus': quality,
            'qualityIssues': run.quality_result['issues'], 'contentHash': run.quality_result['contentHash']}
        for item in result.get('tool_trace') or []:
            await add_event(session, run.id, 1, 'tool', item.get('status', 'succeeded'), item.get('tool', 'tool'), {'summary': item.get('summary', '')})
        await add_event(session, run.id, 1, 'quality', quality, '产出校验完成', run.quality_result)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if lease_guard:
            await lease_guard()  # Lost owners must not persist even a failure.
        logger.exception('Expert Run %s 执行失败', run.id)
        run.status, run.error = 'failed', '运行未完成，请检查模型配置、上下文预算或重新生成。' if not isinstance(exc, ValueError) else str(exc)
        await add_event(session, run.id, 1, 'model', 'failed', '执行失败', {'errorType': type(exc).__name__})
        if emitter:
            await emitter('trace', {'kind': 'model', 'tool': '模型调用', 'status': 'failed', 'summary': run.error})
    finally:
        if run.status != 'interrupted':
            run.finished_at = now_iso()


async def _snapshot_parsed_for_task(session: AsyncSession, run: ExpertRun) -> None:
    schema = (getattr(run, 'config_snapshot', None) or {}).get('schema')
    if schema is None:
        schema = await task_output_schema(session, run)
    values, warnings = parse_schema_output(schema, run.output or '') if schema else ({}, [])
    issues = schema_validation_issues(schema, values)
    if any('不是 JSON' in w or '无法解析' in w for w in warnings):
        issues.append('模型未按 JSON 输出契约生成，不能自动采纳')
    run.parsed = {'values': values, 'warnings': warnings, 'valid': not issues, 'validationIssues': issues}


async def resume_approved_run(session: AsyncSession, approval: ExpertApproval, user: User) -> ExpertRun:
    run = await session.get(ExpertRun, approval.run_id)
    if run is None:
        raise ValueError('关联 Run 不存在')
    version = await session.get(ExpertVersion, run.expert_version_id)
    requester = await session.get(User, run.requested_by)
    if version is None or requester is None:
        raise ValueError('关联版本或原发起人不存在')
    await add_event(session, run.id, 1, 'approval', 'succeeded', '审批通过，继续生成', {'approved_by': user.id})
    run.status = 'running'
    await execute_run(session, run, version, requester, resume_approval=True)
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


async def schedule_deployment_run(session: AsyncSession, deployment_id: str, prompt: str, user: User, *, task_id: str | None = None, completion: dict | None = None, context: str = "", replace_run_id: str | None = None) -> ExpertRun:
    """Enqueue in the caller's transaction; rollback never starts a model call."""
    from flowhub_api.models.expert import ExpertJob

    deployment = await session.get(ExpertDeployment, deployment_id)
    if deployment is None or deployment.status != "active":
        raise ValueError("Expert Deployment 不存在或未启用")
    version = await session.get(ExpertVersion, deployment.expert_version_id)
    if version is None or user is None:
        raise ValueError("Deployment 固定版本或发起人不存在")
    if replace_run_id:
        run = (await session.execute(select(ExpertRun).where(ExpertRun.id == replace_run_id).with_for_update())).scalar_one_or_none()
        if run is None or (task_id is not None and run.task_id != task_id):
            raise ValueError("要覆盖的 Run 不存在或不属于该任务")
        if run.status in {"queued", "running", "interrupted"}:
            raise ValueError("运行正在排队、执行或等待审批，不能覆盖")
        run.execution_generation = (run.execution_generation or 1) + 1
        run.expert_id, run.expert_version_id = deployment.expert_id, version.id
        run.deployment_id, run.requested_by = deployment.id, user.id
        run.status, run.input, run.context = "queued", prompt, context
        run.output, run.error, run.finished_at = "", "", ""
        run.parsed, run.config_snapshot, run.quality_result = None, {}, {}
        run.trace_id, run.started_at = new_id("trace"), now_iso()
        for ev in (await session.execute(select(ExpertRunEvent).where(ExpertRunEvent.run_id == run.id))).scalars():
            await session.delete(ev)
        for approval in (await session.execute(select(ExpertApproval).where(ExpertApproval.run_id == run.id))).scalars():
            await session.delete(approval)
    else:
        run = ExpertRun(id=new_id("run"), expert_id=deployment.expert_id, expert_version_id=version.id,
                        deployment_id=deployment.id, requested_by=user.id, status="queued", task_id=task_id,
                        input=prompt, context=context, trace_id=new_id("trace"), started_at=now_iso(), execution_generation=1)
        session.add(run)
    await session.flush()
    await prepare_run_snapshot(session, run, version, user)
    session.add(ExpertJob(id=new_id("ejb"), run_id=run.id, generation=run.execution_generation,
                          completion=dict(completion or {}), status="queued", created_at=now_iso()))
    await session.flush()
    return run


async def recover_interrupted_runs(session: AsyncSession) -> int:
    """Only legacy runs without durable jobs need manual recovery, never live jobs."""
    from flowhub_api.models.expert import ExpertJob
    stale = (await session.execute(select(ExpertRun).where(
        ExpertRun.status == "running", ~select(ExpertJob.id).where(ExpertJob.run_id == ExpertRun.id).exists(),
    ).with_for_update(skip_locked=True))).scalars().all()
    # Do not touch live session runs: these belong to other API processes.
    legacy = [run for run in stale if run.task_id and not run.config_snapshot]
    for run in legacy:
        run.status, run.error, run.finished_at = "failed", "历史运行缺少可靠配置快照，请人工重新生成", now_iso()
        task = await session.get(TaskItem, run.task_id)
        if task:
            task.expert_pending = False
            if task.status == "pending_confirmation":
                task.status = "assigned"
    await session.commit()
    return len(legacy)


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
            if f.get("required") and not picked:
                warnings.append(f"「{f.get('label', key)}」缺少有效可选项")
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
        if ftype == "date" and key in values:
            try:
                datetime.strptime(str(values[key]), "%Y-%m-%d")
            except ValueError:
                warnings.append(f"「{f.get('label', key)}」不是 YYYY-MM-DD 日期，已留空")
                values.pop(key, None)
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
    version = await session.get(ExpertVersion, run.expert_version_id)
    if version is None:
        raise ValueError("Run 固定的 Expert Version 不存在")
    config = getattr(run, "config_snapshot", None) or {}
    model = await session.get(LlmProviderModel, config.get("provider_model_id") or version.provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not model.enabled or not provider or provider.status != "healthy" or not provider.credential_configured:
        raise ValueError("格式修正模型不可用，请检查 Provider 配置")
    llm = make_model(ChatOpenAI, model, provider, decrypt_secret(provider.api_key), retries=2)
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
    requester = await session.get(User, run.requested_by)
    if requester is None:
        raise ValueError("原发起人不存在")
    evidence = await flowhub_read_snapshot(session, requester, run.input, config.get("project_name"))
    evidence_text = evidence.get("context", "")
    if run.task_id:
        from flowhub_api.services.agent_context import build_task_context
        task = await session.get(TaskItem, run.task_id)
        if task:
            evidence_text += "\n" + await build_task_context(session, requester, task)
    candidate = json.dumps(values, ensure_ascii=False)
    async def invoke(question, context, answer):
        result = await llm.ainvoke([("human", evidence_review_prompt(question, context, answer))])
        return str(result.content)
    review = await review_candidate(question=run.input, evidence=evidence_text, answer=candidate,
        policy=quality_policy("accurate"), attempt=3,
        deterministic_issues=schema_validation_issues(schema, values),
        invoke_review=invoke, parse_review=parse_quality_review)
    if review["quality_status"] != "passed":
        raise ValueError("格式修正后的内容未通过重新校验：" + "；".join(review["validation_issues"]))
    run.quality_result = {"status": "passed", "issues": [], "contentHash": content_hash(candidate)}
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


async def create_document_from_text(session: AsyncSession, *, wi_id: str, project: str, name: str, content: str, uploader: User, idempotency_key: str | None = None) -> dict:
    """把 AI 生成的文本产出落库为工作项文档（MinIO 可用时写对象），返回表单引用 {id, name}。"""
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings
    from flowhub_api.models import DocItem

    from hashlib import sha256

    doc_id = "d" + sha256(f"{idempotency_key}:{content}".encode()).hexdigest()[:24] if idempotency_key else f"d{uuid4().hex[:8]}"
    if idempotency_key:
        existing = await session.get(DocItem, doc_id)
        if existing is not None:
            return {"id": existing.id, "name": existing.name}
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



# Compatibility helpers for callers outside the streaming route. New requests use
# context_budget.prepare_session_history for synchronous semantic compaction.
BYTES_PER_TOKEN = 1
DEFAULT_MAX_CONTEXT_TOKENS = 32_000
BUDGET_RATIO = 0.8
FULL_ROUNDS = 6


def context_budget_bytes(max_context_tokens: int | None = None) -> int:
    from flowhub_api.services.context_budget import ContextBudget
    return int(ContextBudget(max_context_tokens or DEFAULT_MAX_CONTEXT_TOKENS).input_limit * BUDGET_RATIO)


def session_history_preview(rows, summary='', marker=0, budget_bytes=None):
    from flowhub_api.services.context_budget import history_messages
    messages = history_messages(rows, summary, marker)
    text = "\n".join(f"{role}: {content}" for role, content in messages)
    budget = context_budget_bytes() if budget_bytes is None else budget_bytes
    return text, len(text.encode('utf-8')) > budget


def build_session_history(rows, summary='', marker=0, budget_bytes=None):
    """Legacy synchronous fallback: explicitly trim rather than fake a summary."""
    text, needs = session_history_preview(rows, summary, marker, budget_bytes)
    if not needs:
        return text, summary, marker, bool(summary)
    budget = context_budget_bytes() if budget_bytes is None else budget_bytes
    recent = [(seq, role, content) for seq, role, content in rows if seq > marker and role in ('user', 'assistant') and content.strip()]
    summary = '【历史已裁剪】早期记录未完整保留。'
    while recent:
        text, needs = session_history_preview(recent, summary, marker, budget)
        if not needs:
            return text, summary, marker, True
        marker = recent[0][0]
        recent = recent[1:]
    text = summary if len(summary.encode('utf-8')) <= budget else ''
    return text, text, marker, True


async def compact_session_async(session_id: str, provider_max_tokens: int | None = None, summarize=None) -> None:
    """Background compaction with generation CAS; never resurrect cleared memory."""
    from sqlalchemy import select as sa_select, update
    from flowhub_api.db.session import SessionFactory
    from flowhub_api.services.context_budget import ContextBudget, prepare_session_history

    # Background work is optional; never discard history merely because no
    # summarizing model was supplied. The request path enforces hard budgets.
    if summarize is None:
        return
    async with SessionFactory() as db:
        chat = await db.get(ExpertChatSession, session_id)
        if chat is None:
            return
        version, marker = chat.compaction_version, chat.compaction_sequence
        msgs = (await db.execute(sa_select(ExpertChatMessage).where(
            ExpertChatMessage.session_id == session_id).order_by(ExpertChatMessage.sequence))).scalars().all()
        prepared = await prepare_session_history(
            [(m.sequence, m.role, m.content) for m in msgs], chat.compaction_summary,
            marker, budget=ContextBudget(provider_max_tokens or DEFAULT_MAX_CONTEXT_TOKENS),
            summarize=summarize,
        )
        if prepared.compacted:
            await db.execute(update(ExpertChatSession).where(
                ExpertChatSession.id == session_id,
                ExpertChatSession.compaction_version == version,
                ExpertChatSession.compaction_sequence == marker,
            ).values(compaction_summary=prepared.summary, compaction_sequence=prepared.marker,
                     compaction_version=version + 1))
            await db.commit()


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


async def run_native_flowhub_chat(session: AsyncSession, prompt: str, user: User, provider_model_id: str | None = None,
    history: str | list = "", on_token=None, on_trace=None, project_name: str | None = None,
    quality_mode: str = "balanced", emitter=None) -> tuple[str, list[dict]]:
    """Native chat uses the same bounded graph as Expert generation."""
    traces = []
    async def emit(event, payload):
        if event == "trace":
            traces.append(payload)
            if on_trace:
                await on_trace(payload)
        if emitter:
            await emitter(event, payload)
        elif event == "token" and on_token:
            await on_token(payload["text"])
    if not provider_model_id:
        snap = await flowhub_read_snapshot(session, user, prompt, project_name)
        answer = str(snap["pre_answer"] or "")
        for item in snap["trace"]:
            await emit("trace", {"kind": "tool", **item})
        # Local read-only fallback has no provider stream, but it must still
        # satisfy the same draft event contract as model-backed chat.
        await emit("draft_start", {"attemptId": 1})
        if answer:
            await emit("token", {"attemptId": 1, "text": answer})
        return answer, [{"kind": "tool", **item} for item in snap["trace"]]
    model = await session.get(LlmProviderModel, provider_model_id)
    provider = await session.get(LlmProvider, model.provider_id) if model else None
    if not model or not model.enabled or not provider or provider.status != "healthy" or not provider.credential_configured:
        raise ValueError("所选 Provider / 模型不可用，请检查配置")
    async def snapshot(text):
        return await flowhub_read_snapshot(session, user, text, project_name)
    async def repo_tools(text):
        return await create_repo_tool_bundle(session, project_name, user=user, query=text, allow_clone=False)
    graph = build_graph(provider, model,
        "你是 FlowHub 默认助手。只具备只读能力，不得声称已执行创建、提交、删除或发布。需要写操作时说明当前无法执行。",
        flowhub_snapshot=snapshot, emitter=emit, quality_mode=quality_mode,
        repo_tools_factory=repo_tools if project_name else None).compile()
    async with asyncio.timeout(900):
        result = await graph.ainvoke({"prompt": prompt, "history": history, "write_intent": False})
    return str(result.get("output") or ""), traces
