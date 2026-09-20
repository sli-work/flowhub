"""Immutable correction proposals for tasks that have already flowed onward."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import NotificationItem, TaskAppend, TaskCorrection, TaskItem, User, WorkItem
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.notify import deliver_channels
from flowhub_api.services.workflow import WorkflowService, is_empty_form_value


class TaskCorrectionService:
    def __init__(self, session):
        self.session = session
        self.workflow = WorkflowService(session)

    @staticmethod
    def brief(row: TaskCorrection, *, can_review: bool | None = None) -> dict:
        return {"id": row.id, "taskId": row.task_id, "changes": row.changes or {}, "reason": row.reason,
                "suggestedMode": row.suggested_mode, "appliedMode": row.applied_mode, "source": row.source,
                "proposer": row.proposer, "status": row.status, "reviewer": row.reviewer,
                "reviewNotes": row.review_notes, "createdAt": row.created_at, "updatedAt": row.updated_at,
                **({"canReview": can_review} if can_review is not None else {})}

    async def _schema(self, task: TaskItem) -> list[dict]:
        _, template = await self.workflow.resolve_template_for_task(task)
        if template is None:
            return []
        from flowhub_api.models import WorkflowInstance
        instance = (await self.session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
        return (await self.workflow._node_cfg_of(template, task.node_id, instance.version if instance else None) or {}).get("schema") or []

    async def propose(self, task: TaskItem, *, changes: dict, reason: str, suggested_mode: str, actor: User, source: str) -> TaskCorrection:
        if task.status != "completed":
            raise BizError(BizCode.VALIDATION, "只能更正已提交完成的任务")
        if not isinstance(changes, dict) or not changes:
            raise BizError(BizCode.VALIDATION, "至少需要提交一项更正字段")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 4000:
            raise BizError(BizCode.VALIDATION, "更正原因不能为空且不能超过 4000 字")
        if suggested_mode not in {"append", "rework"}:
            raise BizError(BizCode.VALIDATION, "更正方式必须为 append 或 rework")
        wi = await self.session.get(WorkItem, task.wi_id)
        if wi is None or wi.status in {"archived", "cancelled"}:
            raise BizError(BizCode.FORBIDDEN, "流程已冻结或取消，不能提交更正", http_status=403)
        if wi.status == "closed" and suggested_mode == "rework":
            raise BizError(BizCode.FORBIDDEN, "已关闭流程只能提交补充更正，不能创建返工", http_status=403)
        schema = await self._schema(task)
        allowed = {str(field.get("key") or "") for field in schema}
        invalid = set(changes) - allowed
        if invalid:
            raise BizError(BizCode.VALIDATION, f"包含不属于该节点表单的字段：{', '.join(sorted(invalid))}")
        candidate = {**(task.form_values or {}), **changes}
        for field in schema:
            if field.get("required") and is_empty_form_value(candidate.get(field.get("key", ""))):
                raise BizError(BizCode.VALIDATION, f"更正后「{field.get('label', field.get('key'))}」为必填")
        await self.workflow.validate_image_references(
            project=task.project, wi_id=task.wi_id, schema=schema, form_values=changes, uploader=actor.name,
        )
        await self.workflow.validate_file_references(
            wi_id=task.wi_id, schema=schema, form_values=changes, uploader=actor.name,
        )
        now = datetime.now(UTC).isoformat()
        row = TaskCorrection(id=gen_id("cor"), wi_id=task.wi_id, task_id=task.id,
                             original_values=dict(task.form_values or {}), changes=dict(changes), reason=reason.strip(),
                             suggested_mode=suggested_mode, source=source, proposer_id=actor.id, proposer=actor.name,
                             status="pending_review", created_at=now, updated_at=now)
        self.session.add(row)
        await AuditService(self.session).record(actor=actor.name, action="task:correction_propose", target=f"{task.id} · {task.node}", result="success", after={"correctionId": row.id, "source": source, "fields": sorted(changes)})
        return row

    async def review_notifications(self, row: TaskCorrection, task: TaskItem, actor: User) -> list[NotificationItem]:
        """Create one actionable notification for each eligible independent reviewer."""
        recipients = [candidate for candidate in await self.workflow.resolve_task_recipients(task) if candidate.id != actor.id]
        if not recipients:
            users = (await self.session.execute(select(User).where(User.status == "active", User.deleted.is_(False)))).scalars().all()
            recipients = [candidate for candidate in users if candidate.id != actor.id and any(
                role.id in {"system_admin", "organization_admin"} for role in candidate.roles
            )]
        unique = {candidate.id: candidate for candidate in recipients}
        title = "待审核：已提交内容更正"
        body = f"「{task.title}」的节点「{task.node}」收到更正提案，请审核后决定是否生效。"
        notifications: list[NotificationItem] = []
        for recipient in unique.values():
            channels = await deliver_channels(title, body, recipient)
            notification = NotificationItem(
                id=gen_id("ntf"), title=title, body=body, time=datetime.now(UTC).strftime("%m-%d %H:%M"),
                channels=channels, kind="info", unread=True, failed=any(not channel["ok"] for channel in channels),
                target_user=recipient.account, wi_id=task.wi_id, task_id=task.id, correction_id=row.id,
            )
            self.session.add(notification)
            notifications.append(notification)
        return notifications

    async def can_review(self, row: TaskCorrection, user: User) -> bool:
        if user.id == row.proposer_id or (not row.proposer_id and user.name == row.proposer):
            return False
        if any(role.id in {"system_admin", "organization_admin"} for role in user.roles):
            return True
        task = await self.session.get(TaskItem, row.task_id)
        if task is None:
            return False
        recipients = await self.workflow.resolve_task_recipients(task)
        return any(candidate.id == user.id for candidate in recipients)

    async def can_act(self, task: TaskItem, user: User) -> bool:
        if any(role.id in {"system_admin", "organization_admin"} for role in user.roles):
            return True
        recipients = await self.workflow.resolve_task_recipients(task)
        return task.assignee == user.name or any(candidate.id == user.id for candidate in recipients)

    async def review(self, row: TaskCorrection, *, approve: bool, mode: str, notes: str, actor: User) -> TaskCorrection:
        if row.status != "pending_review":
            raise BizError(BizCode.DUPLICATE_OPERATION, "该更正提案已处理")
        if not await self.can_review(row, actor):
            raise BizError(BizCode.PERM_DENIED, "仅原节点处理人或管理员可审批，且不能审批自己的提案")
        wi = await self.session.get(WorkItem, row.wi_id)
        if wi is None or wi.status in {"archived", "cancelled"} or (wi.status == "closed" and approve and mode == "rework"):
            raise BizError(BizCode.FORBIDDEN, "当前流程状态不允许处理该更正", http_status=403)
        now = datetime.now(UTC).isoformat()
        row.reviewer, row.review_notes, row.updated_at = actor.name, notes, now
        if not approve:
            row.status = "rejected"
        elif mode == "append":
            task = await self.session.get(TaskItem, row.task_id)
            if task is None:
                raise BizError(BizCode.NOT_FOUND, "原任务不存在")
            append = TaskAppend(id=gen_id("app"), task_id=task.id, node_id=task.node_id, wi_id=task.wi_id,
                                appender=f"更正审批({actor.name})", time=datetime.now(UTC).strftime("%m-%d %H:%M"), values=row.changes)
            self.session.add(append)
            await self.session.flush()
            row.status, row.applied_mode, row.append_id = "applied", "append", append.id
        else:
            task = await self.session.get(TaskItem, row.task_id)
            if task is None:
                raise BizError(BizCode.NOT_FOUND, "原任务不存在")
            recipients = await self.workflow.resolve_task_recipients(task)
            assignee = recipients[0].name if recipients else task.assignee
            handler = TaskItem(
                id=self.workflow.next_task_id(), wi_id=task.wi_id, title=f"[更正返工] {task.title}",
                project=task.project, node=task.node, node_id=task.node_id, type=task.type, priority=task.priority,
                status="assigned", assignee=assignee or "待分配",
                due=(datetime.now(UTC) + timedelta(hours=48)).strftime("%m-%d %H:%M"), sla_hours=48,
                source=f"correction:{row.id}:handling", parent_task_id=task.id, lineage_root_id=task.lineage_root_id,
                brief=f"更正提案 #{row.id} 已获批准，请重新核对并提交完整表单。\n原因：{row.reason}",
                form_values={**(task.form_values or {}), **(row.changes or {})},
                created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            )
            self.session.add(handler)
            row.status, row.applied_mode = "rework_assigned", "rework"
        await AuditService(self.session).record(actor=actor.name, action="task:correction_review", target=row.id, result="success", after={"approved": approve, "mode": mode})
        return row

    async def complete_rework(self, task: TaskItem, form_values: dict, acceptance_checks: dict, actor: User) -> TaskCorrection:
        """Close a correction-only rework task without advancing the main workflow."""
        correction_id = (task.source or "").split(":")[1] if (task.source or "").startswith("correction:") else ""
        row = (await self.session.execute(select(TaskCorrection).where(
            TaskCorrection.id == correction_id, TaskCorrection.status == "rework_assigned",
        ).with_for_update())).scalar_one_or_none()
        if row is None:
            raise BizError(BizCode.NOT_FOUND, "更正返工记录不存在")
        if not await self.can_act(task, actor):
            raise BizError(BizCode.PERM_DENIED, "仅更正返工处理人可提交", http_status=403)
        schema = await self._schema(task)
        for field in schema:
            if field.get("required") and is_empty_form_value((form_values or {}).get(field.get("key", ""))):
                raise BizError(BizCode.VALIDATION, f"「{field.get('label', field.get('key'))}」为必填")
        await self.workflow.validate_image_references(project=task.project, wi_id=task.wi_id, schema=schema, form_values=form_values or {})
        await self.workflow.validate_file_references(wi_id=task.wi_id, schema=schema, form_values=form_values or {}, uploader=actor.name)
        task.form_values, task.acceptance_checks, task.status = form_values or {}, acceptance_checks or {}, "completed"
        append = TaskAppend(id=gen_id("app"), task_id=row.task_id, node_id=task.node_id, wi_id=task.wi_id,
                            appender=f"更正返工({actor.name})", time=datetime.now(UTC).strftime("%m-%d %H:%M"), values=form_values or {})
        self.session.add(append)
        await self.session.flush()
        row.status, row.append_id, row.updated_at = "applied", append.id, datetime.now(UTC).isoformat()
        await AuditService(self.session).record(actor=actor.name, action="task:correction_rework_complete", target=row.id,
                                                result="success", after={"handlerTaskId": task.id})
        return row

    async def assert_work_item_can_advance(self, task: TaskItem) -> None:
        """An approved material correction must be resolved before main-flow decisions continue."""
        blocking = (await self.session.execute(select(TaskCorrection.id).where(
            TaskCorrection.wi_id == task.wi_id, TaskCorrection.status == "rework_assigned",
        ).limit(1))).scalar_one_or_none()
        if blocking:
            raise BizError(BizCode.DUPLICATE_OPERATION, "存在待完成的实质更正返工，完成后才能继续流转", http_status=409)
