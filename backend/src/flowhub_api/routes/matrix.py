"""权限矩阵路由（docs/03 §2-5）：角色 CRUD / 权限调整 / 成员分配 / 防自锁。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowhub_api.authz.authorizer import build_authorizer, ensure_no_self_lock, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import Role, User
from flowhub_api.schemas.api import RoleUpsertReq
from flowhub_api.seed.data import PERM_MATRIX, ROLE_META, ROLE_ORDER
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/matrix", tags=["matrix"])


async def _get_role(session: AsyncSession, role_id: str) -> Role | None:
    """查询角色并 eager-load members：async 下避免事务结束后懒加载 MissingGreenlet（BUG-B 修复）。"""
    return (await session.execute(
        select(Role).options(selectinload(Role.members)).where(Role.id == role_id)
    )).scalar_one_or_none()


def _role_dict(r: Role) -> dict:
    members = [{"id": u.id, "name": u.name} for u in r.members]
    return {
        "id": r.id, "label": r.label, "desc": r.desc, "builtin": r.builtin,
        "perms": r.perms, "members": members, "memberCount": len(members),
    }


@router.get("/roles")
async def list_roles(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("organization:role_manage")
    roles = (await session.execute(select(Role).options(selectinload(Role.members)))).scalars().all()
    return ok({
        "items": [_role_dict(r) for r in roles],
        "totalPerms": len(PERM_MATRIX),
        "roleOrder": ROLE_ORDER,
        "permMatrix": [{"perm": p, "cells": [bool(c) for c in cells]} for p, cells in PERM_MATRIX],
    })


@router.post("/roles")
async def create_role(
    body: RoleUpsertReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:role_manage")
    exists = await session.get(Role, body.id)
    if exists:
        raise BizError(BizCode.DUPLICATE_OPERATION, "角色标识已存在")
    if body.copy_from:
        src = await session.get(Role, body.copy_from)
        perms = dict(src.perms) if src else {}
    else:
        perms = body.perms or {p: False for p, _ in PERM_MATRIX}
    role = Role(id=body.id, label=body.label, desc=body.desc, builtin=False, perms=perms)
    # 显式初始化 members 集合：避免 commit 后序列化时触发懒加载（MissingGreenlet）
    role.members = list((await session.execute(select(User).where(User.id.in_(body.members)))).scalars().all()) if body.members else []
    session.add(role)
    await AuditService(session).record(
        actor=user.name, action="organization:role_create", target=f"{role.label}（{role.id}）", result="success",
    )
    await session.commit()
    return ok({"role": _role_dict(role)}, "已创建自定义角色")


@router.patch("/roles/{role_id}")
async def update_role(
    role_id: str,
    body: RoleUpsertReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:role_manage")
    role = await _get_role(session, role_id)
    if role is None:
        raise BizError(BizCode.NOT_FOUND, "角色不存在")
    before = _role_dict(role)
    if body.label:
        role.label = body.label
    if body.desc is not None:
        role.desc = body.desc
    if body.perms is not None:
        for perm, val in body.perms.items():
            if role.id == "system_admin" and perm == "organization:user_manage" and not val:
                ensure_no_self_lock(role.id, perm)  # 防自锁
            role.perms[perm] = val
    if body.members is not None:
        users = (await session.execute(select(User).where(User.id.in_(body.members)))).scalars().all()
        role.members = list(users)
    await AuditService(session).record(
        actor=user.name, action="organization:role_update",
        target=f"{role.label}（{role.id}）", result="success", before=before, after=_role_dict(role),
    )
    await session.commit()
    return ok({"role": _role_dict(role)}, "角色已更新")


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:role_manage")
    role = await _get_role(session, role_id)
    if role is None:
        raise BizError(BizCode.NOT_FOUND, "角色不存在")
    if role_id == "system_admin":
        raise BizError(BizCode.FORBIDDEN, "系统管理员角色不可删除（防自锁）")
    affected = len(role.members)
    await AuditService(session).record(
        actor=user.name, action="organization:role_delete",
        target=f"{role.label}（{role.id}）· 影响 {affected} 位成员 · 内置={role.builtin}",
        result="success", before=_role_dict(role),
    )
    await session.delete(role)
    await session.commit()
    return ok(message=f"已删除角色「{role.label}」：影响 {affected} 位成员，历史审计保留")


@router.post("/roles/{role_id}/members")
async def set_role_members(
    role_id: str,
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """角色维度分配用户（与组织管理用户编辑双向同步，docs/03 §6）。"""
    auth = build_authorizer(user)
    auth.require("organization:role_manage")
    role = await _get_role(session, role_id)
    if role is None:
        raise BizError(BizCode.NOT_FOUND, "角色不存在")
    user_ids = payload.get("user_ids", [])
    users = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    role.members = list(users)
    await AuditService(session).record(
        actor=user.name, action="organization:role_members",
        target=f"{role.label}（{role.id}）· {len(users)} 位成员", result="success",
    )
    await session.commit()
    return ok({"role": _role_dict(role)}, "角色成员已更新（与用户维度同步）")
