"""Generic, local rework loops for workflow tasks.

Issue tasks deliberately never call WorkflowService.advance: they are attached
to the current work item for visibility, while the main workflow cursor stays
where the issue was raised.
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import NotificationItem, TaskItem, User, WorkItem, WorkflowIssue, WorkflowInstance
from flowhub_api.services.workflow import WorkflowService, is_empty_form_value
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.notify import deliver_channels


OPEN_ISSUE_STATUSES = ("handling", "waiting_verification")


class TaskIssueService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.workflow = WorkflowService(session)

    @staticmethod
    def brief(issue: WorkflowIssue) -> dict:
        return {
            "id": issue.id, "wiId": issue.wi_id, "sourceTaskId": issue.source_task_id,
            "sourceNode": issue.source_node, "targetNode": issue.target_node,
            "targetTaskId": issue.target_task_id, "handlerTaskId": issue.handler_task_id,
            "verificationTaskId": issue.verification_task_id, "title": issue.title,
            "description": issue.description, "priority": issue.priority, "blocking": issue.blocking,
            "status": issue.status, "reporter": issue.reporter, "verificationNotes": issue.verification_notes,
            "round": issue.round, "createdAt": issue.created_at, "updatedAt": issue.updated_at,
        }

    async def can_act(self, task: TaskItem, user: User) -> bool:
        if any(role.id in {"system_admin", "organization_admin"} for role in user.roles):
            return True
        recipients = await self.workflow.resolve_task_recipients(task)
        return any(candidate.id == user.id for candidate in recipients) or task.assignee == user.name

    async def targets_for(self, task: TaskItem) -> list[dict]:
        """Return completed, non-issue work of this instance as rework anchors."""
        ancestors = await self._ancestor_node_ids(task)
        if not ancestors:
            return []
        lineage = TaskItem.lineage_root_id == task.lineage_root_id if task.lineage_root_id else TaskItem.lineage_root_id.is_(None)
        rows = (await self.session.execute(select(TaskItem).where(
            TaskItem.wi_id == task.wi_id, TaskItem.status == "completed", TaskItem.id != task.id,
            lineage, TaskItem.node_id.in_(ancestors), or_(TaskItem.source.is_(None), ~TaskItem.source.like("issue:%")),
        ).order_by(TaskItem.id.desc()))).scalars().all()
        unique: set[str] = set()
        result: list[dict] = []
        for row in rows:
            if not row.node_id or row.node_id in unique:
                continue
            unique.add(row.node_id)
            recipients = await self.workflow.resolve_task_recipients(row)
            result.append({"taskId": row.id, "nodeId": row.node_id, "label": row.node, "assignees": [user.name for user in recipients]})
        return result

    async def _ancestor_node_ids(self, task: TaskItem) -> set[str]:
        project, tpl = await self.workflow.resolve_template_for_task(task)
        instance = (await self.session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
        if project is None or tpl is None or instance is None:
            return set()
        reverse: dict[str, set[str]] = {}
        for source, target in await self.workflow._edges_of(tpl, instance.version):
            reverse.setdefault(target, set()).add(source)
        pending, ancestors = list(reverse.get(task.node_id, set())), set()
        while pending:
            node = pending.pop()
            if node in ancestors:
                continue
            ancestors.add(node)
            pending.extend(reverse.get(node, set()))
        return ancestors

    async def _assert_active_work_item(self, wi_id: str, *, allow_closed: bool = False) -> None:
        wi = await self.session.get(WorkItem, wi_id)
        instance = (await self.session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == wi_id))).scalar_one_or_none()
        terminal = wi is None or wi.status in {"archived", "cancelled"} or instance is None or instance.state == "cancelled"
        if terminal or (not allow_closed and (wi.status == "closed" or instance.state != "running")):
            raise BizError(BizCode.FORBIDDEN, "工作项当前不允许创建或处理问题", http_status=403)

    async def issues_for_task(self, task: TaskItem) -> list[WorkflowIssue]:
        return (await self.session.execute(select(WorkflowIssue).where(or_(
            WorkflowIssue.source_task_id == task.id,
            WorkflowIssue.handler_task_id == task.id,
            WorkflowIssue.verification_task_id == task.id,
        )).order_by(WorkflowIssue.created_at.desc()))).scalars().all()

    async def summary(self, wi_id: str, source_task_id: str | None = None) -> dict:
        stmt = select(WorkflowIssue).where(WorkflowIssue.wi_id == wi_id)
        if source_task_id:
            stmt = stmt.where(WorkflowIssue.source_task_id == source_task_id)
        rows = (await self.session.execute(stmt)).scalars().all()
        open_rows = [row for row in rows if row.status in OPEN_ISSUE_STATUSES]
        return {"total": len(rows), "open": len(open_rows), "blocking": sum(1 for row in open_rows if row.blocking),
                "waitingVerification": sum(1 for row in open_rows if row.status == "waiting_verification")}

    async def assert_origin_can_advance(self, task: TaskItem) -> None:
        blocking = (await self.session.execute(select(WorkflowIssue.id).where(
            WorkflowIssue.source_task_id == task.id,
            WorkflowIssue.blocking == True,  # noqa: E712
            WorkflowIssue.status.in_(OPEN_ISSUE_STATUSES),
        ).limit(1))).scalar_one_or_none()
        if blocking:
            raise BizError(BizCode.DUPLICATE_OPERATION, "存在未关闭的阻断问题，完成验证后才能提交该节点", http_status=409)

    async def create(self, source: TaskItem, *, target_task_id: str, title: str, description: str,
                     priority: str, blocking: bool | None, actor: User) -> tuple[WorkflowIssue, list[NotificationItem]]:
        source = (await self.session.execute(select(TaskItem).where(TaskItem.id == source.id).with_for_update())).scalar_one_or_none() or source
        await self._assert_active_work_item(source.wi_id)
        if source.status in ("completed", "cancelled"):
            raise BizError(BizCode.VALIDATION, "历史任务不能发起问题")
        if not await self.can_act(source, actor):
            raise BizError(BizCode.PERM_DENIED, "仅当前节点处理人可发起问题", http_status=403)
        allowed_targets = {row["taskId"] for row in await self.targets_for(source)}
        target = await self.session.get(TaskItem, target_task_id)
        if target_task_id not in allowed_targets or target is None or target.wi_id != source.wi_id or target.status != "completed" or (target.source or "").startswith("issue:"):
            raise BizError(BizCode.VALIDATION, "只能选择当前工作项已完成的前置节点")
        if source.lineage_root_id != target.lineage_root_id:
            raise BizError(BizCode.VALIDATION, "只能选择当前并行子线已完成的前置节点")
        # P0/P1 默认会保护主流程；提报人仍能明确改成非阻断问题。
        effective_blocking = priority in {"P0", "P1"} if blocking is None else blocking
        now = datetime.now(UTC).isoformat()
        issue = WorkflowIssue(
            id=gen_id("iss"), wi_id=source.wi_id, source_task_id=source.id,
            source_node_id=source.node_id, source_node=source.node,
            target_task_id=target.id, target_node_id=target.node_id, target_node=target.node,
            title=title, description=description, priority=priority, blocking=effective_blocking,
            status="handling", reporter=actor.name, created_at=now, updated_at=now,
        )
        self.session.add(issue)
        await self.session.flush()
        handler, notifications = await self._spawn_handler(issue, source, target, actor)
        issue.handler_task_id = handler.id
        await AuditService(self.session).record(actor=actor.name, action="issue:create", target=f"{issue.id} · {title}", result="success", after={"target": target.node, "blocking": effective_blocking})
        return issue, notifications

    async def _spawn_handler(self, issue: WorkflowIssue, source: TaskItem, target: TaskItem, actor: User) -> tuple[TaskItem, list[NotificationItem]]:
        project, tpl = await self.workflow.resolve_template_for_task(source)
        recipients = await self.workflow.resolve_task_recipients(target)
        assignee = recipients[0].name if recipients else target.assignee
        task = TaskItem(
            id=self.workflow.next_task_id(), wi_id=source.wi_id, title=f"[问题处理] {issue.title}", project=source.project,
            node=target.node, node_id=target.node_id, type=source.type, priority=issue.priority,
            status="assigned", assignee=assignee or "待分配", due=(datetime.now(UTC) + timedelta(hours=48)).strftime("%m-%d %H:%M"),
            sla_hours=48, source=f"issue:{issue.id}:handling:{issue.round}", parent_task_id=source.id,
            lineage_root_id=source.lineage_root_id, brief=f"问题 #{issue.id}\n来源节点：{source.node}\n问题描述：{issue.description}",
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        self.session.add(task)
        await self.session.flush()
        notifications = await self._notify(recipients, task, "问题待处理", f"问题「{issue.title}」已投递至节点「{target.node}」")
        return task, notifications

    async def complete_handling(self, task: TaskItem, form_values: dict, acceptance_checks: dict, actor: User) -> tuple[WorkflowIssue, TaskItem, list[NotificationItem]]:
        issue = (await self.session.execute(select(WorkflowIssue).where(
            WorkflowIssue.handler_task_id == task.id, WorkflowIssue.status == "handling",
        ).with_for_update())).scalar_one_or_none()
        if issue is None:
            raise BizError(BizCode.NOT_FOUND, "问题处理记录不存在")
        if not await self.can_act(task, actor):
            raise BizError(BizCode.PERM_DENIED, "仅问题处理人可提交修复", http_status=403)
        # 非阻断问题可能在主流程关闭后才完成修复；只要工作项未取消/归档，闭环仍可继续。
        await self._assert_active_work_item(task.wi_id, allow_closed=True)
        source = await self.session.get(TaskItem, issue.source_task_id)
        if source is None:
            raise BizError(BizCode.NOT_FOUND, "原问题任务不存在")
        project, template = await self.workflow.resolve_template_for_task(task)
        instance = (await self.session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
        cfg = await self.workflow._node_cfg_of(template, task.node_id, instance.version if instance else None) if template else {}
        for field in (cfg or {}).get("schema") or []:
            if field.get("required") and is_empty_form_value((form_values or {}).get(field.get("key", ""))):
                raise BizError(BizCode.VALIDATION, f"「{field.get('label', field.get('key'))}」为必填")
        acceptance = self.workflow.deliverable_of(cfg).get("acceptance") or []
        missing = [item.get("text", "") for item in acceptance
                   if not (acceptance_checks or {}).get(item.get("key", ""), {}).get("checked")]
        if missing:
            raise BizError(BizCode.VALIDATION, f"验收标准未全部确认：{'；'.join(missing)}")
        await self.workflow.validate_image_references(project=task.project, wi_id=task.wi_id, schema=(cfg or {}).get("schema") or [], form_values=form_values or {})
        task.form_values, task.acceptance_checks, task.status = form_values or {}, acceptance_checks or {}, "completed"
        issue.status, issue.updated_at = "waiting_verification", datetime.now(UTC).isoformat()
        verifier = (await self.session.execute(select(User).where(User.name == issue.reporter))).scalar_one_or_none()
        assignee = verifier.name if verifier and verifier.status == "active" else source.assignee
        verify = TaskItem(
            id=self.workflow.next_task_id(), wi_id=source.wi_id, title=f"[问题验证] {issue.title}", project=source.project,
            node=source.node, node_id=source.node_id, type=source.type, priority=issue.priority,
            status="assigned", assignee=assignee, due=(datetime.now(UTC) + timedelta(hours=24)).strftime("%m-%d %H:%M"),
            sla_hours=24, source=f"issue:{issue.id}:verify:{issue.round}", parent_task_id=task.id,
            lineage_root_id=source.lineage_root_id, brief=f"请验证问题 #{issue.id} 的修复结果。\n问题：{issue.description}\n修复任务：{task.id}",
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        self.session.add(verify)
        await self.session.flush()
        issue.verification_task_id = verify.id
        recipients = [verifier] if verifier and verifier.status == "active" else await self.workflow.resolve_task_recipients(source)
        notifications = await self._notify(recipients, verify, "问题待验证", f"问题「{issue.title}」已修复，请回归验证")
        await AuditService(self.session).record(actor=actor.name, action="issue:resolve", target=f"{issue.id} · {issue.title}", result="success")
        return issue, verify, notifications

    async def verify(self, issue: WorkflowIssue, passed: bool, notes: str, actor: User) -> tuple[TaskItem | None, list[NotificationItem]]:
        issue = (await self.session.execute(select(WorkflowIssue).where(WorkflowIssue.id == issue.id).with_for_update())).scalar_one_or_none() or issue
        await self._assert_active_work_item(issue.wi_id, allow_closed=True)
        verify_task = await self.session.get(TaskItem, issue.verification_task_id) if issue.verification_task_id else None
        if verify_task is None or issue.status != "waiting_verification":
            raise BizError(BizCode.VALIDATION, "该问题当前不在待验证状态")
        if not await self.can_act(verify_task, actor):
            raise BizError(BizCode.PERM_DENIED, "仅验证任务处理人可提交结论", http_status=403)
        verify_task.status, verify_task.form_values = "completed", {"passed": passed, "notes": notes}
        issue.verification_notes, issue.updated_at = notes, datetime.now(UTC).isoformat()
        if passed:
            issue.status = "closed"
            await AuditService(self.session).record(actor=actor.name, action="issue:verify", target=f"{issue.id} · {issue.title}", result="success", after={"passed": True})
            return None, []
        issue.round += 1
        issue.status = "handling"
        source, target = await self.session.get(TaskItem, issue.source_task_id), await self.session.get(TaskItem, issue.target_task_id)
        if source is None or target is None:
            raise BizError(BizCode.NOT_FOUND, "问题关联任务不存在")
        handler, notifications = await self._spawn_handler(issue, source, target, actor)
        issue.handler_task_id, issue.verification_task_id = handler.id, None
        await AuditService(self.session).record(actor=actor.name, action="issue:verify", target=f"{issue.id} · {issue.title}", result="success", after={"passed": False, "round": issue.round})
        return handler, notifications

    async def _notify(self, recipients: list[User], task: TaskItem, title: str, body: str) -> list[NotificationItem]:
        rows: list[NotificationItem] = []
        for recipient in recipients:
            channels = await deliver_channels(title, body, recipient)
            row = NotificationItem(id=gen_id("ntf"), title=title, body=body, time=datetime.now(UTC).strftime("%m-%d %H:%M"),
                channels=channels, kind="arrive", unread=True, failed=any(not item["ok"] for item in channels),
                target_user=recipient.account, wi_id=task.wi_id, task_id=task.id)
            self.session.add(row)
            rows.append(row)
        return rows
