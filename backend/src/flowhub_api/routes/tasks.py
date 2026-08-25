"""任务路由（docs/02 §七）：列表 / 候选处理人 / 节点动作（提交/退回/转办/认领）。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import GlobalTemplate, Project, TaskItem, User
from flowhub_api.schemas.api import TaskActionReq
from flowhub_api.services.audit import AuditService
from flowhub_api.services.workflow import WorkflowService

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


def _brief(t: TaskItem) -> dict:
    return {
        "id": t.id, "title": t.title, "wiId": t.wi_id, "project": t.project,
        "node": t.node, "nodeId": t.node_id, "type": t.type, "priority": t.priority, "status": t.status,
        "assignee": t.assignee, "due": t.due, "slaHours": t.sla_hours,
        "overdue": t.overdue, "agentPending": t.agent_pending, "source": t.source,
    }


@router.get("")
async def list_tasks(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status: str = "", node: str = "", assignee: str = "", priority: str = "", project: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(TaskItem)
    if status:
        stmt = stmt.where(TaskItem.status == status)
    if node:
        stmt = stmt.where(TaskItem.node.contains(node))
    if project:
        stmt = stmt.where(TaskItem.project.contains(project))
    if assignee:
        stmt = stmt.where(TaskItem.assignee == assignee)
    elif user.name != "系统管理员":
        # 普通用户仅见自己名下的任务；系统管理员可查看全部任务（全局视角）
        stmt = stmt.where(TaskItem.assignee == user.name)
    if priority:
        stmt = stmt.where(TaskItem.priority == priority)
    # 排序：未完成优先（待办在前），同状态按时间倒序（刚创建/刚完成的起始节点任务也能靠前可见）
    from sqlalchemy import case

    stmt = stmt.order_by(
        case((TaskItem.status.in_(["completed", "cancelled"]), 1), else_=0),
        TaskItem.id.collate("C").desc(),
    )
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    # 冻结标记：任务所属工作项已归档（项目归档冻结）→ 前端显示「冻结」并禁操作
    from flowhub_api.models import WorkItem

    archived_wis = {x[0] for x in (await session.execute(select(WorkItem.id).where(WorkItem.status == "archived"))).all()}
    items = [_brief(t) for t in rows]
    for it in items:
        it["frozen"] = it["wiId"] in archived_wis
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    # 冻结标记：任务所属工作项已归档（项目归档冻结）→ 处理页只读提示
    from flowhub_api.models import WorkItem as _WI

    frozen = (await session.execute(
        select(_WI.id).where(_WI.id == t.wi_id, _WI.status == "archived")
    )).first() is not None
    # Agent 建议（docs/02 §7.2）：该任务关联的 Agent 产出（含已应用/已拒绝，前端可展示状态）
    from flowhub_api.models import AgentSuggestion

    suggestions = (await session.execute(
        select(AgentSuggestion)
        .where(AgentSuggestion.task_id == task_id)
        .order_by(AgentSuggestion.time.desc())
    )).scalars().all()
    # 继承上下文（上游 chain）：起始节点表单 + 已完成前序节点的表单值（数据可见性 = 当前任务 + 已执行链）
    from flowhub_api.models import GlobalTemplate, TaskAppend, TemplateCanvas, WorkItem, WorkflowInstance

    wi = await session.get(WorkItem, t.wi_id)
    upstream: list[dict] = []
    start_node_ids: set = set()
    schema_map: dict[str, list] = {}
    if wi is not None:
        # 节点 → 表单 schema 映射（取自该实例绑定的模板版本画布，用于前端渲染补充表单）
        inst = (await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id == wi.id)
        )).scalar_one_or_none()
        if inst is not None:
            canvas = await session.get(TemplateCanvas, f"{inst.template_id}:{inst.version}")
            for n in (canvas.nodes if canvas and canvas.nodes else []):
                schema_map[n.get("id", "")] = (n.get("cfg") or {}).get("schema") or []
        appends = (await session.execute(
            select(TaskAppend).where(TaskAppend.wi_id == wi.id).order_by(TaskAppend.time)
        )).scalars().all()
        appends_by_task: dict[str, list] = {}
        for ap in appends:
            appends_by_task.setdefault(ap.task_id, []).append({
                "id": ap.id, "appender": ap.appender, "time": ap.time, "values": ap.values,
            })
        if wi.start_values:
            # 起始节点用画布真实节点名（如「需求提交」），避免泛称「起始表单」
            start_label = "起始表单"
            start_task_id = ""
            tpl = None
            if inst is not None:
                tpl = await session.get(GlobalTemplate, inst.template_id)
                if tpl is not None:
                    sn = next((n for n in tpl.nodes if n.get("type") == "start"), None)
                    if sn is not None:
                        start_label = sn.get("label", "起始表单")
                        start_node_ids.add(sn.get("id", ""))
            # 起始任务（已自动完成）作为追加 target：工作项第一个任务恒为起始任务（id 按创建序）
            st = (await session.execute(
                select(TaskItem).where(TaskItem.wi_id == wi.id).order_by(TaskItem.id).limit(1)
            )).scalar_one_or_none()
            if st is not None:
                start_task_id = st.id
            upstream.append({
                "node": start_label, "task_id": start_task_id, "values": wi.start_values,
                "assignee": st.assignee if st is not None else "",
                "schema": schema_map.get(next(iter(start_node_ids), ""), []),
                "appends": appends_by_task.get(start_task_id, []),
            })
        done = (await session.execute(
            select(TaskItem)
            .where(TaskItem.wi_id == t.wi_id, TaskItem.status == "completed", TaskItem.id != task_id)
            .order_by(TaskItem.id)
        )).scalars().all()
        for dt in done:
            # 起始节点任务（form_values = 起始表单）已由 start_values 表示，跳过避免重复
            if dt.node_id in start_node_ids:
                continue
            if dt.form_values:
                upstream.append({
                    "node": dt.node, "task_id": dt.id, "values": dt.form_values,
                    "assignee": dt.assignee,
                    "schema": schema_map.get(dt.node_id, []),
                    "appends": appends_by_task.get(dt.id, []),
                })
    return ok({
        "task": {**_brief(t), "frozen": frozen},
        "upstream": upstream,
        "suggestions": [{
            "id": sg.id, "agentId": sg.agent_id, "taskId": sg.task_id, "nodeId": sg.node_id,
            "title": sg.title, "body": sg.body, "status": sg.status, "time": sg.time,
            "appliedAt": sg.applied_at, "data": sg.data,
        } for sg in suggestions],
    })


@router.post("/{task_id}/appends")
async def append_task_info(
    task_id: str,
    body: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """前序节点补充信息：仅节点原处理人（或系统管理员）可给已完成的节点按 schema 结构化追加。

    追加不触发流转、不改节点状态，独立留痕（task_appends）；追加后通知当前节点处理人。
    """
    from datetime import datetime, UTC

    from flowhub_api.models import NotificationItem, TaskAppend, TemplateCanvas, WorkItem, WorkflowInstance
    from flowhub_api.seed.init import gen_id
    from flowhub_api.services.notify import deliver_channels

    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    # 冻结保护：归档项目只读，禁止追加
    from flowhub_api.models import WorkItem as _WI

    if (await session.execute(select(_WI.id).where(_WI.id == t.wi_id, _WI.status == "archived"))).first():
        raise BizError(BizCode.FORBIDDEN, "流程已冻结（项目归档），仅可查看", http_status=403)
    # 仅已完成节点可追加（流程已走过该节点）
    if t.status != "completed":
        raise BizError(BizCode.VALIDATION, f"节点「{t.node}」尚未完成，无需补充信息", http_status=409)
    # 权限：仅节点原处理人 / 系统管理员
    if t.assignee != user.name and user.name != "系统管理员":
        raise BizError(BizCode.PERM_DENIED, f"仅节点「{t.node}」的处理人（{t.assignee}）可补充信息", http_status=403)
    values = (body or {}).get("values") or {}
    # 按节点 schema 校验必填字段（与提交表单一致）
    wi = await session.get(WorkItem, t.wi_id)
    schema: list = []
    if wi is not None:
        inst = (await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id == wi.id)
        )).scalar_one_or_none()
        if inst is not None:
            canvas = await session.get(TemplateCanvas, f"{inst.template_id}:{inst.version}")
            if canvas and canvas.nodes:
                node = next((n for n in canvas.nodes if n.get("id") == t.node_id), None)
                if node is not None:
                    schema = (node.get("cfg") or {}).get("schema") or []
    for f in schema:
        if f.get("required") and not str(values.get(f.get("key"), "") or "").strip():
            raise BizError(BizCode.VALIDATION, f"「{f.get('label', f.get('key'))}」为必填，请补充")
    ap = TaskAppend(
        id=gen_id("app"), task_id=t.id, node_id=t.node_id, wi_id=t.wi_id,
        appender=user.name, time=datetime.now(UTC).strftime("%m-%d %H:%M"), values=values,
    )
    session.add(ap)
    # 通知当前节点处理人（工作项第一个未完成任务；流程已结束则跳过）
    current = (await session.execute(
        select(TaskItem).where(
            TaskItem.wi_id == t.wi_id,
            TaskItem.status.not_in(["completed", "cancelled"]),
        ).order_by(TaskItem.id).limit(1)
    )).scalar_one_or_none()
    notification: NotificationItem | None = None
    if current is not None:
        # target_user 按 account 存储（与通知列表过滤一致：assignee 存的是 name，需换算）
        cur_user = (await session.execute(select(User).where(User.name == current.assignee))).scalar_one_or_none()
        target = cur_user.account if cur_user else current.assignee
        notification = NotificationItem(
            id=gen_id("ntf"), title="节点信息已补充",
            body=f"节点「{t.node}」由 {user.name} 补充了信息，处理前请查看（当前节点「{current.node}」）",
            time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=[],
            kind="info", unread=True, failed=False,
            target_user=target, wi_id=t.wi_id, task_id=current.id,
        )
        session.add(notification)
        channels = await deliver_channels(
            "节点信息已补充", f"「{t.title}」节点「{t.node}」有补充信息，当前节点「{current.node}」请留意", cur_user,
        )
        notification.channels = channels
        notification.failed = any(not channel["ok"] for channel in channels)
    await AuditService(session).record(
        actor=user.name, action="task:append", target=f"{t.id} · {t.node}", result="success",
    )
    await session.commit()
    if notification is not None:
        from flowhub_api.routes.notifications import publish_notification

        await publish_notification(notification)
    return ok({"taskId": t.id, "node": t.node, "values": values}, f"已补充节点「{t.node}」信息（原提交不变，追加留痕）")


@router.get("/{task_id}/candidates")
async def task_candidates(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """节点候选处理人：绑定用户 + 角色/技能解析（docs/04 §5.3）。"""
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    project = (await session.execute(select(Project).where(Project.name == t.project))).scalar_one_or_none()
    tpl = None
    if project:
        binding = next((b for b in project.template_bindings if b.status == "active"), None)
        if binding:
            tpl = await session.get(GlobalTemplate, binding.template_id)
    assignees: list[dict] = []
    if project and tpl:
        service = WorkflowService(session)
        users = await service.resolve_node_assignees(project.id, tpl.id, t.node_id or t.node)
        assignees = [{"id": u.id, "name": u.name, "dept": u.dept} for u in users]
    return ok({"users": assignees, "node": t.node})


@router.post("/{task_id}/actions")
async def task_action(
    task_id: str,
    body: TaskActionReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")

    # 归档项目冻结：任务所属工作项已归档（或项目已归档）→ 禁止任何流转操作（保留可查看）
    from flowhub_api.models import Project, WorkItem

    frozen_wi = await session.get(WorkItem, t.wi_id)
    if frozen_wi is not None and frozen_wi.status == "archived":
        raise BizError(BizCode.FORBIDDEN, "项目已归档，流程已冻结：任务仅可查看，不可提交/退回/转办", http_status=403)
    frozen_proj = (await session.execute(
        select(Project).where(Project.name == t.project)
    )).scalar_one_or_none()
    if frozen_proj is not None and (frozen_proj.status == "archived" or frozen_proj.read_only):
        raise BizError(BizCode.FORBIDDEN, "项目已归档（只读），流程已冻结：任务仅可查看", http_status=403)

    # 幂等保护：已完成/已取消的任务不可重复处理（submit/return/transfer）
    if body.action in ("submit", "return", "transfer", "request_info") and t.status in ("completed", "cancelled"):
        raise BizError(BizCode.DUPLICATE_OPERATION, "任务已处理，不可重复操作（幂等保护）", http_status=409)

    if body.action == "claim":
        auth.require("task:claim")
        if t.status not in ("assigned", "transferred"):
            raise BizError(BizCode.DUPLICATE_OPERATION, "任务已认领，不可重复认领", http_status=409)
        t.status = "accepted"
        t.assignee = user.name
        await AuditService(session).record(actor=user.name, action="task:claim", target=f"{t.id} · {t.node}", result="success")
        await session.commit()
        return ok({"task": _brief(t)}, "任务已认领")

    if body.action == "transfer":
        auth.require("task:transfer")
        if not body.to_user_id:
            raise BizError(BizCode.VALIDATION, "请选择转办对象")
        target = await session.get(User, body.to_user_id)
        if target is None:
            raise BizError(BizCode.NOT_FOUND, "转办用户不存在")
        t.status = "transferred"
        t.assignee = target.name
        t.source = f"{user.name}转办"
        await AuditService(session).record(
            actor=user.name, action="task:transfer", target=f"{t.id} → {target.name}", result="success",
        )
        await session.commit()
        return ok({"task": _brief(t)}, f"任务已转办给 {target.name}")

    if body.action == "return":
        auth.require("task:return")
        if not body.to_node_id:
            raise BizError(BizCode.VALIDATION, "请选择回退目标节点")
        t.status = "returned"
        t.node = body.to_node_id
        await AuditService(session).record(
            actor=user.name, action="task:return", target=f"{t.id} → {body.to_node_id}", result="success",
        )
        await session.commit()
        return ok({"task": _brief(t)}, f"任务已回退至 {body.to_node_id}")

    if body.action == "submit":
        auth.require("task:submit")
        # Agent 自动节点：必须先批准确认请求，禁止直接提交（绕过 Agent 参与）
        if t.status == "pending_confirmation":
            raise BizError(
                BizCode.DUPLICATE_OPERATION,
                "该节点由 Agent 自动处理，需先批准 Agent 确认请求后才能提交",
                http_status=409,
            )
        t.form_values = body.form_values or {}
        # 推进流程：需要项目 + 模板定位主边
        project = (await session.execute(select(Project).where(Project.name == t.project))).scalar_one_or_none()
        tpl = None
        if project:
            binding = next((b for b in project.template_bindings if b.status == "active"), None)
            if binding:
                tpl = await session.get(GlobalTemplate, binding.template_id)
        if project and tpl:
            service = WorkflowService(session)
            result = await service.advance(t, project, tpl)
            await AuditService(session).record(
                actor=user.name, action="task:submit", target=f"{t.id} · {t.node}", result="success",
                after={"next": result["next_node"].get("label") if result["next_node"] else None,
                       "assignees": [a["name"] for a in result["next_assignees"]]},
            )
            await session.commit()
            nxt_task = result.get("task") or (result.get("tasks") or [None])[0]
            next_tasks = [
                {"id": x.id, "node": x.node, "status": x.status, "assignee": x.assignee}
                for x in (result.get("tasks") or ([nxt_task] if nxt_task else []))
            ]
            if result.get("parallel"):
                msg = f"提交成功：已并行拆分 {len(next_tasks)} 个分支任务"
            elif result.get("waiting_join"):
                msg = "提交成功：等待其他并行分支完成后自动汇合"
            else:
                msg = "提交成功：已自动流转至下一节点并分配处理人"
            return ok({
                "task": _brief(t),
                "next_node": result["next_node"],
                "next_assignees": result["next_assignees"],
                "next_task_id": nxt_task.id if nxt_task else None,
                "next_tasks": next_tasks,
                "parallel": bool(result.get("parallel")),
                "waiting_join": bool(result.get("waiting_join")),
                "closed": bool(result.get("closed")),
            }, msg)
        # 无项目绑定 → 仅标记完成
        t.status = "completed"
        await session.commit()
        return ok({"task": _brief(t)}, "提交成功")

    if body.action == "request_info":
        t.status = "waiting_for_information"
        await AuditService(session).record(
            actor=user.name, action="task:request_info", target=f"{t.id} · {t.node}", result="success",
        )
        await session.commit()
        return ok({"task": _brief(t)}, "补充信息请求已发送：SLA 暂停计时")

    raise BizError(BizCode.VALIDATION, f"未知动作：{body.action}")
