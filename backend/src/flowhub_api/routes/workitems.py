"""工作项路由（docs/02 §六）：列表 / 详情 / 新建（发起流程）。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizError, BizCode, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import DocItem, TaskItem, User, WorkItem
from flowhub_api.schemas.api import CreateWorkItemReq
from flowhub_api.services.audit import AuditService
from flowhub_api.services.workflow import WorkflowService

router = APIRouter(prefix="/api/v1/work-items", tags=["work-items"])


def _attachment_ids(values: dict | None) -> set[str]:
    """从表单中的新版附件引用提取文档 ID；旧版文件名不能安全关联。"""
    ids: set[str] = set()
    for value in (values or {}).values():
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.add(entry["id"])
    return ids


def _brief(wi: WorkItem) -> dict:
    return {
        "id": wi.id, "type": wi.type, "title": wi.title, "project": wi.project,
        "priority": wi.priority, "status": wi.status, "assignee": wi.assignee,
        "creator": wi.creator, "due": wi.due, "labels": wi.labels, "progress": wi.progress,
    }


@router.get("")
async def list_work_items(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    type: str = "", status: str = "", project: str = "", priority: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(WorkItem)
    if type:
        stmt = stmt.where(WorkItem.type == type)
    if status:
        stmt = stmt.where(WorkItem.status == status)
    if project:
        stmt = stmt.where(WorkItem.project.contains(project))
    if priority:
        stmt = stmt.where(WorkItem.priority == priority)
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.order_by(WorkItem.id.collate("C").desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return ok({"items": [_brief(w) for w in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/{wi_id}")
async def get_work_item(
    wi_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    wi = (await session.execute(
        select(WorkItem).options(selectinload(WorkItem.instance)).where(WorkItem.id == wi_id)
    )).scalar_one_or_none()
    if wi is None:
        raise BizError(BizCode.NOT_FOUND, "工作项不存在")
    # 按 id 升序（创建序）：时间线/起始任务稳定在首位（task id 后缀随机，不能依赖物理顺序）
    tasks = (await session.execute(
        select(TaskItem).where(TaskItem.wi_id == wi_id).order_by(TaskItem.id)
    )).scalars().all()
    inst = wi.instance
    return ok({
        "item": _brief(wi),
        "startValues": wi.start_values or {},
        "instance": None if inst is None else {
            "id": inst.id, "templateId": inst.template_id, "version": inst.version,
            "currentNode": inst.current_node, "state": inst.state,
        },
        "tasks": [{"id": t.id, "node": t.node, "nodeId": t.node_id, "status": t.status, "assignee": t.assignee, "due": t.due, "agentPending": t.agent_pending} for t in tasks],
    })


@router.post("")
async def create_work_item(
    body: CreateWorkItemReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("workflow_instance:create")
    service = WorkflowService(session)
    result = await service.create_instance(body.project_id, body.template_id, body.start_values, user)
    wi = result["item"]
    attachment_ids = _attachment_ids(body.start_values)
    if attachment_ids:
        # 起始表单在工作项 ID 生成前上传。仅归档当前用户在同项目上传的未绑定文档，防止借 ID 关联他人文件。
        docs = (await session.execute(
            select(DocItem).where(
                DocItem.id.in_(attachment_ids), DocItem.wi.is_(None),
                DocItem.project == wi.project, DocItem.uploader == user.name, DocItem.deleted == False,  # noqa: E712
            )
        )).scalars().all()
        for doc in docs:
            doc.wi = wi.id
    await AuditService(session).record(
        actor=user.name, action="workflow_instance:create",
        target=f"{wi.id} · {wi.title}（{result['next_node'].get('label', '')}）", result="success",
    )
    await session.commit()
    from flowhub_api.routes.notifications import publish_notification

    for notification in result["notifications"]:
        await publish_notification(notification)
    return ok(
        {"item": _brief(wi), "instance": {"id": result["instance"].id, "current_node": result["next_node"]}},
        "工作项已创建，流程实例已发起",
    )
