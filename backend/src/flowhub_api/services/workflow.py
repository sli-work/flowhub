"""工作流服务（docs/04 §5）：发起实例 / 节点动作 / 绑定解析自动分配。"""
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import (
    GlobalTemplate, NodeAssignment, NotificationItem, Project, TaskItem, User, WorkItem, WorkflowInstance,
)
from flowhub_api.models.workflow import PRIORITY
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services import repo_mirror


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
        # priority 归一化：起始表单选项可能为小写（p0），而 DB 枚举只接受大写（P0-P3）；非法值回落 P2
        raw_priority = str(start_values.get("priority") or "").upper()
        wi = WorkItem(
            id=self.next_wi_id(wi_type), type=wi_type, title=title,
            project=project.name, priority=raw_priority if raw_priority in PRIORITY else "P2",
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
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
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

    # ---------- 产出契约 ----------
    @staticmethod
    def deliverable_of(cfg: dict | None) -> dict:
        """规范化节点产出契约：兼容旧 cfg.output（单行文本）懒迁移为 instruction。"""
        cfg = cfg or {}
        deliverable = dict(cfg.get("deliverable") or {})
        if not deliverable.get("instruction"):
            legacy = str(cfg.get("output") or "").strip()
            if legacy and legacy != "待配置":
                deliverable["instruction"] = legacy
        deliverable.setdefault("instruction", "")
        deliverable.setdefault("acceptance", [])
        deliverable.setdefault("aiGuidance", "")
        deliverable.setdefault("example", "")
        return deliverable

    async def build_task_brief(self, task: TaskItem, tpl: GlobalTemplate) -> str:
        """结构化任务书：节点目的 + 产出要求 + 验收标准 + 表单字段 + 上游摘要。
        人（处理页任务书）与 AI（Expert 运行 prompt）消费同一份契约。"""
        cfg = await self._node_cfg_of(tpl, task.node_id) or {}
        deliverable = self.deliverable_of(cfg)
        wi = await self.session.get(WorkItem, task.wi_id)
        lines: list[str] = [f"# 任务书：{task.node}"]
        if task.brief:
            lines.append(f"## 拆分说明\n{task.brief}")
        purpose = str(cfg.get("purpose") or "").strip()
        if purpose:
            lines.append(f"## 节点目的\n{purpose}")
        if deliverable["instruction"]:
            lines.append(f"## 产出要求\n{deliverable['instruction']}")
        acceptance = deliverable.get("acceptance") or []
        if acceptance:
            items = "\n".join(f"- {item.get('text', '')}" + (f"（{item.get('hint')}）" if item.get("hint") else "") for item in acceptance)
            lines.append(f"## 验收标准（逐条确认）\n{items}")
        schema = cfg.get("schema") or []
        if schema:
            fields = "\n".join(
                f"- {f.get('label', f.get('key'))}（{f.get('key')}，{'必填' if f.get('required') else '选填'}）：{f.get('placeholder') or ''}"
                for f in schema
            )
            lines.append(f"## 需要填写的产出字段\n{fields}\n请严格按字段 key 组织结果 JSON。")
        if deliverable.get("aiGuidance"):
            lines.append(f"## AI 执行指引\n{deliverable['aiGuidance']}")
        if wi is not None and (wi.start_values or {}).get("title"):
            sv = wi.start_values or {}
            summary = "；".join(f"{k}={str(v)[:80]}" for k, v in sv.items() if k != "labels" and str(v).strip())[:600]
            lines.append(f"## 工作项背景\n{summary}")
        if wi is not None:
            # 仓库地图层：任务书渲染不阻塞（只用已建镜像），缺镜像时后台补建
            repo_section = await repo_mirror.repo_map_section(self.session, wi.project, allow_clone=False)
            if "镜像构建中" in repo_section:
                await repo_mirror.schedule_mirror_build(wi.project)
            if repo_section:
                lines.append(f"## 关联代码仓库\n{repo_section}")
        if deliverable.get("example"):
            lines.append(f"## 参考示例\n{deliverable['example']}")
        return "\n\n".join(lines)

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

    async def _find_open_task_at(self, task: TaskItem, node_id: str) -> TaskItem | None:
        """幂等兜底：同工作项、同节点、同子线上已有未终结任务则返回它（父级任务，非拆分子任务）。
        防止重复提交/多实例写入在相同节点生成重复待办（子任务拆分的并行子线不受影响）。"""
        lineage_cond = (
            TaskItem.lineage_root_id == task.lineage_root_id
            if task.lineage_root_id
            else TaskItem.lineage_root_id.is_(None)
        )
        return (await self.session.execute(
            select(TaskItem).where(
                TaskItem.wi_id == task.wi_id,
                TaskItem.node_id == node_id,
                TaskItem.status.not_in(["completed", "cancelled"]),
                TaskItem.parent_task_id.is_(None),
                TaskItem.id != task.id,
                lineage_cond,
            ).order_by(TaskItem.id).limit(1)
        )).scalar_one_or_none()

    async def advance(self, task: TaskItem, project: Project, tpl: GlobalTemplate, auto_depth: int = 0) -> dict:
        """完成任务 → 沿边推进到下一节点（决策按条件选分支 / 并行分叉拆单 / 汇合等齐）→ 绑定解析 → 生成新任务。
        auto_depth：自动节点的递归采纳深度（防连环自动节点 + 环画布无限递归）。"""
        nodes = tpl.nodes
        edges = await self._edges_of(tpl)
        current = task.node_id
        cur_node = next((n for n in nodes if n.get("id") == current), None)
        out_edges = [b for a, b in edges if a == current]
        cfg = await self._node_cfg_of(tpl, current) or {}
        values = task.form_values or {}

        # 产出契约统一校验：所有产出节点的 schema 必填项 + 验收清单强制勾选（此前仅 end 节点校验）
        for field in cfg.get("schema") or []:
            key = field.get("key", "")
            if field.get("required") and not str(values.get(key, "") or "").strip():
                raise BizError(BizCode.VALIDATION, f"「{field.get('label', key)}」为必填")
        acceptance = self.deliverable_of(cfg).get("acceptance") or []
        if acceptance:
            checks = task.acceptance_checks or {}
            missing = [item.get("text", "") for item in acceptance if not (checks.get(item.get("key", "")) or {}).get("checked")]
            if missing:
                raise BizError(BizCode.VALIDATION, f"验收标准未全部确认：{'；'.join(missing)}")

        # 结束节点是最终人工确认任务：处理人提交（含 Schema 必填校验）后才关闭流程。
        # 多线流程（子任务独立流转）：仅当工作项下没有其他未终结任务时才关闭，否则保持进行中。
        if cur_node and cur_node.get("type") == "end":
            task.status = "completed"
            open_left = (await self.session.execute(
                select(TaskItem).where(
                    TaskItem.wi_id == task.wi_id,
                    TaskItem.id != task.id,
                    TaskItem.status.not_in(["completed", "cancelled"]),
                )
            )).scalars().first()
            wi = await self.session.get(WorkItem, task.wi_id)
            if wi and open_left is None:
                wi.status = "closed"
                wi.progress = cur_node.get("label", "完成")
                await self._sync_instance(task.wi_id, node_id=current, state="closed")
            elif wi:
                wi.progress = f"{cur_node.get('label', '完成')}（等待其他分支）"
            return {"next_node": cur_node, "next_assignees": [], "closed": open_left is None}

        # 决策节点：多条出边且配置了分支条件 → 按提交表单值选一条（无匹配走默认分支）；
        # 未配置任何条件时视为并行分叉（全部分支生成任务），避免静默丢弃分支导致后续汇合永久等待
        is_decision = bool(cur_node and cur_node.get("type") == "decision")
        branches_cfg = (cfg.get("branches") or {}) if is_decision else {}
        decision_conditional = is_decision and len(out_edges) > 1 and any(
            branches_cfg.get(to) for to in out_edges
        )
        if decision_conditional:
            form = task.form_values or {}
            out_edges = [
                next((to for to in out_edges if self._match_cond(form, branches_cfg.get(to))),
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

        # 并行分叉：多条出边且非（配置了条件的决策节点 / 汇合节点）→ 每个分支各生成一个任务（拆单）
        if cur_node and cur_node.get("type") != "parallel_join" and len(out_edges) > 1 and not decision_conditional:
            new_tasks = []
            for to in out_edges:
                nxt = next((n for n in nodes if n.get("id") == to), {"id": to, "label": to})
                # 幂等兜底：该分支节点已有未终结任务则复用，不重复生成
                existing = await self._find_open_task_at(task, to)
                new_tasks.append(existing if existing is not None else await self._spawn_task(task, project, tpl, nxt, auto_depth=auto_depth))
            task.status = "completed"
            wi = await self.session.get(WorkItem, task.wi_id)
            # 首分支若是自动节点且已被递归采纳流转，进度以内层更新为准
            if wi and new_tasks[0].status != "completed":
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
            # 子任务独立流转：汇合只等待同一子线 root 的分支，不能与其他子线互相满足/阻塞。
            join_stmt = select(TaskItem).where(TaskItem.wi_id == task.wi_id, TaskItem.status == "completed")
            if task.lineage_root_id:
                join_stmt = join_stmt.where(TaskItem.lineage_root_id == task.lineage_root_id)
            else:
                join_stmt = join_stmt.where(TaskItem.lineage_root_id.is_(None))
            done_nodes = {x.node_id for x in (await self.session.execute(join_stmt)).scalars().all()}
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

        # 正常推进：生成单个下一节点任务（幂等兜底：已有未终结任务则复用，防重复提交产生重复待办）
        existing = await self._find_open_task_at(task, next_id)
        if existing is not None:
            new_task = existing
        else:
            new_task = await self._spawn_task(task, project, tpl, next_node, auto_depth=auto_depth)
        task.status = "completed"
        wi = await self.session.get(WorkItem, task.wi_id)
        # 若 new_task 是自动节点且已被递归采纳并继续流转（status 已 completed），
        # 内层 advance 已更新进度/实例，外层不得覆盖为中间节点
        advanced_further = new_task.status == "completed"
        if wi and not advanced_further:
            wi.progress = next_node.get("label", next_id)
            wi.assignee = new_task.assignee
        assignees = await self.resolve_node_assignees(project.id, tpl.id, next_id)
        if not advanced_further:
            await self._sync_instance(task.wi_id, node_id=next_id)
        return {
            "next_node": next_node,
            "next_assignees": [{"id": u.id, "name": u.name, "dept": u.dept} for u in assignees],
            "task": new_task,
            "tasks": [new_task],
        }

    # ---------- 节点表单 AI 填充 ----------
    async def fill_task_from_run(self, task: TaskItem, run, cfg: dict, actor: User) -> tuple[dict, list[str]]:
        """把 Expert Run 产出解析为节点表单值（upload 字段自动生成工作项文档）。"""
        from flowhub_api.services.expert_runtime import parse_schema_output

        schema = cfg.get("schema") or []
        values, warnings = parse_schema_output(schema, run.output or "")
        for f in schema:
            key, ftype = f.get("key", ""), f.get("type", "")
            if ftype in ("upload", "file") and isinstance(values.get(key), str) and values[key].strip():
                from flowhub_api.services.expert_runtime import create_document_from_text

                ref = await create_document_from_text(
                    self.session, wi_id=task.wi_id, project=task.project,
                    name=f"{f.get('label', key)}-{task.id}", content=values[key], uploader=actor,
                )
                values[key] = [ref]
        return values, warnings

    def acceptance_checks_ai(self, cfg: dict) -> dict:
        """Expert 自动路径：验收清单由 AI 自评逐条确认（快照标注来源，供人复核）。"""
        acceptance = self.deliverable_of(cfg).get("acceptance") or []
        return {item.get("key", ""): {"text": item.get("text", ""), "checked": True, "source": "ai"} for item in acceptance}

    async def ai_autosubmit(self, task: TaskItem, project: Project, tpl: GlobalTemplate, cfg: dict, run, actor: User, auto_depth: int = 0) -> dict:
        """Expert 自动节点：采纳 run 产出填充表单后自动流转。返回 advance 结果。
        若节点开启 ai_auto 拆分且模型输出带 split 数组，则拆分为多条子线（父完成不 advance）。"""
        values, warnings = await self.fill_task_from_run(task, run, cfg, actor)
        task.acceptance_checks = self.acceptance_checks_ai(cfg)
        split_mode = (cfg.get("split") or {}).get("mode", "off")
        from flowhub_api.services.expert_runtime import extract_split_proposals

        proposals = extract_split_proposals(run.output or "") if split_mode == "ai_auto" else []
        if split_mode == "ai_auto" and proposals:
            children = [
                {"title": str(p.get("title", ""))[:120], "note": str(p.get("note", "")), "assignee": str(p.get("assignee_hint", ""))}
                for p in proposals if str(p.get("title", "")).strip()
            ]
            if children:
                task.form_values = {k: v for k, v in values.items() if k != "split"}
                await AuditService(self.session).record(
                    actor=f"Expert({actor.name})", action="task:ai_split", target=f"{task.id} · {task.node}",
                    result="success", after={"runId": run.id, "children": [c["title"] for c in children]},
                )
                await self.split_task(task, project, tpl, children, actor)
                return {"split": True, "children": len(children)}
        task.form_values = values
        await AuditService(self.session).record(
            actor=f"Expert({actor.name})", action="task:ai_submit", target=f"{task.id} · {task.node}",
            result="success", after={"runId": run.id, "warnings": warnings[:5]},
        )
        return await self.advance(task, project, tpl, auto_depth=auto_depth)

    async def submit_and_advance(self, t: TaskItem, form_values: dict | None, acceptance_checks: dict | None, actor: User) -> dict:
        """人工提交任务核心（HTTP 路由与外部 MCP 工具共用）：
        表单/验收快照落库 → 表单附件回填工作项关联 → 沿边流转 → 审计。
        调用方负责 commit 与响应组装；返回 advance 结果（或无绑定时的兜底结果）。"""
        from flowhub_api.models import DocItem

        t.form_values = form_values or {}
        t.acceptance_checks = acceptance_checks or {}
        # 表单附件回填工作项关联：与发起流程时的 _attachment_ids 同语义，防止文档游离
        attachment_ids = [
            v.get("id") for v in (form_values or {}).values()
            if isinstance(v, dict) and isinstance(v.get("id"), str)
        ] + [
            ref.get("id") for v in (form_values or {}).values() if isinstance(v, list)
            for ref in v if isinstance(ref, dict) and isinstance(ref.get("id"), str)
        ]
        if attachment_ids:
            docs = (await self.session.execute(
                select(DocItem).where(DocItem.id.in_(attachment_ids), DocItem.wi.is_(None))
            )).scalars().all()
            for d in docs:
                d.wi = t.wi_id
        # 推进流程：需要项目 + 模板定位主边
        project = (await self.session.execute(select(Project).where(Project.name == t.project))).scalar_one_or_none()
        tpl = None
        if project:
            binding = next((b for b in project.template_bindings if b.status == "active"), None)
            if binding:
                tpl = await self.session.get(GlobalTemplate, binding.template_id)
        if project and tpl:
            result = await self.advance(t, project, tpl)
        else:
            # 无项目绑定 → 仅标记完成
            t.status = "completed"
            result = {"next_node": None, "next_assignees": [], "task": None, "tasks": [], "closed": True}
        await AuditService(self.session).record(
            actor=actor.name, action="task:submit", target=f"{t.id} · {t.node}", result="success",
            after={"next": result.get("next_node").get("label") if result.get("next_node") else None,
                   "assignees": [a.get("name") for a in (result.get("next_assignees") or []) if isinstance(a, dict)]},
        )
        return result

    async def resolve_template_for_task(self, task: TaskItem) -> tuple[Project | None, GlobalTemplate | None]:
        project = (await self.session.execute(select(Project).where(Project.name == task.project))).scalar_one_or_none()
        tpl = None
        if project:
            binding = next((b for b in project.template_bindings if b.status == "active"), None)
            if binding:
                tpl = await self.session.get(GlobalTemplate, binding.template_id)
        return project, tpl

    async def _spawn_task(self, task: TaskItem, project: Project, tpl: GlobalTemplate, next_node: dict, *, title: str = "", assignee: str = "", due_hours: int | None = None, brief: str = "", parent_task_id: str | None = None, lineage_root_id: str | None = None, auto_depth: int = 0) -> TaskItem:
        """为 next_node 生成任务并触发绑定的 Expert Deployment。
        覆盖参数供子任务拆分使用（标题/负责人/截止/拆分说明/父链接）。"""
        next_id = next_node.get("id", "")
        resolved = await self.resolve_node_assignees(project.id, tpl.id, next_id)
        due = timedelta(hours=due_hours) if due_hours else timedelta(hours=48)
        new_task = TaskItem(
            id=self.next_task_id(), wi_id=task.wi_id,
            title=title or task.title, project=project.name,
            node=next_node.get("label", next_id), node_id=next_id, type=task.type,
            priority=task.priority, status="assigned",
            assignee=assignee or (resolved[0].name if resolved else "待分配"),
            due=(datetime.now(UTC) + due).strftime("%m-%d %H:%M"),
            sla_hours=due_hours or 48,
            parent_task_id=parent_task_id,
            # 普通后继沿用当前子线 root；拆分起点首次创建时稍后以自身 id 固定 root
            lineage_root_id=lineage_root_id if lineage_root_id is not None else task.lineage_root_id,
            brief=brief,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )
        # Expert Deployment 节点集成：画布绑定一个已发布的 Deployment。
        # run 语义统一（不再走 write_intent 审批中断）：
        # - 自动节点：run 成功后直接采纳产出 → 填充表单 → 自动流转（免人工介入）
        # - 协助节点：run 结果由处理人在任务页点击「采纳」回填表单
        canvas_cfg = await self._node_cfg_of(tpl, next_id)
        handler = (canvas_cfg or {}).get("handler", "")
        expert_cfg = (canvas_cfg or {}).get("expert") or {}
        deployment_id = expert_cfg.get("expertDeploymentId")
        if deployment_id:
            new_task.expert_pending = True
            self.session.add(new_task)
            await self.session.flush()
            # 运行发起人：节点处理人优先；待分配时回退工作项创建人，再回退系统管理员
            expert_user = (await self.session.execute(select(User).where(User.name == new_task.assignee))).scalar_one_or_none()
            if expert_user is None:
                wi_row = await self.session.get(WorkItem, new_task.wi_id)
                if wi_row is not None:
                    expert_user = (await self.session.execute(select(User).where(User.name == wi_row.creator))).scalar_one_or_none()
            if expert_user is None:
                expert_user = (await self.session.execute(
                    select(User).join(User.roles).where(User.name == "系统管理员")
                )).scalars().first()
            from flowhub_api.services.expert_runtime import schedule_deployment_run

            # AI 拿到与人相同的产出契约任务书，而不是「id·标题·节点」三件套
            brief_text = await self.build_task_brief(new_task, tpl)
            is_auto = handler == "Expert 自动"
            if is_auto and ((canvas_cfg or {}).get("split") or {}).get("mode") == "ai_auto":
                brief_text += (
                    "\n\n## 自动拆分指令\n本节点要求对工作拆分为可独立执行的子任务。"
                    '请在输出 JSON 中额外增加 "split" 字段（数组），每项格式：'
                    '{"title":"子任务标题","note":"子任务要求说明","assignee_hint":"建议负责人"}；'
                    "拆分粒度到可独立交付的模块，数量 1-5 个。"
                )
            if is_auto:
                # 自动节点处理期间禁止人工提交（后台完成后自动采纳流转，失败回退 assigned）
                new_task.status = "pending_confirmation"
            expert_user_id = expert_user.id if expert_user is not None else None
            auto_depth_next = auto_depth + 1

            async def _on_auto_finished(bg_session, bg_run):
                """自动节点后台 Run 完成回调（独立 Session）：成功 → 采纳流转；失败/校验不过 → 回退人工。"""
                if not is_auto:
                    return
                bg_task = await bg_session.get(TaskItem, new_task.id)
                if bg_task is None or bg_task.status != "pending_confirmation":
                    return
                if bg_run.status == "succeeded" and expert_user_id and auto_depth < 5:
                    bg_user = await bg_session.get(User, expert_user_id)
                    if bg_user is not None:
                        try:
                            svc = WorkflowService(bg_session)
                            project_bg, tpl_bg = await svc.resolve_template_for_task(bg_task)
                            if project_bg is not None and tpl_bg is not None:
                                cfg_bg = await svc._node_cfg_of(tpl_bg, bg_task.node_id) or {}
                                if cfg_bg.get("handler") == "Expert 自动":
                                    await svc.ai_autosubmit(
                                        bg_task, project_bg, tpl_bg, cfg_bg, bg_run, bg_user,
                                        auto_depth=auto_depth_next,
                                    )
                                    return
                        except BizError:
                            pass  # 产出未通过节点校验（必填缺失等）→ 落到人工兜底
                # 运行失败 / 深度超限 / 校验不过：回退人工兜底（保持待办可见）
                refreshed = await bg_session.get(TaskItem, new_task.id)
                if refreshed is not None and refreshed.status == "pending_confirmation":
                    refreshed.status = "assigned"

            # Run 后台执行：提交请求不再等待 LLM（此前内联执行导致提交挂起 30s+）；
            # 协助节点完成后处理人在任务页「采纳」；自动节点由 _on_auto_finished 自动采纳流转
            await schedule_deployment_run(
                self.session, deployment_id, brief_text,
                expert_user, task_id=new_task.id,
                on_finished=_on_auto_finished,
            )
        self.session.add(new_task)
        await self.session.flush()
        return new_task

    async def split_task(self, task: TaskItem, project: Project, tpl: GlobalTemplate, children: list[dict], actor: User) -> list[TaskItem]:
        """把当前任务拆分为多个子任务：父任务完成（不 advance），子任务在下一节点独立流转。
        约束：仅任务类型节点、单出边、cfg.split.mode != off；每个子任务带独立标题/负责人/截止/需求说明。"""
        nodes = tpl.nodes
        edges = await self._edges_of(tpl)
        current = task.node_id
        cur_node = next((n for n in nodes if n.get("id") == current), None)
        if not cur_node or cur_node.get("type") != "task":
            raise BizError(BizCode.VALIDATION, "仅任务类型节点支持拆分子任务", http_status=422)
        cfg = await self._node_cfg_of(tpl, current) or {}
        split_mode = (cfg.get("split") or {}).get("mode", "off")
        if split_mode == "off":
            raise BizError(BizCode.VALIDATION, "该节点未开启子任务拆分（请在流程画布节点配置中开启）", http_status=422)
        out_edges = [b for a, b in edges if a == current]
        if len(out_edges) != 1:
            raise BizError(BizCode.VALIDATION, "拆分要求节点只有一条后继分支；多分支请使用并行分叉节点", http_status=422)
        next_node = next((n for n in nodes if n.get("id") == out_edges[0]), {"id": out_edges[0], "label": out_edges[0]})

        created: list[TaskItem] = []
        for child in children:
            spawned = await self._spawn_task(
                task, project, tpl, next_node,
                title=child.get("title", ""), assignee=child.get("assignee", ""),
                due_hours=child.get("due_hours"), brief=child.get("note", ""),
                parent_task_id=task.id,
            )
            # 每个拆分起点就是一条独立子线 root；其后的普通流转会一直继承此标识
            spawned.lineage_root_id = spawned.id
            created.append(spawned)
        task.status = "completed"
        task.form_values = {
            "__split__": True,
            "split_to": [{"task": c.id, "title": c.title, "assignee": c.assignee} for c in created],
        }
        wi = await self.session.get(WorkItem, task.wi_id)
        if wi:
            wi.progress = f"{next_node.get('label', '')}（{len(created)} 条子线）"
        await AuditService(self.session).record(
            actor=actor.name, action="task:split", target=f"{task.id} · {task.node}",
            result="success", after={"children": [c.id for c in created]},
        )
        await self.session.flush()
        return created

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

    async def latest_published_canvas_nodes(self, tpl: GlobalTemplate) -> list[dict]:
        """引擎视角的节点配置来源：最新 published 画布（与 _node_cfg_of/_edges_of 同一选择规则）。"""
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
                return canvas.nodes
        return []

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
