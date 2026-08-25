"""启动时幂等 seed：角色/权限矩阵/模板 + 可选演示业务数据。

环境变量 FLOWHUB_SEED_DEMO=1 时灌入演示业务数据（项目/工作项/任务/文档/Agent/通知/审计/演示用户）；
默认 0：只初始化系统骨架（角色/权限矩阵/模板 + bootstrap admin 引导账号），业务表保持干净，由用户自建。
"""
import logging
import os
from uuid import uuid4

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.config import get_settings
from flowhub_api.models import (
    Agent, AgentCapability, AgentModel, AgentTool, AgentType, AuditRow, DocItem, GlobalTemplate,
    NodeAssignment, NotificationItem, Project, ProjectTemplateBinding,
    Role, TaskItem, User, WorkItem, WorkflowInstance,
)
from flowhub_api.seed.data import AGENT_MODELS, AGENT_TOOLS, AGENT_TYPES, GLOBAL_TEMPLATES, PERM_MATRIX, ROLE_META, ROLE_ORDER, SEED_USERS
from flowhub_api.seed.demo import (
    CHANGE_START_SCHEMA, DEMO_AGENTS, DEMO_AUDITS, DEMO_DOCUMENTS, DEMO_NOTIFICATIONS,
    DEMO_PROJECTS, DEMO_TASKS, DEMO_WORK_ITEMS, ISSUE_START_SCHEMA, REQ_START_SCHEMA,
)

logger = logging.getLogger(__name__)

TEMPLATE_START_SCHEMA = {"tpl-req": REQ_START_SCHEMA, "tpl-issue": ISSUE_START_SCHEMA, "tpl-change": CHANGE_START_SCHEMA}

SEED_DEMO = os.environ.get("FLOWHUB_SEED_DEMO", "0") == "1"


async def seed_all(session: AsyncSession) -> None:
    # 系统骨架：角色 / 权限矩阵 / 流程模板（任何模式下都初始化）
    await _seed_roles(session)
    await _seed_templates(session)
    await _seed_agent_models(session)
    await _seed_agent_types(session)
    await _seed_agent_tools(session)
    if SEED_DEMO:
        await _seed_users(session)          # 12 个演示用户
        await _seed_projects(session)
        await _seed_work_items(session)
        await _seed_documents(session)
        await _seed_agents(session)
        await _seed_notifications(session)
        await _seed_audits(session)
        logger.info(
            "Seed 演示模式：角色 %d / 用户 %d / 模板 %d / 项目 %d / 工作项 %d / 任务 %d / 文档 %d / Agent %d / 通知 %d / 审计 %d",
            len(ROLE_ORDER), len(SEED_USERS), len(GLOBAL_TEMPLATES), len(DEMO_PROJECTS),
            len(DEMO_WORK_ITEMS), len(DEMO_TASKS), len(DEMO_DOCUMENTS), len(DEMO_AGENTS),
            len(DEMO_NOTIFICATIONS), len(DEMO_AUDITS),
        )
    else:
        # 干净模式：仅引导一个 bootstrap admin（登录后可在组织管理创建其他用户）
        await _seed_bootstrap_admin(session)
        logger.info("Seed 干净模式：角色 %d / 模板 %d / bootstrap admin 已引导（演示数据已禁用）", len(ROLE_ORDER), len(GLOBAL_TEMPLATES))
    await session.commit()


async def _seed_bootstrap_admin(session: AsyncSession) -> None:
    """干净模式：确保存在一个系统管理员账号（settings.bootstrap_admin_*）。"""
    settings = get_settings()
    exists = await session.execute(
        select(User).where(User.account == settings.bootstrap_admin_account)
    )
    if exists.scalar_one_or_none():
        return
    system_admin = await session.get(Role, "system_admin")
    session.add(User(
        id="admin", name=settings.bootstrap_admin_name, account=settings.bootstrap_admin_account,
        password_hash=bcrypt.hashpw(settings.bootstrap_admin_password.encode(), bcrypt.gensalt()).decode("utf-8"),
        dept="平台研发部 / 平台组", roles=[system_admin] if system_admin else [],
        skills=["backend", "devops"], avatar_grad="g6", status="active",
        ding_talk=None, wecom=None, load=0, must_change_password=True,
    ))


async def _seed_roles(session: AsyncSession) -> None:
    for idx, role_id in enumerate(ROLE_ORDER):
        exists = await session.get(Role, role_id)
        if exists:
            continue
        label, desc = ROLE_META[role_id]
        perms = {perm: bool(cells[idx]) for perm, cells in PERM_MATRIX}
        session.add(Role(id=role_id, label=label, desc=desc, builtin=True, perms=perms))


async def _seed_users(session: AsyncSession) -> None:
    for (uid, name, account, dept, roles, skills, grad, status, ding, wecom, load) in SEED_USERS:
        exists = await session.get(User, uid)
        if exists:
            continue
        session.add(User(
            id=uid, name=name, account=account,
            password_hash=bcrypt.hashpw(b"Demo@1234", bcrypt.gensalt()).decode("utf-8"),
            dept=dept, roles=[await _get_or_create_role(session, r) for r in roles],
            skills=skills, avatar_grad=grad, status=status,
            ding_talk=ding, wecom=wecom, load=load,
        ))


async def _get_or_create_role(session: AsyncSession, role_id: str) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        label = ROLE_META.get(role_id, (role_id, ""))[0]
        role = Role(id=role_id, label=label, builtin=True, perms={})
        session.add(role)
        await session.flush()
    return role


async def _seed_templates(session: AsyncSession) -> None:
    for tpl in GLOBAL_TEMPLATES:
        exists = await session.get(GlobalTemplate, tpl["id"])
        if exists:
            # 补 startSchema（原 seed 为空）
            if not exists.start_schema:
                exists.start_schema = TEMPLATE_START_SCHEMA.get(tpl["id"], [])
            continue
        session.add(GlobalTemplate(
            id=tpl["id"], name=tpl["name"], type=tpl["type"],
            versions=tpl["versions"],
            start_schema=TEMPLATE_START_SCHEMA.get(tpl["id"], []),
            nodes=tpl["nodes"],
        ))


async def _seed_agent_models(session: AsyncSession) -> None:
    for m in AGENT_MODELS:
        row = (await session.execute(
            select(AgentModel).where(AgentModel.provider == m["provider"], AgentModel.model == m["model"])
        )).scalar_one_or_none()
        if row:
            # 已有记录仅补描述/label/base_url（migrate 加列后旧行为空）
            if not row.desc:
                row.desc = m.get("desc", "")
            if row.label != m["label"]:
                row.label = m["label"]
            if not row.base_url:
                row.base_url = m.get("base_url", "")
            continue
        session.add(AgentModel(
            id=f"am{uuid4().hex[:6]}", provider=m["provider"], model=m["model"], label=m["label"],
            desc=m.get("desc", ""), base_url=m.get("base_url", ""), status="active",
        ))


async def _seed_agent_types(session: AsyncSession) -> None:
    for t in AGENT_TYPES:
        row = (await session.execute(select(AgentType).where(AgentType.code == t["code"]))).scalar_one_or_none()
        if row:
            # 已有记录仅补 system_prompt（migrate 加列后旧行为空）
            if not row.system_prompt:
                row.system_prompt = t.get("system_prompt", "")
            continue
        session.add(AgentType(
            id=f"at{uuid4().hex[:6]}", code=t["code"], label=t["label"], desc=t["desc"],
            default_caps=t["default_caps"], system_prompt=t.get("system_prompt", ""), status="active",
        ))


async def _seed_agent_tools(session: AsyncSession) -> None:
    from datetime import datetime
    for tool in AGENT_TOOLS:
        exists = await session.execute(select(AgentTool).where(AgentTool.name == tool["name"]))
        if exists.scalar_one_or_none():
            continue
        session.add(AgentTool(
            id=f"tool{uuid4().hex[:4]}", name=tool["name"], engine=tool["engine"],
            provider=tool.get("provider", ""), model=tool.get("model", ""),
            base_url=tool.get("base_url", ""), desc=tool.get("desc", ""),
            status="active", created_at=datetime.now().strftime("%m-%d %H:%M"),
        ))


async def _seed_projects(session: AsyncSession) -> None:
    for p in DEMO_PROJECTS:
        exists = await session.get(Project, p["id"])
        if exists:
            continue
        project = Project(
            id=p["id"], name=p["name"], code=p["code"], status=p["status"], desc=p["desc"],
            members=p["members"], work_items=p["work_items"], progress=p["progress"],
            manager=p["manager"], owner=p["owner"], updated=p["updated"],
            read_only=p.get("read_only", False),
        )
        for b in p["bindings"]:
            tpl_type = {"tpl-req": "requirement", "tpl-issue": "issue", "tpl-change": "change"}.get(b["template_id"], "requirement")
            binding = ProjectTemplateBinding(
                id=f"b{p['id']}-{b['template_id']}", project_id=p["id"],
                template_id=b["template_id"], name=b["template_id"], type=tpl_type,
                version=b["version"], status=b["status"],
            )
            for a in b["assignments"]:
                binding.assignments.append(NodeAssignment(
                    id=f"na{p['id']}-{b['template_id']}-{a['node_id']}",
                    node_id=a["node_id"], node_label=a["node_label"],
                    users=a["users"], roles=a["roles"],
                ))
            project.template_bindings.append(binding)
        session.add(project)


async def _seed_work_items(session: AsyncSession) -> None:
    for w in DEMO_WORK_ITEMS:
        exists = await session.get(WorkItem, w["id"])
        if exists:
            continue
        wi = WorkItem(
            id=w["id"], type=w["type"], title=w["title"], project=w["project"],
            priority=w["priority"], status=w["status"], assignee=w["assignee"],
            creator=w["creator"], due=w["due"], labels=w["labels"],
            progress=w["progress"], start_values=w["start_values"],
        )
        session.add(wi)
        session.add(WorkflowInstance(
            id=f"inst_{w['id']}", work_item_id=w["id"],
            template_id="tpl-req" if w["type"] == "requirement" else "tpl-issue",
            version="v3", state="closed" if w["status"] == "closed" else "running",
        ))
    # 任务（依赖工作项已存在）
    existing_task_ids = set((await session.execute(select(TaskItem.id))).scalars().all())
    for t in DEMO_TASKS:
        if t["id"] in existing_task_ids:
            continue
        session.add(TaskItem(
            id=t["id"], wi_id=t["wi"], title=t["title"], project=t["project"],
            node=t["node"], node_id=t["node_id"], type=t["type"], priority=t["priority"],
            status=t["status"], assignee=t["assignee"], due=t["due"],
            sla_hours=t["sla"], overdue=t.get("overdue", False),
            agent_pending=t.get("agent_pending", False), source=t.get("source", ""),
        ))


async def _seed_documents(session: AsyncSession) -> None:
    for d in DEMO_DOCUMENTS:
        exists = await session.get(DocItem, d["id"])
        if exists:
            continue
        session.add(DocItem(
            id=d["id"], name=d["name"], project=d["project"], version=d["version"],
            level=d["level"], scan=d["scan"], uploader=d["uploader"],
            size=d["size"], time=d["time"], kind=d["kind"], wi=d["wi"],
        ))


async def _seed_agents(session: AsyncSession) -> None:
    for a in DEMO_AGENTS:
        exists = await session.get(Agent, a["id"])
        if exists:
            continue
        agent = Agent(
            id=a["id"], name=a["name"], code=a["code"], desc=a["desc"],
            status=a["status"], scope=a["scope"], bindings=a["bindings"],
            owner=a["owner"], calls=a["calls"], success_rate=a["success_rate"],
            avg_ms=a["avg_ms"], updated=a["updated"],
        )
        for name, mode in [("read_context", "direct"), ("read_documents", "direct"), ("generate_content", "confirm"),
                           ("submit_task", "forbid"), ("return_task", "forbid"), ("transfer_task", "forbid")]:
            agent.capabilities.append(AgentCapability(
                id=f"cap-{a['id']}-{name}", agent_id=a["id"], name=name, mode=mode,
            ))
        session.add(agent)


async def _seed_notifications(session: AsyncSession) -> None:
    for n in DEMO_NOTIFICATIONS:
        exists = await session.get(NotificationItem, n["id"])
        if exists:
            continue
        session.add(NotificationItem(
            id=n["id"], title=n["title"], body=n["body"], time=n["time"],
            channels=n["channels"], kind=n["kind"], unread=n.get("unread", False),
            failed=n.get("failed", False), retries=n.get("retries", 0),
            target_user=n.get("target", ""),
        ))


async def _seed_audits(session: AsyncSession) -> None:
    import hashlib

    existing = (await session.execute(select(AuditRow.id))).scalars().all()
    existing_set = set(existing)
    for a in DEMO_AUDITS:
        key = hashlib.sha1(f"{a['time']}|{a['action']}|{a['target']}".encode()).hexdigest()[:24]
        if key in existing_set:
            continue
        session.add(AuditRow(
            id=key, time=a["time"], actor=a["actor"], actor_type=a["actor_type"],
            action=a["action"], target=a["target"], result=a["result"],
            req_id=a["req_id"], ip=a["ip"], authorized=False,
        ))


def gen_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex[:8]}"
