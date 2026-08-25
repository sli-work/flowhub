"""项目路由（docs/02 §三）：项目 CRUD + 模板绑定（含节点处理人绑定）。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizError, BizCode, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import GlobalTemplate, NodeAssignment, Project, ProjectTemplateBinding, User, WorkItem
from flowhub_api.schemas.api import ProjectUpsertReq
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _binding_dict(b: ProjectTemplateBinding) -> dict:
    return {
        "templateId": b.template_id, "name": b.name, "type": b.type,
        "version": b.version, "status": b.status,
        "assignments": [
            {"nodeId": a.node_id, "nodeLabel": a.node_label, "users": a.users, "roles": a.roles}
            for a in b.assignments
        ],
    }


async def _project_bindings(session: AsyncSession, p: Project) -> list[dict]:
    """查询驱动获取模板绑定快照：不依赖对象 lazy 关系加载状态（create/update 审计时对象可能未 flush）。"""
    binds = (await session.execute(
        select(ProjectTemplateBinding)
        .options(selectinload(ProjectTemplateBinding.assignments))
        .where(ProjectTemplateBinding.project_id == p.id)
    )).scalars().all()
    return [_binding_dict(b) for b in binds]


async def _project_stats(
    session: AsyncSession, p: Project, users: list[User] | None = None,
) -> dict:
    """项目卡片真实统计（替代存储的假值）：工作项数 / 成员数 / 进度。

    - workItems：按项目名统计 work_items 表中真实工作项数量
    - members：节点绑定处理人（显式用户 + 角色/技能解析出的在职用户）去重
    - progress：已完成（closed）工作项占比
    """
    wi_count = (await session.execute(
        select(func.count(WorkItem.id)).where(WorkItem.project == p.name)
    )).scalar() or 0
    wi_closed = (await session.execute(
        select(func.count(WorkItem.id)).where(WorkItem.project == p.name, WorkItem.status == "closed")
    )).scalar() or 0
    progress = round(wi_closed / wi_count * 100) if wi_count else 0

    all_users = users if users is not None else (await session.execute(select(User))).scalars().all()
    member_ids: set[str] = set()
    role_tags: set[str] = set()
    # 查询驱动：避免访问未加载的 lazy 关系（async 下 MissingGreenlet）
    assigns = (await session.execute(
        select(NodeAssignment)
        .join(ProjectTemplateBinding, ProjectTemplateBinding.id == NodeAssignment.binding_id)
        .where(ProjectTemplateBinding.project_id == p.id, ProjectTemplateBinding.status == "active")
    )).scalars().all()
    for a in assigns:
        member_ids.update(a.users or [])
        role_tags.update(a.roles or [])
    if role_tags:
        for u in all_users:
            if u.status == "active" and (
                {r.id for r in u.roles} & role_tags or set(u.skills or []) & role_tags
            ):
                member_ids.add(u.id)
    return {"members": len(member_ids), "workItems": wi_count, "progress": progress}


async def _project_dict(session: AsyncSession, p: Project, users: list[User] | None = None) -> dict:
    stats = await _project_stats(session, p, users)
    return {
        "id": p.id, "name": p.name, "code": p.code, "status": p.status,
        "desc": p.desc, "members": stats["members"], "workItems": stats["workItems"],
        "progress": stats["progress"], "manager": p.manager, "owner": p.owner,
        "updated": p.updated, "readOnly": p.read_only,
        "templateBindings": await _project_bindings(session, p),
    }


async def _upsert_bindings(session: AsyncSession, project: Project, bindings: list) -> None:
    project.template_bindings.clear()
    for i, b in enumerate(bindings):
        tpl = await session.get(GlobalTemplate, b.template_id)
        if tpl is None:
            raise BizError(BizCode.VALIDATION, f"模板不存在：{b.template_id}")
        binding = ProjectTemplateBinding(
            id=gen_id("b"), project_id=project.id, template_id=b.template_id,
            name=tpl.name, type=tpl.type, version=b.version or tpl.versions[-1],
            status=b.status if b.status in ("active", "disabled") else "active",
        )
        for a in b.assignments:
            binding.assignments.append(NodeAssignment(
                id=gen_id("na"), node_id=a.node_id, node_label=a.node_label,
                users=a.users, roles=a.roles,
            ))
        project.template_bindings.append(binding)


@router.get("")
async def list_projects(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    # eager load 绑定+处理人：async 下避免事务结束后懒加载崩溃（MissingGreenlet）
    rows = (await session.execute(
        select(Project)
        .options(selectinload(Project.template_bindings).selectinload(ProjectTemplateBinding.assignments))
        .order_by(Project.updated.desc())
    )).scalars().all()
    # 一次性加载全部用户供角色→成员解析复用，避免 N+1
    users = (await session.execute(select(User))).scalars().all()
    return ok({"items": [await _project_dict(session, p, users) for p in rows], "total": len(rows)})


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    p = (await session.execute(
        select(Project)
        .options(selectinload(Project.template_bindings).selectinload(ProjectTemplateBinding.assignments))
        .where(Project.id == project_id)
    )).scalar_one_or_none()
    if p is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    return ok({"item": await _project_dict(session, p)})


@router.post("")
async def create_project(
    body: ProjectUpsertReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("project:create")
    exists = (await session.execute(select(Project).where(Project.code == body.code.upper()))).scalar_one_or_none()
    if exists:
        raise BizError(BizCode.DUPLICATE_OPERATION, f"项目编码已存在：{body.code.upper()}")
    project = Project(
        id=gen_id("p"), name=body.name.strip(), code=body.code.strip().upper(),
        status=body.status, desc=body.desc, manager=body.manager,
        owner="平台研发部", updated="刚刚",
    )
    await _upsert_bindings(session, project, body.template_bindings)
    session.add(project)
    await AuditService(session).record(
        actor=user.name, action="project:create", target=f"{project.name}（{project.code}）",
        result="success", after=await _project_dict(session, project),
    )
    await session.commit()
    # commit 后重新 eager-load 序列化（避免事务结束后懒加载崩溃）
    fresh = (await session.execute(
        select(Project)
        .options(selectinload(Project.template_bindings).selectinload(ProjectTemplateBinding.assignments))
        .where(Project.id == project.id)
    )).scalar_one()
    return ok({"item": await _project_dict(session, fresh)}, "已创建项目")


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpsertReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("project:update")
    project = (await session.execute(
        select(Project)
        .options(selectinload(Project.template_bindings).selectinload(ProjectTemplateBinding.assignments))
        .where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    if project.read_only or project.status == "archived":
        raise BizError(BizCode.FORBIDDEN, "归档项目只读")
    before = await _project_dict(session, project)
    project.name = body.name.strip()
    project.code = body.code.strip().upper()
    project.status = body.status
    project.desc = body.desc
    project.manager = body.manager
    project.updated = "刚刚"
    await _upsert_bindings(session, project, body.template_bindings)
    await AuditService(session).record(
        actor=user.name, action="project:update", target=f"{project.name}（{project.code}）",
        result="success", before=before, after=await _project_dict(session, project),
    )
    await session.commit()
    # commit 后重新 eager-load 序列化（避免事务结束后懒加载崩溃）
    fresh = (await session.execute(
        select(Project)
        .options(selectinload(Project.template_bindings).selectinload(ProjectTemplateBinding.assignments))
        .where(Project.id == project.id)
    )).scalar_one()
    return ok({"item": await _project_dict(session, fresh)}, "已保存项目配置：模板绑定变更已写入审计（含 before/after）")


@router.post("/{project_id}/archive")
async def archive_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """归档项目（业务终止）：级联冻结其下进行中的工作项与流程实例（→ archived），
    未完成任务保留原状态但禁止再流转（task_action 拦截）；历史数据只读可查。"""
    auth = build_authorizer(user)
    auth.require("project:archive")
    project = await session.get(Project, project_id)
    if project is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    if project.status == "archived":
        raise BizError(BizCode.DUPLICATE_OPERATION, "项目已归档", http_status=409)
    from flowhub_api.models import WorkItem, WorkflowInstance

    frozen = 0
    rows = (await session.execute(
        select(WorkItem).where(WorkItem.project == project.name)
    )).scalars().all()
    non_terminal = [w for w in rows if w.status not in ("closed", "cancelled", "archived")]
    for w in non_terminal:
        w.status = "archived"
        frozen += 1
    if frozen:
        inst_rows = (await session.execute(
            select(WorkflowInstance).where(WorkflowInstance.work_item_id.in_([w.id for w in non_terminal]))
        )).scalars().all()
        for inst in inst_rows:
            if inst.state == "running":
                inst.state = "archived"
    project.status = "archived"
    project.read_only = True
    await AuditService(session).record(
        actor=user.name, action="project:archive",
        target=f"{project.name}（冻结 {frozen} 个进行中流程）", result="success",
    )
    await session.commit()
    return ok(
        message=f"项目已归档（只读）：已冻结 {frozen} 个进行中流程，历史任务保留但不可再流转",
    )


@router.post("/{project_id}/cancel")
async def cancel_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("project:archive")
    project = await session.get(Project, project_id)
    if project is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    project.status = "cancelled"
    await AuditService(session).record(
        actor=user.name, action="project:cancel", target=project.name, result="success",
    )
    await session.commit()
    return ok(message="项目已取消")


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """删除项目：仅【归档】状态可删除（未归档 409）；仍关联工作项的归档项目拒绝删除；
    删除时级联清理模板绑定与节点处理人配置。
    """
    auth = build_authorizer(user)
    auth.require("project:archive")
    project = await session.get(Project, project_id)
    if project is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    if project.status != "archived":
        raise BizError(BizCode.FORBIDDEN, "仅归档项目可删除：请先归档项目（操作 → 归档）", http_status=409)
    wi_count = (await session.execute(
        select(func.count(WorkItem.id)).where(WorkItem.project == project.name)
    )).scalar()
    if wi_count:
        raise BizError(BizCode.FORBIDDEN, f"项目仍关联 {wi_count} 个工作项，无法删除", http_status=409)
    # 级联清理：节点处理人配置 → 模板绑定 → 项目
    await session.execute(
        text("DELETE FROM node_assignments WHERE binding_id IN (SELECT id FROM project_template_bindings WHERE project_id = :p)"),
        {"p": project_id},
    )
    await session.execute(text("DELETE FROM project_template_bindings WHERE project_id = :p"), {"p": project_id})
    name = project.name
    await session.delete(project)
    await AuditService(session).record(
        actor=user.name, action="project:delete", target=f"{name}（{project_id}）", result="success",
    )
    await session.commit()
    return ok(message=f"已删除项目「{name}」（仅归档项目可删除，绑定与处理人配置已清理）")
