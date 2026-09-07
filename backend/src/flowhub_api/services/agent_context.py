"""Agent 上下文组装：按当前用户权限，读取当前节点任务 + 该节点之前所有节点的表单内容 + 关联文档。

权限语义与 `GET /tasks` 列表一致（docs/03）：
- 系统管理员 / 组织管理员：可读任意任务上下文
- 其他用户：可读自己被节点绑定为共同处理人的任务
无权限时抛 403（调用方写审计 denied），绝不静默返回部分数据。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import DocItem, TaskAppend, TaskItem, User, WorkItem
from flowhub_api.services.document_access import document_content_link
from flowhub_api.services import repo_mirror

logger = logging.getLogger(__name__)

_ADMIN_ROLES = ("system_admin", "organization_admin")
# 文档正文注入的最大字节数（防止超大文件撑爆 prompt）
_MAX_DOC_BYTES = 4096


async def can_read_task(session: AsyncSession, user: User, task: TaskItem) -> bool:
    """当前用户是否有权读取该任务上下文（含其所属工作项的历史节点与文档）。"""
    if any(r.id in _ADMIN_ROLES for r in user.roles):
        return True
    if task.assignee in {user.name, user.account, user.id}:
        return True
    from flowhub_api.services.workflow import WorkflowService

    recipients = await WorkflowService(session).resolve_task_recipients(task)
    return any(candidate.id == user.id for candidate in recipients)


def _fmt_values(values: dict | None) -> str:
    if not values:
        return "（无表单值）"
    try:
        return "\n".join(f"  - {k}: {v}" for k, v in values.items() if v not in (None, ""))
    except Exception:  # noqa: BLE001
        return f"（表单值序列化失败：{values}）"


def _fmt_doc(d: DocItem) -> str:
    ver = d.version if d.version.startswith("v") else f"v{d.version}"
    return f"  - {d.name}（{d.kind or '文档'} · {ver} · {d.level} · 上传 {d.uploader} · {d.size} · 受控读取链接 {document_content_link(d.id)}）"


def _file_ids(values: dict | None) -> set[str]:
    """提取新版表单附件引用；旧版仅存文件名，不能安全反向匹配。"""
    ids: set[str] = set()
    for value in (values or {}).values():
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.add(entry["id"])
    return ids


async def _doc_body(doc: DocItem) -> str:
    """尝试读取文档正文（MinIO 文本对象），失败返回空串。"""
    if not doc.name.lower().endswith((".txt", ".md", ".yaml", ".yml")):
        return ""
    if not doc.object_name:
        return ""
    try:
        from flowhub_api.clients.minio import get_minio
        from flowhub_api.core.config import get_settings

        minio = get_minio()
        if minio is None:
            return ""
        resp = minio.get_object(get_settings().minio_bucket, doc.object_name)
        data = resp.read(_MAX_DOC_BYTES + 1)
        resp.close()
        resp.release_conn()
        text = data.decode("utf-8", errors="replace")
        truncated = len(data) > _MAX_DOC_BYTES
        return text[:_MAX_DOC_BYTES] + ("\n…（正文过长已截断）" if truncated else "")
    except Exception as exc:  # noqa: BLE001 — 文档正文不可读时仅记日志，不阻断
        logger.debug("文档正文读取失败 %s: %s", doc.id, exc)
        return ""


async def build_task_context(session: AsyncSession, user: User, task: TaskItem, *, include_docs: bool = True) -> str:
    """组装 Agent 可见的节点上下文：工作项信息 + 当前任务 + 该节点之前所有节点表单 + 关联文档。

    raise BizError(403) 当用户无权限读取该任务。
    """
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务上下文（仅任务处理人或管理员可见）", http_status=403)

    wi = await session.get(WorkItem, task.wi_id)
    lines: list[str] = []
    lines.append("【工作项信息】")
    if wi:
        lines.append(f"  - ID: {wi.id}｜标题: {wi.title}｜类型: {wi.type}｜项目: {wi.project}｜状态: {wi.status}｜优先级: {wi.priority}")
        if wi.start_values:
            lines.append("  - 起始提交表单：")
            lines.append(_fmt_values(wi.start_values).replace("\n", "\n    "))
    else:
        lines.append(f"  - ID: {task.wi_id}（工作项不存在）")

    # 绑定仓库的地图层（目录概览/README/依赖清单）：失败降级为空，不阻断上下文
    if wi is not None:
        repo_section = await repo_mirror.repo_map_section(session, wi.project, allow_clone=True, query=f"{task.title} {task.brief}")
        if repo_section:
            lines.append("")
            lines.append(repo_section)

    from flowhub_api.services.workflow import WorkflowService

    current_recipients = await WorkflowService(session).resolve_task_recipients(task)
    current_assignees = "、".join(user.name for user in current_recipients) or task.assignee
    lines.append("")
    lines.append(f"【当前节点任务】{task.id}｜节点「{task.node}」｜状态 {task.status}｜处理人 {current_assignees}｜截止 {task.due}")
    lines.append("  当前节点表单值：")
    lines.append(_fmt_values(task.form_values).replace("\n", "\n  "))

    # 与处理页继承上下文一致：仅已完成前序节点及其追加信息。
    lines.append("")
    lines.append("【该节点之前的节点记录】")
    prev_tasks = (await session.execute(
        select(TaskItem)
        .where(TaskItem.wi_id == task.wi_id, TaskItem.id < task.id, TaskItem.status == "completed")
        .order_by(TaskItem.id.asc())
    )).scalars().all()
    attachment_ids = _file_ids(wi.start_values if wi else None)
    if prev_tasks:
        for t in prev_tasks:
            lines.append(f"  - {t.id}｜节点「{t.node}」｜状态 {t.status}｜处理人 {t.assignee}｜截止 {t.due}")
            if t.form_values:
                lines.append("      表单值：")
                lines.append(_fmt_values(t.form_values).replace("\n", "\n        "))
                attachment_ids.update(_file_ids(t.form_values))
    else:
        lines.append("  （无——当前为该流程首个进入的节点）")

    appends = (await session.execute(
        select(TaskAppend).where(TaskAppend.wi_id == task.wi_id, TaskAppend.task_id.in_([t.id for t in prev_tasks])).order_by(TaskAppend.time)
    )).scalars().all() if prev_tasks else []
    if appends:
        lines.append("【前序节点补充信息】")
        for append in appends:
            lines.append(f"  - {append.appender} · {append.time}：")
            lines.append(_fmt_values(append.values).replace("\n", "    \n"))
            attachment_ids.update(_file_ids(append.values))

    if include_docs:
        lines.append("")
        lines.append("【关联文档】")
        docs = (await session.execute(
            select(DocItem).where(DocItem.id.in_(attachment_ids), DocItem.wi == task.wi_id, DocItem.deleted == False)  # noqa: E712
        )).scalars().all() if attachment_ids else []
        if docs:
            for d in docs:
                lines.append(_fmt_doc(d))
                body = await _doc_body(d)
                if body:
                    lines.append(f"      文档内容片段：{body[:300]}")
        else:
            lines.append("  （该工作项暂无关联文档）")

    return "\n".join(lines)
