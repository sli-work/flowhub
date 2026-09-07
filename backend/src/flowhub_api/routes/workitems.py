"""工作项路由（docs/02 §六）：列表 / 详情 / 新建（发起流程）/ 停止。"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Text, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizError, BizCode, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import NotificationItem, TaskItem, User, WorkItem
from flowhub_api.schemas.api import CreateWorkItemReq, UpdateWorkItemPriorityReq
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.work_item_creation import create_work_item as create_work_item_service
from flowhub_api.services.task_lineage import historical_split_parent_ids_for_tasks
from flowhub_api.services.workflow import WorkflowService

router = APIRouter(prefix="/api/v1/work-items", tags=["work-items"])


def _brief(wi: WorkItem, assignees: list[str] | None = None) -> dict:
    # assignee 保留给旧客户端；assignees 是当前节点的全量共同处理人。
    return {
        "id": wi.id, "type": wi.type, "title": wi.title, "project": wi.project,
        "priority": wi.priority, "status": wi.status, "assignee": wi.assignee,
        "assignees": assignees if assignees is not None else ([wi.assignee] if wi.assignee and wi.assignee != "待分配" else []),
        "creator": wi.creator, "due": wi.due, "labels": wi.labels, "progress": wi.progress,
    }


async def _current_assignee_names(session: AsyncSession, wi: WorkItem, tasks: list[TaskItem]) -> list[str]:
    """返回所有活跃节点的共同处理人；并行节点会合并为一个去重名单。"""
    open_tasks = [task for task in tasks if task.status not in ("completed", "cancelled")]
    if not open_tasks:
        return [wi.assignee] if wi.assignee and wi.assignee != "待分配" else []
    workflow = WorkflowService(session)
    names: list[str] = []
    for task in open_tasks:
        recipients = await workflow.resolve_task_recipients(task)
        for recipient in recipients:
            if recipient.name not in names:
                names.append(recipient.name)
    if names:
        return names
    return [wi.assignee] if wi.assignee and wi.assignee != "待分配" else []


@router.get("")
async def list_work_items(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    type: str = "", status: str = "", project: str = "", priority: str = "",
    q: str = "", label: str = "",
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
    if q:
        stmt = stmt.where(WorkItem.title.contains(q))
    if label:
        # labels 为 JSON 数组列：序列化成文本后按带引号的完整串匹配，避免子串误命中
        stmt = stmt.where(WorkItem.labels.cast(Text).contains(f'"{label}"'))
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.order_by(WorkItem.id.collate("C").desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    task_rows = (await session.execute(
        select(TaskItem).where(TaskItem.wi_id.in_([row.id for row in rows]))
    )).scalars().all() if rows else []
    tasks_by_wi: dict[str, list[TaskItem]] = {row.id: [] for row in rows}
    for task in task_rows:
        tasks_by_wi.setdefault(task.wi_id, []).append(task)
    items = [
        _brief(row, await _current_assignee_names(session, row, tasks_by_wi[row.id]))
        for row in rows
    ]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


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
    # 兼容 parent_task_id 尚未落库的历史拆分记录：审计里保存了父任务和 child IDs，
    # 在流程图响应中即时恢复关系；不按任务创建顺序猜测，避免误连普通串行任务。
    historical_parents = await historical_split_parent_ids_for_tasks(
        session, {task.id for task in tasks if not task.parent_task_id},
    )
    parent_by_task = {task.id: task.parent_task_id or historical_parents.get(task.id) for task in tasks}
    inst = wi.instance
    # 老实例的游标可能仍停在已完成的拆分父节点。只在游标恰好指向该父节点时，
    # 以子任务的实际下一节点作为展示位置；并行子线在这里共享同一拆分起点。
    display_current_node = inst.current_node if inst is not None else ""
    if inst is not None:
        for child in tasks:
            parent_id = parent_by_task.get(child.id)
            parent = next((item for item in tasks if item.id == parent_id), None)
            if parent is not None and parent.node_id == inst.current_node:
                display_current_node = child.node_id
                break
    return ok({
        "item": _brief(wi, await _current_assignee_names(session, wi, tasks)),
        "startValues": wi.start_values or {},
        "instance": None if inst is None else {
            "id": inst.id, "templateId": inst.template_id, "version": inst.version,
            "currentNode": display_current_node, "state": inst.state,
        },
        "tasks": [{"id": t.id, "node": t.node, "nodeId": t.node_id, "status": t.status, "assignee": t.assignee, "due": t.due, "expertPending": t.expert_pending, "priority": t.priority, "title": t.title, "parentTaskId": parent_by_task.get(t.id), "lineageRootId": t.lineage_root_id} for t in tasks],
    })


@router.post("")
async def create_work_item(
    body: CreateWorkItemReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    result = await create_work_item_service(
        session, user=user, project_id=body.project_id, template_id=body.template_id,
        start_values=body.start_values, priority=body.priority, labels=body.labels,
    )
    wi = result["item"]
    from flowhub_api.routes.notifications import publish_notification

    for notification in result["notifications"]:
        await publish_notification(notification)
    return ok(
        {"item": _brief(wi), "instance": {"id": result["instance"].id, "current_node": result["next_node"]}},
        "工作项已创建，流程实例已发起",
    )


@router.patch("/{wi_id}/priority")
async def update_work_item_priority(
    wi_id: str,
    body: UpdateWorkItemPriorityReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """修改历史或进行中工作项优先级，并同步所有未终结任务。"""
    build_authorizer(user).require("workflow_instance:update")
    wi = await session.get(WorkItem, wi_id)
    if wi is None:
        raise BizError(BizCode.NOT_FOUND, "工作项不存在")

    previous_priority = wi.priority
    open_tasks = (await session.execute(
        select(TaskItem).where(
            TaskItem.wi_id == wi_id,
            TaskItem.status.not_in(["completed", "cancelled"]),
        )
    )).scalars().all()
    wi.priority = body.priority
    # 起始表单是工作项事实快照的一部分，优先级变更后应避免详情中仍展示旧值。
    wi.start_values = {**(wi.start_values or {}), "priority": body.priority}
    for task in open_tasks:
        task.priority = body.priority
    await AuditService(session).record(
        actor=user.name,
        action="workflow_instance:update_priority",
        target=f"{wi.id} · {wi.title}",
        result="success",
        before={"priority": previous_priority},
        after={"priority": body.priority, "updatedOpenTaskCount": len(open_tasks)},
    )
    await session.commit()
    return ok(
        {"item": _brief(wi), "updatedOpenTaskCount": len(open_tasks)},
        "工作项优先级已更新",
    )


@router.post("/{wi_id}/stop")
async def stop_work_item(
    wi_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """手动停止工作项：实例置 cancelled，未终结任务全部取消，工作项状态置 cancelled。"""
    auth = build_authorizer(user)
    auth.require("workflow_instance:cancel")
    wi = (await session.execute(
        select(WorkItem).options(selectinload(WorkItem.instance)).where(WorkItem.id == wi_id)
    )).scalar_one_or_none()
    if wi is None:
        raise BizError(BizCode.NOT_FOUND, "工作项不存在")
    if wi.status in ("closed", "cancelled", "archived"):
        raise BizError(BizCode.VALIDATION, f"工作项当前状态（{wi.status}）已终结，无需停止", http_status=409)
    # 未终结任务全部取消（含待处理与 AI 执行中的任务；后台 Expert Run 完成回调检测到非
    # pending_confirmation 会自行放弃采纳，无需额外中断）
    open_tasks = (await session.execute(
        select(TaskItem).where(
            TaskItem.wi_id == wi_id, TaskItem.status.not_in(["completed", "cancelled"])
        )
    )).scalars().all()
    for t in open_tasks:
        t.status = "cancelled"
    if wi.instance is not None:
        wi.instance.state = "cancelled"
    wi.status = "cancelled"
    wi.progress = f"{wi.progress}（手动停止）" if wi.progress else "手动停止"
    await AuditService(session).record(
        actor=user.name, action="workflow_instance:cancel",
        target=f"{wi.id} · {wi.title}", result="success",
        after={"cancelledTasks": [t.id for t in open_tasks]},
    )
    await session.commit()

    # 站内通知：创建人 + 被取消任务的处理人（去重）
    from flowhub_api.services.notify import deliver_channels

    now = datetime.now(UTC).strftime("%m-%d %H:%M")
    names = {wi.creator, *(t.assignee for t in open_tasks)} - {"—", ""}
    users = (await session.execute(
        select(User).where(User.name.in_(names))
    )).scalars().all() if names else []
    notified: set[str] = set()
    for u in users:
        if u.account in notified:
            continue
        notified.add(u.account)
        channels = await deliver_channels("工作项已停止", f"「{wi.title}」已被 {user.name} 手动停止，未完成任务已取消", u)
        session.add(NotificationItem(
            id=gen_id("ntf"), title="工作项已停止",
            body=f"「{wi.title}」已被 {user.name} 手动停止，未完成任务已取消",
            time=now, channels=channels, kind="info", unread=True,
            failed=any(not c["ok"] for c in channels), target_user=u.account, wi_id=wi.id,
        ))
    await session.commit()
    return ok({"item": _brief(wi)}, "工作项已停止，流程实例已取消")


@router.delete("/{wi_id}")
async def delete_work_item(
    wi_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """删除工作项（仅限已取消的工作项）：硬删工作项 + 流程实例 + 全部任务（数据库级联）。

    仅 cancelled 可删：运行中/已完成的工作项不可删除，取消是删除的前置状态。
    文档不删（挂在工作项下的文档解除关联后保留在文档中心）；通知/审计仅保留 ID 引用。
    """
    auth = build_authorizer(user)
    auth.require("workflow_instance:cancel")
    wi = (await session.execute(
        select(WorkItem).options(selectinload(WorkItem.instance)).where(WorkItem.id == wi_id)
    )).scalar_one_or_none()
    if wi is None:
        raise BizError(BizCode.NOT_FOUND, "工作项不存在")
    if wi.status != "cancelled":
        raise BizError(BizCode.VALIDATION, f"仅已取消的工作项可删除（当前状态 {wi.status}），请先停止流程", http_status=409)
    audit_target = f"{wi.id} · {wi.title}"
    task_count = len((await session.execute(
        select(TaskItem.id).where(TaskItem.wi_id == wi_id)
    )).scalars().all())
    # 实例/任务/追加信息均为 ondelete=CASCADE，随工作项硬删；文档解除关联保留
    await session.execute(
        sa_text("UPDATE documents SET wi = '' WHERE wi = :wi_id"), {"wi_id": wi_id},
    )
    await session.delete(wi)
    await AuditService(session).record(
        actor=user.name, action="workflow_instance:delete",
        target=audit_target, result="success",
        after={"deletedTasks": task_count},
    )
    await session.commit()
    return ok(message=f"工作项「{wi.title}」已删除（含 {task_count} 个任务）")
