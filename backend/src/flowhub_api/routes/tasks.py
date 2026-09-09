"""任务路由（docs/02 §七）：列表 / 候选处理人 / 节点动作（提交/退回/转办/认领）。"""
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import GlobalTemplate, NodeAssignment, Project, ProjectTemplateBinding, TaskItem, User, WorkflowInstance, WorkflowIssue
from flowhub_api.schemas.api import IssueCreateReq, IssueVerifyReq, TaskActionReq, TaskAdoptRunReq, TaskAiFillReq, TaskSplitReq
from flowhub_api.services.audit import AuditService
from flowhub_api.services.agent_context import can_read_task
from flowhub_api.services.task_lineage import historical_split_parent_ids_for_tasks
from flowhub_api.services.task_issues import TaskIssueService
from flowhub_api.services.workflow import WorkflowService, is_empty_form_value, main_task_clause

logger = logging.getLogger("flowhub_api")

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

# 状态分组（10 个状态全覆盖）：列表 Tab 与计数聚合共用
STATUS_GROUPS = {
    "todo": ("assigned", "accepted", "returned", "transferred"),   # 转办后待新处理人认领，归待处理
    "doing": ("in_progress", "waiting_for_information", "pending_confirmation"),
    "submitted": ("submitted",),
    "done": ("completed", "cancelled"),
}

# 优先级排序权重（priority 为 PG enum，需显式 == 比较，不能用 case(value=) 的 varchar 等值）
_PRIORITY_ORDER = case(
    (TaskItem.priority == "P0", 0), (TaskItem.priority == "P1", 1),
    (TaskItem.priority == "P2", 2), (TaskItem.priority == "P3", 3), else_=9,
)


async def _shared_node_task_ids(session: AsyncSession, user: User) -> set[str]:
    """返回当前用户作为节点共同处理人时可见的任务 ID。

    TaskItem.assignee 是旧的单字符串字段，创建任务时只会展示多个绑定人中的首位。
    节点绑定本身支持多用户/角色，所以列表必须同时按绑定关系补充可见范围。
    """
    role_tags = {role.id for role in user.roles} | set(user.skills or [])
    binding_rows = (await session.execute(
        select(NodeAssignment, ProjectTemplateBinding.template_id, Project.name)
        .join(ProjectTemplateBinding, NodeAssignment.binding_id == ProjectTemplateBinding.id)
        .join(Project, ProjectTemplateBinding.project_id == Project.id)
        .where(ProjectTemplateBinding.status == "active")
    )).all()
    shared_nodes = {
        (project_name, template_id, assignment.node_id)
        for assignment, template_id, project_name in binding_rows
        if user.id in (assignment.users or []) or role_tags.intersection(assignment.roles or [])
    }
    if not shared_nodes:
        return set()
    task_rows = (await session.execute(
        select(TaskItem.id, TaskItem.project, TaskItem.node_id, WorkflowInstance.template_id)
        .join(WorkflowInstance, WorkflowInstance.work_item_id == TaskItem.wi_id)
    )).all()
    return {
        task_id for task_id, project_name, node_id, template_id in task_rows
        if (project_name, template_id, node_id) in shared_nodes
    }


def _brief(t: TaskItem) -> dict:
    return {
        "id": t.id, "title": t.title, "wiId": t.wi_id, "project": t.project,
        "node": t.node, "nodeId": t.node_id, "type": t.type, "priority": t.priority, "status": t.status,
        "assignee": t.assignee, "due": t.due, "slaHours": t.sla_hours,
        "overdue": t.overdue, "expertPending": t.expert_pending, "source": t.source,
        "parentTaskId": t.parent_task_id, "lineageRootId": t.lineage_root_id, "brief": t.brief or "",
        "createdAt": t.created_at,
    }


@router.get("")
async def list_tasks(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status: str = "", node: str = "", assignee: str = "", priority: str = "", project: str = "",
    q: str = "", status_group: str = "", sort: str = "default",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    conds = []
    visibility = None
    if status:
        conds.append(TaskItem.status == status)
    if status_group in STATUS_GROUPS:
        conds.append(TaskItem.status.in_(STATUS_GROUPS[status_group]))
    if node:
        conds.append(TaskItem.node.contains(node))
    if project:
        conds.append(TaskItem.project.contains(project))
    if assignee:
        conds.append(TaskItem.assignee == assignee)
    elif user.name != "系统管理员":
        # 单人任务仍按 assignee 过滤；多人节点则按项目模板绑定补齐共同处理人的可见范围。
        shared_ids = await _shared_node_task_ids(session, user)
        visibility = or_(TaskItem.assignee.in_([user.name, user.account, user.id]), TaskItem.id.in_(shared_ids))
        conds.append(visibility)
    if priority:
        conds.append(TaskItem.priority == priority)
    if q:
        conds.append(TaskItem.title.contains(q))
    base = select(TaskItem).where(*conds)

    # 冻结：所属工作项已归档（项目归档冻结）→ 排序沉底 + 前端「冻结」标记
    from flowhub_api.models import WorkItem

    archived_subq = select(WorkItem.id).where(WorkItem.status == "archived")
    frozen_last = case((TaskItem.wi_id.in_(archived_subq), 1), else_=0)
    # 排序：未完成优先（待办在前），同状态按时间倒序；冻结任务一律排最后；created 为纯时间线倒序
    open_first = case((TaskItem.status.in_(["completed", "cancelled"]), 1), else_=0)
    if sort == "created":
        stmt = base.order_by(TaskItem.created_at.collate("C").desc(), TaskItem.id.collate("C").desc())
    elif sort == "priority":
        stmt = base.order_by(frozen_last, open_first, _PRIORITY_ORDER.asc(), TaskItem.id.collate("C").desc())
    elif sort == "due":
        # due 为零填充 "%m-%d" 展示串，同年内字典序即时间序；先超时、再临期
        stmt = base.order_by(frozen_last, open_first, TaskItem.overdue.desc(), TaskItem.due.collate("C").asc(), TaskItem.id.collate("C").desc())
    else:
        stmt = base.order_by(frozen_last, open_first, TaskItem.id.collate("C").desc())
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    archived_wis = {x[0] for x in (await session.execute(archived_subq)).all()}
    items = [_brief(t) for t in rows]
    for it in items:
        it["frozen"] = it["wiId"] in archived_wis
    # Tab 计数 / KPI 聚合：与列表同一过滤范围（不含 status/status_group/q），一条 SQL 出 7 个计数
    stats_where = []
    if node:
        stats_where.append(TaskItem.node.contains(node))
    if project:
        stats_where.append(TaskItem.project.contains(project))
    if assignee:
        stats_where.append(TaskItem.assignee == assignee)
    elif visibility is not None:
        stats_where.append(visibility)
    if priority:
        stats_where.append(TaskItem.priority == priority)
    all_count, todo, doing, submitted, done, overdue_n, expert_n = (await session.execute(
        select(
            func.count(TaskItem.id),
            func.count(TaskItem.id).filter(TaskItem.status.in_(STATUS_GROUPS["todo"])),
            func.count(TaskItem.id).filter(TaskItem.status.in_(STATUS_GROUPS["doing"])),
            func.count(TaskItem.id).filter(TaskItem.status.in_(STATUS_GROUPS["submitted"])),
            func.count(TaskItem.id).filter(TaskItem.status.in_(STATUS_GROUPS["done"])),
            func.count(TaskItem.id).filter(TaskItem.overdue.is_(True)),
            func.count(TaskItem.id).filter(TaskItem.expert_pending.is_(True)),
        ).where(*stats_where)
    )).one()
    stats = {
        "all": all_count, "todo": todo, "doing": doing, "submitted": submitted, "done": done,
        "open": all_count - done, "overdue": overdue_n, "expertPending": expert_n,
    }
    return ok({"items": items, "total": total, "page": page, "page_size": page_size, "stats": stats})


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    if not await can_read_task(session, user, t):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务", http_status=403)
    # 冻结标记：任务所属工作项已归档（项目归档冻结）→ 处理页只读提示
    from flowhub_api.models import WorkItem as _WI

    frozen = (await session.execute(
        select(_WI.id).where(_WI.id == t.wi_id, _WI.status == "archived")
    )).first() is not None
    # Expert Run is the execution record. Task-level suggestions are now
    # represented by linked Expert Run output rather than a parallel Agent table.
    suggestions = []
    # 继承上下文（上游 chain）：起始节点表单 + 已完成前序节点的表单值（数据可见性 = 当前任务 + 已执行链）
    from flowhub_api.models import GlobalTemplate, TaskAppend, TemplateCanvas, WorkItem, WorkflowInstance

    wi = await session.get(WorkItem, t.wi_id)
    upstream: list[dict] = []
    start_node_ids: set = set()
    schema_map: dict[str, list] = {}
    # 节点 → 表单 schema 映射：严格跟随该工作项绑定的模板版本，不能取当前最新发布版本。
    node_cfg: dict = {}
    fallback_targets: list[dict[str, str]] = []
    if wi is not None:
        inst = (await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id == wi.id)
        )).scalar_one_or_none()
        if inst is not None:
            service = WorkflowService(session)
            tpl = await session.get(GlobalTemplate, inst.template_id)
            if tpl is not None:
                latest_nodes = await service.latest_published_canvas_nodes(tpl, inst.version)
                canvas = await session.get(TemplateCanvas, f"{inst.template_id}:{inst.version}")
                if canvas is not None:
                    labels = {node.get("id", ""): node.get("label", node.get("id", "")) for node in canvas.nodes}
                    fallback_targets = [
                        {"id": target_id, "label": labels.get(target_id, target_id)}
                        for source_id, target_id in canvas.fallbacks
                        if source_id == t.node_id and target_id in labels
                    ]
                for n in latest_nodes:
                    schema_map[n.get("id", "")] = (n.get("cfg") or {}).get("schema") or []
                if t.node_id:
                    node_cfg = next((n.get("cfg") or {}) for n in latest_nodes if n.get("id") == t.node_id) if any(n.get("id") == t.node_id for n in latest_nodes) else (await service._node_cfg_of(tpl, t.node_id, inst.version) or {})
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
                    version_nodes = await WorkflowService(session).nodes_of(tpl, inst.version)
                    sn = next((n for n in version_nodes if n.get("type") == "start"), None)
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
            .where(TaskItem.wi_id == t.wi_id, TaskItem.status == "completed", TaskItem.id != task_id, main_task_clause())
            .order_by(TaskItem.id)
        )).scalars().all()
        for dt in done:
            # 起始节点任务（form_values = 起始表单）已由 start_values 表示，跳过避免重复
            if dt.node_id in start_node_ids:
                continue
            # 拆分控制字段不是父任务上下文。父任务原有表单值会在 split 时保留，
            # 因此子任务可和普通后继任务一样在这里读取到它。
            context_values = {
                key: value for key, value in (dt.form_values or {}).items()
                if not str(key).startswith("__") and key != "split_to"
            }
            if context_values:
                upstream.append({
                    "node": dt.node, "task_id": dt.id, "values": context_values,
                    "assignee": dt.assignee,
                    "schema": schema_map.get(dt.node_id, []),
                    "appends": appends_by_task.get(dt.id, []),
                })
    # 存量拆分发生在 parent_task_id 落库前：从不可变审计记录恢复直接父链，
    # 让历史子任务的任务书和继承上下文不依赖一次性迁移是否已经跑过。
    legacy_parent_ids = await historical_split_parent_ids_for_tasks(session, {t.id}) if not t.parent_task_id else {}
    effective_parent_id = t.parent_task_id or legacy_parent_ids.get(t.id)
    parent_task = await session.get(TaskItem, effective_parent_id) if effective_parent_id else None
    # 子任务优先显式注入直属父任务。不要依赖“已完成任务列表”顺序或表单是否非空，
    # 否则拆分刚完成时父任务上下文可能被过滤掉。
    if parent_task is not None and parent_task.id not in {item.get("task_id") for item in upstream}:
        # 旧拆分可能把父表单覆盖成拆分控制数据，原字段无法凭空复原；仍应提供可见的
        # 父任务摘要，避免子任务页完全失去来源上下文。
        parent_values = {
            key: value for key, value in (parent_task.form_values or {}).items()
            if not str(key).startswith("__") and key != "split_to"
        } or {
            "父任务": parent_task.title,
            "节点": parent_task.node,
            "任务说明": parent_task.brief or "（历史任务未保留表单产出）",
        }
        upstream.insert(0, {"node": f"父任务 · {parent_task.node}", "task_id": parent_task.id, "values": parent_values, "assignee": parent_task.assignee, "schema": schema_map.get(parent_task.node_id, []), "appends": appends_by_task.get(parent_task.id, [])})
    subtasks = (await session.execute(
        select(TaskItem).where(TaskItem.parent_task_id == t.id).order_by(TaskItem.id)
    )).scalars().all()
    # 关联的 Expert Run（Expert 自动/协助填充产生）
    from flowhub_api.models import ExpertRun, ExpertRunEvent

    linked_runs = (await session.execute(
        select(ExpertRun).where(ExpertRun.task_id == t.id).order_by(ExpertRun.started_at.desc(), ExpertRun.id.desc()).limit(3)
    )).scalars().all()
    run_ids = [run.id for run in linked_runs]
    event_rows = (await session.execute(
        select(ExpertRunEvent).where(ExpertRunEvent.run_id.in_(run_ids)).order_by(ExpertRunEvent.run_id, ExpertRunEvent.sequence)
    )).scalars().all() if run_ids else []
    events_by_run: dict[str, list[dict]] = {run_id: [] for run_id in run_ids}
    for event in event_rows:
        events_by_run[event.run_id].append({
            "sequence": event.sequence, "kind": event.kind, "status": event.status, "title": event.title,
            "summary": (event.payload or {}).get("summary", ""), "durationMs": event.duration_ms,
            "createdAt": event.created_at,
        })
    # 本任务提交/自动流转后的下一任务（同工作项、创建于本任务之后的第一个未完成任务）：
    # 前端在 Expert 自动节点完成后直达下一任务，不再手动回工作项找
    next_task_id = ""
    if t.status == "completed":
        nt = (await session.execute(
            select(TaskItem)
            .where(TaskItem.wi_id == t.wi_id, TaskItem.id != t.id, TaskItem.status != "completed", TaskItem.created_at > (t.created_at or ""))
            .order_by(TaskItem.created_at)
            .limit(1)
        )).scalar_one_or_none()
        if nt is not None:
            next_task_id = nt.id
    return ok({
        # 详情必须回传本节点已保存的表单快照。列表接口刻意不带该大字段，
        # 但任务处理页打开历史已完成节点时需要据此只读回显。
        "task": {
            **_brief(t), "parentTaskId": effective_parent_id, "frozen": frozen,
            "formValues": t.form_values or {}, "acceptanceChecks": t.acceptance_checks or {},
        },
        "upstream": upstream,
        "parent": _brief(parent_task) if parent_task else None,
        "subtasks": [
            {"id": s.id, "title": s.title, "node": s.node, "status": s.status, "assignee": s.assignee, "due": s.due}
            for s in subtasks
        ],
        # 引擎视角的节点配置（最新 published）：任务书/表单/拆分与流转校验同源
        "nodeCfg": {
            "purpose": node_cfg.get("purpose", ""),
            "handler": node_cfg.get("handler", ""),
            "sla": node_cfg.get("sla", ""),
            "schema": node_cfg.get("schema") or [],
            "deliverable": node_cfg.get("deliverable") or {},
            "split": node_cfg.get("split") or {"mode": "off"},
        },
        # 回退目标必须来自该工作项冻结版本的画布，前端不能使用模板演示数据。
        "fallbackTargets": fallback_targets,
        "issueTargets": await TaskIssueService(session).targets_for(t),
        "issues": [TaskIssueService.brief(issue) for issue in await TaskIssueService(session).issues_for_task(t)],
        "issueSummary": await TaskIssueService(session).summary(t.wi_id, t.id),
        "nextTaskId": next_task_id,
        "expertRuns": [
            {"id": r.id, "status": r.status, "output": r.output or "", "error": r.error, "startedAt": r.started_at, "context": r.context or "", "parsed": r.parsed,
             "events": events_by_run.get(r.id, [])}
            for r in linked_runs
        ],
    })


@router.post("/{task_id}/issues")
async def create_task_issue(
    task_id: str, body: IssueCreateReq, user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("task:submit")
    task = (await session.execute(select(TaskItem).where(TaskItem.id == task_id).with_for_update())).scalar_one_or_none()
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    service = TaskIssueService(session)
    issue, notifications = await service.create(task, target_task_id=body.target_task_id, title=body.title,
        description=body.description, description_doc=body.description_doc, attachments=body.attachments, priority=body.priority, blocking=body.blocking, actor=user)
    await session.commit()
    if notifications:
        from flowhub_api.routes.notifications import publish_notification
        for notification in notifications:
            await publish_notification(notification)
    return ok({"issue": service.brief(issue)}, "问题已创建并分派处理")


@router.get("/{task_id}/issues")
async def list_task_issues(
    task_id: str, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)],
):
    task = await session.get(TaskItem, task_id)
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务的问题记录", http_status=403)
    service = TaskIssueService(session)
    issues = await service.issues_for_task(task)
    return ok({"items": [service.brief(issue) for issue in issues], "summary": await service.summary(task.wi_id, task.id)})


@router.post("/issues/{issue_id}/verify")
async def verify_issue(
    issue_id: str, body: IssueVerifyReq, user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("task:submit")
    issue = (await session.execute(select(WorkflowIssue).where(WorkflowIssue.id == issue_id).with_for_update())).scalar_one_or_none()
    if issue is None:
        raise BizError(BizCode.NOT_FOUND, "问题不存在")
    service = TaskIssueService(session)
    next_task, notifications = await service.verify(issue, body.passed, body.notes, user)
    await session.commit()
    if notifications:
        from flowhub_api.routes.notifications import publish_notification
        for notification in notifications:
            await publish_notification(notification)
    return ok({"issue": service.brief(issue), "nextTaskId": next_task.id if next_task else ""}, "验证通过，问题已关闭" if body.passed else "验证未通过，已重新分派处理")


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
        if f.get("required") and is_empty_form_value(values.get(f.get("key"))):
            raise BizError(BizCode.VALIDATION, f"「{f.get('label', f.get('key'))}」为必填，请补充")
    await WorkflowService(session).validate_image_references(
        project=t.project, wi_id=t.wi_id, schema=schema, form_values=values,
    )
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
    notifications: list[NotificationItem] = []
    if current is not None:
        # target_user 按 account 存储（与通知列表过滤一致：assignee 存的是 name，需换算）。
        # 追加信息同样通知该节点全部绑定处理人（多人节点每个共同处理人都收到通知/邮件）。
        from flowhub_api.services.workflow import WorkflowService as _WFS

        recipients = await _WFS(session).resolve_task_recipients(current)
        for cur_user in recipients:
            notification = NotificationItem(
                id=gen_id("ntf"), title="节点信息已补充",
                body=f"节点「{t.node}」由 {user.name} 补充了信息，处理前请查看（当前节点「{current.node}」）",
                time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=[],
                kind="info", unread=True, failed=False,
                target_user=cur_user.account, wi_id=t.wi_id, task_id=current.id,
            )
            session.add(notification)
            channels = await deliver_channels(
                "节点信息已补充", f"「{t.title}」节点「{t.node}」有补充信息，当前节点「{current.node}」请留意", cur_user,
            )
            notification.channels = channels
            notification.failed = any(not channel["ok"] for channel in channels)
            notifications.append(notification)
    await AuditService(session).record(
        actor=user.name, action="task:append", target=f"{t.id} · {t.node}", result="success",
    )
    await session.commit()
    if notifications:
        from flowhub_api.routes.notifications import publish_notification

        for notification in notifications:
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
    service = WorkflowService(session)
    project, tpl = await service.resolve_template_for_task(t)
    assignees: list[dict] = []
    if project and tpl:
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
    # 行锁加载：并发重复提交时第二个请求阻塞到第一个事务提交，再按最新状态命中幂等守卫 409
    # （否则两请求都读到未完成的旧状态，双双通过守卫产生重复流转）
    t = (await session.execute(
        select(TaskItem).where(TaskItem.id == task_id).with_for_update()
    )).scalar_one_or_none()
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")

    if (t.source or "").startswith("issue:") and body.action == "return":
        raise BizError(BizCode.VALIDATION, "问题处理任务不能使用主流程退回，请提交修复或验证结论")

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
        if (t.source or "").startswith("issue:") and not await TaskIssueService(session).can_act(t, user):
            raise BizError(BizCode.PERM_DENIED, "仅问题节点指定处理人可认领", http_status=403)
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
        # source 是问题闭环的类型标记；覆盖它会让后续 submit 误走主流程推进。
        if not (t.source or "").startswith("issue:"):
            t.source = f"{user.name}转办"
        await AuditService(session).record(
            actor=user.name, action="task:transfer", target=f"{t.id} → {target.name}", result="success",
        )
        from flowhub_api.models import NotificationItem
        from flowhub_api.seed.init import gen_id
        from flowhub_api.services.notify import deliver_channels

        channels = await deliver_channels("任务已转办", f"「{t.title}」节点「{t.node}」已由 {user.name} 转办给你", target)
        notification = NotificationItem(
            id=gen_id("ntf"), title="任务已转办", body=f"「{t.title}」节点「{t.node}」已转办给你",
            time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=channels, kind="transfer", unread=True,
            failed=any(not channel["ok"] for channel in channels), target_user=target.account, wi_id=t.wi_id, task_id=t.id,
        )
        session.add(notification)
        await session.commit()
        from flowhub_api.routes.notifications import publish_notification
        await publish_notification(notification)
        return ok({"task": _brief(t)}, f"任务已转办给 {target.name}")

    if body.action == "return":
        auth.require("task:return")
        if not body.to_node_id:
            raise BizError(BizCode.VALIDATION, "请选择回退目标节点")
        from flowhub_api.models import TemplateCanvas

        instance = (await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id == t.wi_id)
        )).scalar_one_or_none()
        canvas = await session.get(TemplateCanvas, f"{instance.template_id}:{instance.version}") if instance else None
        allowed_targets = {
            target_id for source_id, target_id in (canvas.fallbacks if canvas else [])
            if source_id == t.node_id
        }
        if body.to_node_id not in allowed_targets:
            raise BizError(BizCode.VALIDATION, "该回退目标未在当前流程版本的画布中配置")
        target_node = next((node for node in (canvas.nodes if canvas else []) if node.get("id") == body.to_node_id), None)
        if target_node is None:
            raise BizError(BizCode.VALIDATION, "回退目标节点不存在")
        t.status = "returned"
        # task.node 是展示名称，node_id 才是流程定位键；两者和实例游标必须一起更新。
        t.node_id = body.to_node_id
        t.node = target_node.get("label", body.to_node_id)
        instance.current_node = body.to_node_id
        await AuditService(session).record(
            actor=user.name, action="task:return", target=f"{t.id} → {t.node}", result="success",
        )
        await session.commit()
        return ok({"task": _brief(t)}, f"任务已回退至 {t.node}")

    if body.action == "submit":
        auth.require("task:submit")
        # Expert 自动节点：必须先批准 LangGraph Approval，禁止绕过受治理写入。
        if t.status == "pending_confirmation":
            raise BizError(
                BizCode.DUPLICATE_OPERATION,
                "Expert 正在自动处理该节点，完成后会自动采纳并流转，无需人工提交",
                http_status=409,
            )
        issue_service = TaskIssueService(session)
        if (t.source or "").startswith("issue:") and ":handling:" in (t.source or ""):
            issue, verification_task, notifications = await issue_service.complete_handling(
                t, body.form_values or {}, body.acceptance_checks or {}, user,
            )
            await session.commit()
            if notifications:
                from flowhub_api.routes.notifications import publish_notification
                for notification in notifications:
                    await publish_notification(notification)
            return ok({"task": _brief(t), "issue": issue_service.brief(issue), "next_task_id": verification_task.id,
                       "next_node": {"id": verification_task.node_id, "label": verification_task.node}, "next_assignees": []},
                      "问题修复已提交，已自动创建验证任务")
        if (t.source or "").startswith("issue:") and ":verify:" in (t.source or ""):
            raise BizError(BizCode.VALIDATION, "问题验证请使用“验证通过/不通过”操作")
        await issue_service.assert_origin_can_advance(t)
        t.form_values = body.form_values or {}
        # 验收清单勾选快照随提交落库（引擎在 advance 中强制全部勾选后才会流转）
        t.acceptance_checks = body.acceptance_checks or {}
        service = WorkflowService(session)
        # 被退回到开始节点后，重新提交的值要成为工作项的权威起始表单；否则详情、继承上下文
        # 仍会展示创建时的旧值，且与新建工作项的编辑体验不一致。
        project, template = await service.resolve_template_for_task(t)
        if template is not None:
            instance = (await session.execute(
                select(WorkflowInstance).where(WorkflowInstance.work_item_id == t.wi_id)
            )).scalar_one_or_none()
            nodes = await service.nodes_of(template, instance.version if instance else None)
            current_node = next((node for node in nodes if node.get("id") == t.node_id), None)
            if current_node and current_node.get("type") == "start":
                wi = await session.get(WorkItem, t.wi_id)
                if wi is not None:
                    wi.start_values = body.form_values or {}
                    title = str((body.form_values or {}).get("title") or "").strip()
                    if title:
                        wi.title = title
                        t.title = title
        result = await service.submit_and_advance(t, body.form_values, body.acceptance_checks, user)
        # Every newly-arrived human task gets a durable notification.  This is
        # deliberately handled here (after advance has selected all branches)
        # so normal, conditional and parallel paths share the same delivery.
        from flowhub_api.models import NotificationItem
        from flowhub_api.seed.init import gen_id
        from flowhub_api.services.notify import deliver_channels

        arrival_notifications: list[NotificationItem] = []
        seen_task_ids: set[str] = set()
        for next_task in result.get("tasks") or ([result.get("task")] if result.get("task") else []):
            if next_task is None or next_task.id in seen_task_ids or next_task.status in {"completed", "cancelled", "pending_confirmation"}:
                continue
            seen_task_ids.add(next_task.id)
            # 通知该节点全部绑定处理人（多人节点每个共同处理人都收到通知/邮件），
            # 而非只通知 TaskItem.assignee（单字符串只存绑定首位）。
            for recipient in await service.resolve_task_recipients(next_task):
                channels = await deliver_channels(
                    "新待办任务",
                    f"工作项「{next_task.title}」已流转至节点「{next_task.node}」，请处理任务 {next_task.id}",
                    recipient,
                )
                notification = NotificationItem(
                    id=gen_id("ntf"), title="新待办任务",
                    body=f"工作项「{next_task.title}」已流转至节点「{next_task.node}」",
                    time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=channels, kind="arrive",
                    unread=True, failed=any(not channel["ok"] for channel in channels),
                    target_user=recipient.account, wi_id=next_task.wi_id, task_id=next_task.id,
                )
                session.add(notification)
                arrival_notifications.append(notification)
        await session.commit()
        if arrival_notifications:
            from flowhub_api.routes.notifications import publish_notification
            for notification in arrival_notifications:
                await publish_notification(notification)
        nxt_task = result.get("task") or (result.get("tasks") or [None])[0]
        next_tasks = [
            {"id": x.id, "node": x.node, "status": x.status, "assignee": x.assignee}
            for x in (result.get("tasks") or ([nxt_task] if nxt_task else []))
        ]
        if result.get("parallel"):
            msg = f"提交成功：已并行拆分 {len(next_tasks)} 个分支任务"
        elif result.get("waiting_join"):
            msg = "提交成功：等待其他并行分支完成后自动汇合"
        elif result.get("next_node"):
            msg = "提交成功：已自动流转至下一节点并分配处理人"
        else:
            return ok({"task": _brief(t)}, "提交成功")
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

    if body.action == "request_info":
        t.status = "waiting_for_information"
        await AuditService(session).record(
            actor=user.name, action="task:request_info", target=f"{t.id} · {t.node}", result="success",
        )
        await session.commit()
        return ok({"task": _brief(t)}, "补充信息请求已发送：SLA 暂停计时")

    raise BizError(BizCode.VALIDATION, f"未知动作：{body.action}")


async def _resolve_task_template(session: AsyncSession, t: TaskItem) -> tuple[Project | None, GlobalTemplate | None]:
    """按任务所属的流程实例解析模板，避免项目新绑定覆盖历史流程。"""
    return await WorkflowService(session).resolve_template_for_task(t)


@router.post("/{task_id}/split")
async def split_task(
    task_id: str,
    body: TaskSplitReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """把当前任务拆分为多个子任务：父任务完成，子任务在下一节点独立流转。"""
    auth = build_authorizer(user)
    auth.require("task:submit")
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    if (t.source or "").startswith("issue:"):
        raise BizError(BizCode.VALIDATION, "问题处理任务不能拆分；请提交修复结果或验证结论")
    from flowhub_api.models import WorkItem as _WI

    if (await session.execute(select(_WI.id).where(_WI.id == t.wi_id, _WI.status == "archived"))).first():
        raise BizError(BizCode.FORBIDDEN, "流程已冻结（项目归档），不可拆分", http_status=403)
    if t.status in ("completed", "cancelled"):
        raise BizError(BizCode.DUPLICATE_OPERATION, "任务已处理，不可再拆分", http_status=409)
    # 权限：节点处理人 / 工作项创建人或负责人 / 系统管理员
    wi_row = await session.get(_WI, t.wi_id)
    allowed = (
        t.assignee == user.name
        or user.name == "系统管理员"
        or (wi_row is not None and user.name in (wi_row.creator, wi_row.assignee))
    )
    if not allowed:
        raise BizError(BizCode.PERM_DENIED, f"仅节点「{t.node}」的处理人或工作项负责人可拆分", http_status=403)
    project, tpl = await _resolve_task_template(session, t)
    if not project or not tpl:
        raise BizError(BizCode.VALIDATION, "未找到项目与模板绑定，无法拆分", http_status=422)
    service = WorkflowService(session)
    children = [c.model_dump() for c in body.children]
    created = await service.split_task(t, project, tpl, children, user, body.form_values)
    await session.commit()
    return ok({
        "parent": _brief(t),
        "children": [_brief(c) for c in created],
    }, f"已拆分为 {len(created)} 个子任务，均在下一节点独立流转")


async def _expert_fill_guard(session: AsyncSession, t: TaskItem, user: User) -> tuple[Project, GlobalTemplate, dict]:
    """Expert 填充/采纳共用守卫：冻结校验 + 状态校验 + 处理人权限 + 节点 schema/deployment 配置。"""
    from flowhub_api.models import WorkItem as _WI

    if (await session.execute(select(_WI.id).where(_WI.id == t.wi_id, _WI.status == "archived"))).first():
        raise BizError(BizCode.FORBIDDEN, "流程已冻结（项目归档），不可操作", http_status=403)
    if t.status in ("completed", "cancelled"):
        raise BizError(BizCode.DUPLICATE_OPERATION, "任务已处理，无需填充", http_status=409)
    wi_row = await session.get(_WI, t.wi_id)
    allowed = (
        t.assignee == user.name
        or user.name == "系统管理员"
        or (wi_row is not None and user.name in (wi_row.creator, wi_row.assignee))
    )
    if not allowed:
        raise BizError(BizCode.PERM_DENIED, f"仅节点「{t.node}」的处理人或工作项负责人可操作", http_status=403)
    project, tpl = await _resolve_task_template(session, t)
    if not project or not tpl:
        raise BizError(BizCode.VALIDATION, "未找到项目与模板绑定", http_status=422)
    service = WorkflowService(session)
    from flowhub_api.models import WorkflowInstance

    inst = (await session.execute(
        select(WorkflowInstance).where(WorkflowInstance.work_item_id == t.wi_id)
    )).scalar_one_or_none()
    cfg = await service._node_cfg_of(tpl, t.node_id, inst.version if inst else None) or {}
    if not (cfg.get("schema") or []):
        raise BizError(BizCode.VALIDATION, "该节点未配置表单字段，无需 AI 填充", http_status=422)
    if not ((cfg.get("expert") or {}).get("expertDeploymentId") or ""):
        raise BizError(BizCode.FLOW_VALIDATE, "该节点未绑定 Expert Deployment，无法 AI 填充；请在画布中绑定或手动填写", http_status=422)
    return project, tpl, cfg


@router.post("/{task_id}/ai-fill")
async def ai_fill_task(
    task_id: str,
    body: TaskAiFillReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Expert 协助填充（重新执行）：覆盖任务最新 Run 就地重跑（不新建记录）。

    任务到达时引擎已自动触发一次 Run——优先用「采纳」入口（adopt-run）复用其产出；本接口用于失败重试或重新生成。
    重跑为覆盖语义：原 Run 的状态/产出/上下文被新一轮执行替换（事件与挂起审批一并清空）；
    任务尚无 Run 时新建。body.context 为用户补充的执行上下文（可选）：拼入 prompt 引导本轮生成，并随 Run 落库便于追溯。
    立即返回 running 状态的 Run（后台执行），前端沿用任务详情轮询获取结果。
    """
    from flowhub_api.models import ExpertRun

    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    project, tpl, cfg = await _expert_fill_guard(session, t, user)
    latest = (await session.execute(
        select(ExpertRun).where(ExpertRun.task_id == t.id).order_by(ExpertRun.started_at.desc(), ExpertRun.id.desc()).limit(1)
    )).scalar_one_or_none()
    if latest is not None and latest.status in {"queued", "running", "interrupted"}:
        raise BizError(BizCode.LOCKED, "Expert Run 仍在执行中，请稍后再重跑", http_status=423)
    schema = cfg.get("schema") or []
    deployment_id = (cfg.get("expert") or {}).get("expertDeploymentId") or ""
    service = WorkflowService(session)
    brief = await service.build_task_brief(t, tpl)
    from flowhub_api.services.expert_runtime import build_schema_output_instruction, schedule_deployment_run

    instruction = build_schema_output_instruction(schema)
    context = body.context.strip()
    prompt = brief + ("\n\n## 用户补充执行上下文\n" + context if context else "") + ("\n\n" + instruction if instruction else "")
    # 后台执行：请求只登记 running Run 即返回，避免被 30s+ 模型调用阻塞（与自动节点同机制）
    run = await schedule_deployment_run(
        session, deployment_id, prompt, user, task_id=t.id, context=context,
        replace_run_id=latest.id if latest is not None else None,
    )
    run.config_snapshot = {**run.config_snapshot, "forceCodeReanalysis": body.reanalyze_code}
    await AuditService(session).record(
        actor=user.name, action="task:ai_fill", target=f"{t.id} · {t.node}", result="success",
        after={"runId": run.id, "replaced": latest.id if latest is not None else "", "contextLen": len(context), "reRun": True, "reanalyzeCode": body.reanalyze_code},
    )
    await session.commit()
    return ok(
        {"run": {"id": run.id, "status": run.status, "output": "", "error": "", "startedAt": run.started_at, "context": context}},
        "Expert 已重新发起执行，请稍候（轮询任务详情获取结果）",
    )


@router.post("/{task_id}/adopt-run")
async def adopt_run(
    task_id: str,
    body: TaskAdoptRunReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """采纳 Expert Run 产出：把指定 run 的输出解析为节点表单值（upload 字段自动生成文档），回填表单供人审核后提交。

    协助节点的标准路径：任务到达时引擎已自动创建 Run → 任务页展示 Run 状态 → 成功后点「采纳」回填，无需重复触发模型。
    body.normalize=true 时先做 AI 二次格式修正（只调格式不改内容，结构性字段程序校验兜底）；
    修正结果带 normalized 标记写回 run.parsed 快照（幂等：重复采纳不重复调模型），修正失败自动降级为原解析采纳。"""
    from flowhub_api.models import ExpertRun

    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    _, _, cfg = await _expert_fill_guard(session, t, user)
    run = (await session.execute(
        select(ExpertRun).where(ExpertRun.id == body.run_id, ExpertRun.task_id == t.id)
    )).scalar_one_or_none()
    if run is None:
        raise BizError(BizCode.NOT_FOUND, "Run 不存在或不属于该任务")
    if run.status == "running":
        raise BizError(BizCode.LOCKED, "Expert Run 仍在执行中，请稍后再采纳", http_status=423)
    if run.status != "succeeded":
        raise BizError(BizCode.FLOW_VALIDATE, f"Run 状态为 {run.status}，无可采纳产出；可点击「重新生成」重试")
    schema = cfg.get("schema") or []
    schema_keys = {f.get("key", "") for f in schema}
    normalized = False
    manual_edited = False
    diff_fields: list[str] = []
    snapshot = run.parsed if isinstance(run.parsed, dict) else None
    # 人工修改优先：body.values 非空 = 用户在抽屉中直接编辑过产出，按编辑值覆盖快照采纳（跳过 AI 格式修正）。
    # 只接受 schema 内字段；upload 字段的人工改动与文档生成逻辑冲突（正文→文档引用由采纳流程生成），忽略之
    if body.values:
        manual = {k: v for k, v in body.values.items()
                  if k in schema_keys and next((f.get("type") for f in schema if f.get("key") == k), "") not in ("upload", "file", "image")}
        if manual:
            from flowhub_api.services.expert_runtime import schema_validation_issues

            base = snapshot.get("values") if isinstance(snapshot, dict) else None
            # originalValues 保留「采纳前」的原始解析值（首次覆盖时记录，后续编辑不回写），供 diff 展示
            prev_original = snapshot.get("originalValues") if isinstance(snapshot, dict) else None
            before = dict(prev_original) if isinstance(prev_original, dict) else dict(base or {})
            edited_keys = sorted(k for k in manual if manual.get(k) != (base or {}).get(k))
            edited_values = {**(base or {}), **manual}
            validation_issues = schema_validation_issues(schema, edited_values)
            run.parsed = {**(snapshot or {}), "values": edited_values,
                          "warnings": list(snapshot.get("warnings") or []) if isinstance(snapshot, dict) else [],
                          "valid": not validation_issues, "validationIssues": validation_issues,
                          "formatStatus": "manual",
                          "originalValues": before, "manualEdited": True,
                          "editedKeys": edited_keys,
                          "editedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
            if body.approve_quality_override and run.parsed.get("qualityStatus") not in (None, "passed"):
                from flowhub_api.services.runtime_quality import content_hash
                import json

                manual_hash = content_hash(json.dumps(edited_values, ensure_ascii=False, sort_keys=True, default=str))
                run.quality_result = {
                    "status": "passed",
                    "issues": [],
                    "contentHash": manual_hash,
                    "overriddenBy": user.id,
                    "overriddenAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                run.parsed = {**run.parsed, "qualityReview": {
                    "status": run.parsed.get("qualityStatus"), "issues": list(run.parsed.get("qualityIssues") or []),
                    "overriddenBy": user.id, "overriddenAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }, "qualityStatus": "passed", "qualityIssues": [], "contentHash": manual_hash}
            snapshot = run.parsed
            manual_edited = True
            diff_fields = edited_keys
    # AI 二次格式修正：把产出按节点 schema 契约规范后写回 parsed 快照（normalized 标记保证幂等）。
    # 任何失败都降级为原快照采纳——采纳动作永不因格式修正而失败；人工编辑过时跳过（编辑值即最终格式）
    if body.normalize and not manual_edited and snapshot is not None and snapshot.get("formatStatus") != "invalid":
        if snapshot.get("normalized"):
            normalized = True  # 已规范化快照直接复用（幂等，不再调模型）
        else:
            from flowhub_api.services.expert_runtime import normalize_run_output

            try:
                from flowhub_api.services.expert_runtime import schema_validation_issues

                n_values, n_warnings = await normalize_run_output(session, run, schema)
                before = snapshot.get("values") or {}
                diff_fields = [k for k in n_values if n_values.get(k) != before.get(k)]
                validation_issues = schema_validation_issues(schema, n_values)
                run.parsed = {**snapshot, "values": n_values, "warnings": n_warnings, "originalValues": before,
                              "valid": not validation_issues, "validationIssues": validation_issues,
                              "formatStatus": "valid",
                              "qualityStatus": run.quality_result.get("status", "needs_human_review"),
                              "qualityIssues": list(run.quality_result.get("issues") or []),
                              "contentHash": run.quality_result.get("contentHash", ""),
                              "normalized": True, "formattedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
                snapshot = run.parsed
                normalized = True
            except Exception as exc:  # noqa: BLE001 — 修正失败降级，不阻塞采纳
                logger.warning("adopt-run 格式修正失败（降级原解析采纳）run=%s: %s", run.id, exc)
    service = WorkflowService(session)
    values, warnings = await service.fill_task_from_run(t, run, cfg, user)
    if not values:
        raise BizError(BizCode.FLOW_VALIDATE, warnings[0] if warnings else "Run 产出未能解析出表单值；可点击「重新生成」重试", http_status=422)
    # 幂等：采纳后把快照里 upload 字段的正文替换为文档引用，重复采纳不再重复生成文档
    if isinstance(run.parsed, dict):
        parsed_values = dict(run.parsed.get("values") or {})
        changed = False
        for f in schema:
            key, ftype = f.get("key", ""), f.get("type", "")
            if ftype in ("upload", "file") and isinstance(parsed_values.get(key), str) and isinstance(values.get(key), list):
                parsed_values[key] = values[key]
                changed = True
        if changed:
            run.parsed = {**run.parsed, "values": parsed_values}
    await AuditService(session).record(
        actor=user.name, action="task:adopt_run", target=f"{t.id} · {t.node}", result="success",
        after={"runId": run.id, "fields": list(values.keys()), "warnings": warnings[:5],
               "normalized": normalized, "diffFields": diff_fields[:10], "qualityOverride": bool(body.approve_quality_override and manual_edited)},
    )
    await session.commit()
    msg = "已按编辑内容采纳，请审核后提交" if manual_edited else ("已采纳 Expert 产出（经 AI 格式规范），请审核后提交" if normalized else "已采纳 Expert 产出，请审核后提交")
    return ok({"values": values, "warnings": warnings, "runId": run.id, "normalized": normalized, "manualEdited": manual_edited}, msg)


@router.post("/{task_id}/split-suggest")
async def split_suggest(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """AI 拆分建议：用节点绑定的 Expert Deployment 模型 + 任务书生成结构化建议（人确认后才创建）。"""
    t = await session.get(TaskItem, task_id)
    if t is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    project, tpl = await _resolve_task_template(session, t)
    if not project or not tpl:
        raise BizError(BizCode.VALIDATION, "未找到项目与模板绑定", http_status=422)
    service = WorkflowService(session)
    from flowhub_api.models import WorkflowInstance

    inst = (await session.execute(
        select(WorkflowInstance).where(WorkflowInstance.work_item_id == t.wi_id)
    )).scalar_one_or_none()
    cfg = await service._node_cfg_of(tpl, t.node_id, inst.version if inst else None) or {}
    if (cfg.get("split") or {}).get("mode", "off") not in ("ai_assist", "ai_auto"):
        raise BizError(BizCode.VALIDATION, "该节点未开启 AI 拆分建议（请在画布中配置拆分模式）", http_status=422)
    # 模型来源：节点绑定的 Expert Deployment 固定版本中的 provider model
    deployment_id = ((cfg.get("expert") or {}).get("expertDeploymentId")) or ""
    provider_model_id = ""
    if deployment_id:
        from flowhub_api.models import ExpertDeployment, ExpertVersion

        dep = await session.get(ExpertDeployment, deployment_id)
        ver = await session.get(ExpertVersion, dep.expert_version_id) if dep else None
        provider_model_id = (ver.provider_model_id or "") if ver else ""
    if not provider_model_id:
        raise BizError(BizCode.FLOW_VALIDATE, "该节点未绑定可用的 Expert Deployment 模型；请先在画布中绑定或手动填写拆分", http_status=422)
    brief = await service.build_task_brief(t, tpl)
    instruction = (
        brief
        + "\n\n## 本轮指令\n基于以上任务书判断本节点应拆分为哪些子任务。"
        + "只输出一个 JSON 数组，不要输出其他文字，格式："
        + '[{"title":"子任务标题(<=30字)","note":"该子任务的具体要求说明","assignee_hint":"建议负责人或角色"}]'
    )
    from flowhub_api.services.expert_runtime import run_native_flowhub_chat

    raw, _trace = await run_native_flowhub_chat(session, instruction, user, provider_model_id)
    import json as _json
    import re as _re

    proposals: list[dict] = []
    parse_error = ""
    try:
        match = _re.search(r"\[.*\]", raw, _re.S)
        parsed = _json.loads(match.group(0)) if match else []
        proposals = [
            {"title": str(p.get("title", ""))[:120], "note": str(p.get("note", "")), "assigneeHint": str(p.get("assignee_hint", ""))}
            for p in parsed if isinstance(p, dict) and str(p.get("title", "")).strip()
        ]
        if not proposals:
            parse_error = "模型未返回有效拆分项"
    except Exception:  # noqa: BLE001
        parse_error = "模型返回内容无法解析为 JSON"
    await AuditService(session).record(actor=user.name, action="task:split_suggest", target=t.id, result="success")
    await session.commit()
    return ok({"proposals": proposals, "raw": raw if parse_error else "", "parseError": parse_error})


@router.post("/{task_id}/reparse-attachments")
async def reparse_attachments(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """失败重试：仅重跑附件解析与证据注入，不调用模型、不写 Run。"""
    task = await session.get(TaskItem, task_id)
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务", http_status=403)
    from flowhub_api.services.attachment_evidence import build_attachment_evidence

    evidence = await build_attachment_evidence(session, task, user, task.brief or task.title)
    await AuditService(session).record(
        actor=user.name, action="attachment:reparse", target=f"{task.id} · 附件重试解析", result="success",
    )
    await session.commit()
    return ok({
        "attachments": [{
            "id": p.doc_id, "status": p.status, "parser": p.parser, "cacheHit": p.cache_hit,
            "durationMs": p.duration_ms, "entriesOrPages": p.entries_or_pages, "error": p.error,
        } for p in evidence.parsed],
        "injected": [{
            "docName": c.doc_name, "location": c.location, "seq": c.seq,
            "kind": c.kind, "text": c.text,
        } for c in evidence.injected],
        "durationMs": evidence.duration_ms,
    }, "附件解析完成")
