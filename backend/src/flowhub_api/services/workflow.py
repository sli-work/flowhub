"""工作流服务（docs/04 §5）：发起实例 / 节点动作 / 绑定解析自动分配。"""
import asyncio
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import (
    GlobalTemplate, NodeAssignment, NotificationItem, Project, TaskItem, User, WorkItem, WorkflowInstance,
)
from flowhub_api.seed.init import gen_id


class WorkflowService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------- 编号（可排序：日期时间 + 随机尾缀，order by id desc 按时间倒序） ----------
    @staticmethod
    def next_wi_id(wi_type: str) -> str:
        from uuid import uuid4

        prefix = "REQ" if wi_type == "requirement" else ("ISSUE" if wi_type == "issue" else "CHG")
        return f"{prefix}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:4].upper()}"

    @staticmethod
    def next_task_id() -> str:
        from uuid import uuid4

        # 微秒时间戳 + 随机后缀：同请求内连续创建的任务 id 也保持递增可排序
        # （纯秒级时间戳 + 随机后缀会因同一秒内顺序不定导致任务列表/时间线排序不稳定）
        return f"T-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}-{uuid4().hex[:3].upper()}"

    # ---------- 发起 ----------
    async def create_instance(self, project_id: str, template_id: str, start_values: dict, creator: User) -> dict:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise BizError(BizCode.NOT_FOUND, "项目不存在")
        # 仅 active 项目可发起流程：draft（草稿）/ paused（暂停）/ completed（完结）/
        # cancelled（取消）/ archived（归档，含只读）一律拒绝，避免在非启用项目上创建流程
        if project.status != "active" or project.read_only:
            status_hint = {
                "draft": "项目尚在草稿中，请先编辑为「启用」后再发起流程",
                "paused": "项目已暂停，无法新建工作项",
                "completed": "项目已完结，无法新建工作项",
                "cancelled": "项目已取消，无法新建工作项",
                "archived": "项目已归档（只读），无法新建工作项",
            }.get(project.status, "项目当前状态不允许发起流程")
            raise BizError(BizCode.FORBIDDEN, status_hint, http_status=403)
        tpl = await self.session.get(GlobalTemplate, template_id)
        if tpl is None:
            raise BizError(BizCode.NOT_FOUND, "模板不存在")
        # 项目必须绑定该模板（docs/04 §5.1）
        bound = any(b.template_id == template_id for b in project.template_bindings)
        if not bound:
            raise BizError(BizCode.FORBIDDEN, f"项目未绑定模板「{tpl.name}」，无法发起流程")
        title = (start_values or {}).get("title", "").strip()
        if not title:
            raise BizError(BizCode.VALIDATION, "标题必填")

        wi_type = tpl.type
        start_node = next((n for n in tpl.nodes if n.get("type") == "start"), tpl.nodes[0] if tpl.nodes else {"id": "", "label": "开始"})
        start_label = start_node.get("label", "开始")
        # 权限：只有第一个节点的候选处理人（或系统/组织管理员）才能创建工作项；
        # start 节点未配置处理人时允许任何登录用户创建
        is_admin = any(r.id in ("system_admin", "organization_admin") for r in creator.roles)
        start_assignees = await self.resolve_node_assignees(project_id, template_id, start_node.get("id", ""))
        if start_assignees and not is_admin and not any(u.id == creator.id or u.name == creator.name for u in start_assignees):
            names = "、".join(u.name for u in start_assignees[:3])
            raise BizError(
                BizCode.FORBIDDEN,
                f"仅「{start_label}」节点的处理人（{names}）可创建工作项",
                http_status=403,
            )
        wi = WorkItem(
            id=self.next_wi_id(wi_type), type=wi_type, title=title,
            project=project.name, priority=(start_values.get("priority") or "P2"),
            status="in_progress", assignee=creator.name, creator=creator.name,
            due=(datetime.now(UTC) + timedelta(days=3)).strftime("%m-%d"),
            labels=(start_values.get("labels") or []), progress=start_label,
            start_values=start_values,
        )
        instance = WorkflowInstance(
            id=gen_id("inst"), work_item_id=wi.id, template_id=template_id,
            version=next((b.version for b in project.template_bindings if b.template_id == template_id), "v1"),
            current_node=start_node.get("id", ""), state="running",
        )
        # 起始节点任务：创建后【自动完成并直接流转到下一节点】——
        # 创建时填写的硬性要求表单即 start 节点表单，无需人工再点一次提交
        start_task = TaskItem(
            id=self.next_task_id(), wi_id=wi.id, title=title, project=project.name,
            node=start_label, node_id=start_node.get("id", ""),
            type=wi_type, priority=wi.priority, status="assigned",
            assignee=creator.name,
            due=(datetime.now(UTC) + timedelta(hours=48)).strftime("%m-%d %H:%M"),
            sla_hours=48,
        )
        self.session.add(wi)
        self.session.add(instance)
        self.session.add(start_task)
        await self.session.flush()
        start_task.form_values = start_values or {}
        start_task.status = "completed"
        flow = await self.advance(start_task, project, tpl)
        next_node = flow.get("next_node")
        next_task = flow.get("task") or (flow.get("tasks") or [None])[0]
        closed = bool(flow.get("closed")) or next_node is None
        if next_node is not None:
            instance.current_node = next_node.get("id", "")
        elif closed:
            instance.current_node = ""
            instance.state = "closed"
        # 站内通知：发起人（工作项已创建）+ 下一节点处理人（新任务到达）
        # 真实投递已启用渠道（钉钉/企微 webhook、邮件 SMTP，未配置渠道自动跳过），失败渠道标记 failed 可重试
        from flowhub_api.services.notify import deliver_channels

        now = datetime.now(UTC).strftime("%m-%d %H:%M")
        cur_label = next_node.get("label", start_label) if next_node is not None else "流程结束"
        ch1 = await deliver_channels("工作项已创建并启动流程", f"「{title}」已进入流程，当前节点「{cur_label}」", creator)
        created_notifications: list[NotificationItem] = []
        creator_notification = NotificationItem(
            id=gen_id("ntf"), title="工作项已创建并启动流程",
            body=f"「{title}」已进入流程，当前节点「{cur_label}」",
            time=now, channels=ch1, kind="info",
            unread=True, failed=any(not c["ok"] for c in ch1), target_user=creator.account,
            wi_id=wi.id,
        )
        self.session.add(creator_notification)
        created_notifications.append(creator_notification)
        if next_task is not None:
            assignee = (await self.session.execute(
                select(User).where(User.name == next_task.assignee)
            )).scalar_one_or_none()
            ch2 = await deliver_channels(
                "新待办任务", f"节点「{next_task.node}」有你的新任务 {next_task.id}（SLA 48h）", assignee,
            )
            assignee_notification = NotificationItem(
                id=gen_id("ntf"), title="新待办任务",
                body=f"节点「{next_task.node}」有你的新任务 {next_task.id}（SLA 48h）",
                time=now, channels=ch2, kind="arrive",
                unread=True, failed=any(not c["ok"] for c in ch2),
                target_user=assignee.account if assignee else next_task.assignee,
                wi_id=wi.id, task_id=next_task.id,
            )
            self.session.add(assignee_notification)
            created_notifications.append(assignee_notification)
        await self.session.flush()
        return {"item": wi, "instance": instance, "next_node": next_node, "start_task": start_task, "next_task": next_task, "closed": closed, "notifications": created_notifications}

    # ---------- 绑定解析（PRD §4.5） ----------
    async def resolve_node_assignees(self, project_id: str, template_id: str, node_id: str) -> list[User]:
        project = await self.session.get(Project, project_id)
        if project is None:
            return []
        binding = next((b for b in project.template_bindings if b.template_id == template_id), None)
        if binding is None:
            return []
        assignment = next((a for a in binding.assignments if a.node_id == node_id), None)
        if assignment is None:
            return []
        all_users = (await self.session.execute(select(User))).scalars().all()
        candidates: dict[str, User] = {}
        for uid in assignment.users or []:
            u = next((x for x in all_users if x.id == uid), None)
            if u and u.status == "active":
                candidates[u.id] = u
        for role in assignment.roles or []:
            for u in all_users:
                if u.status == "active" and (role in [r.id for r in u.roles] or role in u.skills):
                    candidates[u.id] = u
        return list(candidates.values())

    # ---------- 节点动作 ----------
    @staticmethod
    def _match_cond(form: dict, cond: dict | None) -> bool:
        """决策分支条件匹配：cond = {field, op(eq/ne/contains), value}，无条件不匹配（走默认分支）。"""
        if not cond or not cond.get("field"):
            return False
        actual = str(form.get(cond["field"], ""))
        expect = str(cond.get("value", ""))
        op = cond.get("op", "eq")
        if op == "eq":
            return actual == expect
        if op == "ne":
            return actual != expect
        if op == "contains":
            return expect in actual
        return actual == expect

    async def _sync_instance(self, wi_id: str, node_id: str | None = None, state: str | None = None) -> None:
        """流转后同步流程实例的当前节点 / 状态（工作项详情页展示 current_node 依据）。"""
        inst = (await self.session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id == wi_id)
        )).scalar_one_or_none()
        if inst is not None:
            if node_id is not None:
                inst.current_node = node_id
            if state is not None:
                inst.state = state

    async def advance(self, task: TaskItem, project: Project, tpl: GlobalTemplate) -> dict:
        """完成任务 → 沿边推进到下一节点（决策按条件选分支 / 并行分叉拆单 / 汇合等齐）→ 绑定解析 → 生成新任务。"""
        nodes = tpl.nodes
        edges = await self._edges_of(tpl)
        current = task.node_id
        cur_node = next((n for n in nodes if n.get("id") == current), None)
        out_edges = [b for a, b in edges if a == current]

        # 结束节点是最终人工确认任务：处理人提交（含 Schema 必填校验）后才关闭流程。
        if cur_node and cur_node.get("type") == "end":
            cfg = await self._node_cfg_of(tpl, current) or {}
            values = task.form_values or {}
            for field in cfg.get("schema") or []:
                key = field.get("key", "")
                if field.get("required") and not str(values.get(key, "") or "").strip():
                    raise BizError(BizCode.VALIDATION, f"「{field.get('label', key)}」为必填")
            task.status = "completed"
            wi = await self.session.get(WorkItem, task.wi_id)
            if wi:
                wi.status = "closed"
                wi.progress = cur_node.get("label", "完成")
            await self._sync_instance(task.wi_id, node_id=current, state="closed")
            return {"next_node": cur_node, "next_assignees": [], "closed": True}

        # 决策节点：多条出边按提交表单值选一条分支（无匹配走默认分支）
        if cur_node and cur_node.get("type") == "decision" and len(out_edges) > 1:
            cfg = await self._node_cfg_of(tpl, current) or {}
            branches = cfg.get("branches") or {}
            form = task.form_values or {}
            out_edges = [
                next((to for to in out_edges if self._match_cond(form, branches.get(to))),
                     cfg.get("defaultBranch") or out_edges[0]),
            ]

        if not out_edges:
            # 无出边（单节点流程 / 边缺失）：直接关闭
            wi = await self.session.get(WorkItem, task.wi_id)
            if wi:
                wi.status = "closed"
                wi.progress = "完成"
            task.status = "completed"
            await self._sync_instance(task.wi_id, node_id="", state="closed")
            return {"next_node": None, "next_assignees": [], "closed": True}

        # 并行分叉：非决策节点多条出边 → 每个分支各生成一个任务（拆单）
        if cur_node and cur_node.get("type") not in ("decision", "parallel_join") and len(out_edges) > 1:
            new_tasks = []
            for to in out_edges:
                nxt = next((n for n in nodes if n.get("id") == to), {"id": to, "label": to})
                t = await self._spawn_task(task, project, tpl, nxt)
                new_tasks.append(t)
            task.status = "completed"
            wi = await self.session.get(WorkItem, task.wi_id)
            if wi:
                wi.progress = new_tasks[0].node
            first_assignees = await self.resolve_node_assignees(project.id, tpl.id, new_tasks[0].node_id)
            await self._sync_instance(task.wi_id, node_id=new_tasks[0].node_id)
            return {
                "next_node": {"id": new_tasks[0].node_id, "label": new_tasks[0].node},
                "next_assignees": [{"id": u.id, "name": u.name, "dept": u.dept} for u in first_assignees],
                "tasks": new_tasks,
                "parallel": True,
            }

        next_id = out_edges[0]
        next_node = next((n for n in nodes if n.get("id") == next_id), {"id": next_id, "label": next_id})

        # 并行汇合：目标节点有多条入边（来自多个分支）→ 必须所有分支任务完成后才生成
        join_sources = {a for a, b in edges if b == next_id}
        if len(join_sources) > 1:
            done_nodes = {
                x.node_id for x in (await self.session.execute(
                    select(TaskItem).where(TaskItem.wi_id == task.wi_id, TaskItem.status == "completed")
                )).scalars().all()
            }
            done_nodes.add(current)
            missing = sorted(join_sources - done_nodes)
            if missing:
                # 还有分支未完成 → 当前任务完成但等待汇合，不生成下一节点任务
                task.status = "completed"
                wi = await self.session.get(WorkItem, task.wi_id)
                if wi:
                    wi.progress = next_node.get("label", next_id)
                await self._sync_instance(task.wi_id, node_id=next_id)
                return {"next_node": next_node, "next_assignees": [], "waiting_join": True, "join_sources": missing}

        # 正常推进：生成单个下一节点任务
        new_task = await self._spawn_task(task, project, tpl, next_node)
        task.status = "completed"
        wi = await self.session.get(WorkItem, task.wi_id)
        if wi:
            wi.progress = next_node.get("label", next_id)
            wi.assignee = new_task.assignee
        assignees = await self.resolve_node_assignees(project.id, tpl.id, next_id)
        await self._sync_instance(task.wi_id, node_id=next_id)
        return {
            "next_node": next_node,
            "next_assignees": [{"id": u.id, "name": u.name, "dept": u.dept} for u in assignees],
            "task": new_task,
            "tasks": [new_task],
        }

    async def _spawn_task(self, task: TaskItem, project: Project, tpl: GlobalTemplate, next_node: dict) -> TaskItem:
        """为 next_node 生成任务（绑定解析处理人 + Agent 节点触发），并落库（flush）。"""
        next_id = next_node.get("id", "")
        assignees = await self.resolve_node_assignees(project.id, tpl.id, next_id)
        new_task = TaskItem(
            id=self.next_task_id(), wi_id=task.wi_id, title=task.title, project=project.name,
            node=next_node.get("label", next_id), node_id=next_id, type=task.type,
            priority=task.priority, status="assigned",
            assignee=assignees[0].name if assignees else "待分配",
            due=(datetime.now(UTC) + timedelta(hours=48)).strftime("%m-%d %H:%M"),
            sla_hours=48,
        )
        # Agent 节点集成（docs/05 §5）：节点 handler 含「需要 Agent 协助」（Agent 自动 / 人工+可协助）
        # 且绑定 Agent → 置 agent_pending + 触发预分析/确认流。
        canvas_cfg = await self._node_cfg_of(tpl, next_id)
        handler = (canvas_cfg or {}).get("handler", "")
        agent_cfg = (canvas_cfg or {}).get("agent") or {}
        if agent_cfg.get("agentId"):
            new_task.agent_pending = True
            if handler in ("Agent 自动", "人工 + Agent 可协助"):
                from flowhub_api.services.agent_runner import trigger_node_invocation

                # 先落库新任务（同一会话 flush），确认请求才能查到该任务
                self.session.add(new_task)
                await self.session.flush()
                if handler == "Agent 自动":
                    new_task.status = "pending_confirmation"
                    # Agent 自动：同步等待确认请求创建（与任务同一事务、立即可见可批准）
                    await trigger_node_invocation(
                        agent_cfg["agentId"], new_task.id,
                        {**canvas_cfg, "id": next_id, "label": next_node.get("label", next_id)},
                        new_task.assignee, session=self.session,
                    )
                else:
                    # 人工 + Agent 可协助：预分析后台执行，不阻塞人工处理
                    asyncio.create_task(trigger_node_invocation(
                        agent_cfg["agentId"], new_task.id,
                        {**canvas_cfg, "id": next_id, "label": next_node.get("label", next_id)},
                        new_task.assignee,
                    ))
        self.session.add(new_task)
        await self.session.flush()
        return new_task

    async def _edges_of(self, tpl: GlobalTemplate) -> list[tuple[str, str]]:
        """模板主边：优先取最新 published 版本的画布边（引擎按已发布流程执行），
        无 published 有边版本时回退最新有画布的草稿；再兜底按节点顺序串联。

        注意：不能取"最新任意版本"——未发布的残缺草稿（如仅保存部分节点）会
        污染边集，导致流程找不到出边、进度卡死。
        """
        from flowhub_api.models import TemplateCanvas, TemplateVersion

        rows = (await self.session.execute(
            select(TemplateVersion).where(TemplateVersion.template_id == tpl.id)
        )).scalars().all()
        ordered = sorted(
            rows, key=lambda tv: int(tv.version[1:]) if tv.version[1:].isdigit() else 0, reverse=True,
        )
        for tv in ordered:
            if tv.status != "published":
                continue
            canvas = await self.session.get(TemplateCanvas, tv.id)
            if canvas and canvas.edges:
                return [(e[0], e[1]) for e in canvas.edges]
        # 无 published 有边版本 → 回退最新有画布的草稿
        for tv in ordered:
            canvas = await self.session.get(TemplateCanvas, tv.id)
            if canvas and canvas.edges:
                return [(e[0], e[1]) for e in canvas.edges]
        ids = [n.get("id") for n in tpl.nodes]
        return list(zip(ids, ids[1:])) if len(ids) > 1 else []

    async def _node_cfg_of(self, tpl: GlobalTemplate, node_id: str) -> dict | None:
        """读模板指定节点的 cfg（含 agent 绑定）：优先取最新 published 版本画布，
        与主边同一版本配套（避免残缺草稿覆盖节点配置）；无 published 时回退最新草稿。"""
        from flowhub_api.models import TemplateCanvas, TemplateVersion

        rows = (await self.session.execute(
            select(TemplateVersion).where(TemplateVersion.template_id == tpl.id)
        )).scalars().all()
        ordered = sorted(
            rows, key=lambda tv: int(tv.version[1:]) if tv.version[1:].isdigit() else 0, reverse=True,
        )
        for tv in ordered:
            if tv.status != "published":
                continue
            canvas = await self.session.get(TemplateCanvas, tv.id)
            if canvas and canvas.nodes:
                node = next((n for n in canvas.nodes if n.get("id") == node_id), None)
                if node is not None:
                    return node.get("cfg") or {}
        for tv in ordered:
            canvas = await self.session.get(TemplateCanvas, tv.id)
            if canvas and canvas.nodes:
                node = next((n for n in canvas.nodes if n.get("id") == node_id), None)
                if node is not None:
                    return node.get("cfg") or {}
        # 回退：模板定义节点
        node = next((n for n in tpl.nodes if n.get("id") == node_id), None)
        return {"handler": "", "purpose": (node or {}).get("label", "")}
