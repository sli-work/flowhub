"""组织路由（docs/02 §二）：用户 CRUD / 角色技能分配 / 组织同步 / 注册审批。"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizError, BizCode, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import NotificationItem, Role, User, UserRole
from flowhub_api.schemas.api import AdminResetPwdReq, CreateUserReq, UserUpdateReq
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.auth import AuthService

router = APIRouter(prefix="/api/v1/org", tags=["org"])


def _brief(u: User) -> dict:
    return {
        "id": u.id, "name": u.name, "account": u.account, "dept": u.dept, "email": u.email,
        "roles": [r.id for r in u.roles], "skills": u.skills,
        "status": u.status, "avatarGrad": u.avatar_grad, "load": u.load,
        "dingTalk": u.ding_talk, "wecom": u.wecom,
        "mustChangePassword": bool(u.must_change_password),
    }


@router.get("/overview")
async def overview(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    total = (await session.execute(select(func.count(User.id)).where(User.deleted == False))).scalar() or 0  # noqa: E712
    active = (await session.execute(select(func.count(User.id)).where(User.status == "active", User.deleted == False))).scalar() or 0  # noqa: E712
    return ok({
        "departments": len({u.dept.split(" / ")[0] for u in (await session.execute(select(User).where(User.deleted == False))).scalars()}),
        "users": total, "active": active, "pending_approve": 0, "last_sync": "—",
    })


@router.get("/users")
async def list_users(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    dept: str = "", status: str = "", keyword: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    stmt = select(User).where(User.deleted == False)  # noqa: E712
    if dept:
        stmt = stmt.where(User.dept.like(f"%{dept}%"))
    if status:
        stmt = stmt.where(User.status == status)
    if keyword:
        stmt = stmt.where(User.name.contains(keyword) | User.account.contains(keyword))
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return ok({"items": [_brief(u) for u in rows], "total": total, "page": page, "page_size": page_size})


@router.post("/users")
async def create_user(
    body: CreateUserReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:user_manage")
    # 邮箱仅在非空时参与查重（email 默认空串，不能用空值去匹配存量用户）
    cond = User.account == body.account
    if body.email.strip():
        cond = or_(cond, User.email == body.email.strip())
    exists = (await session.execute(
        select(User).where(cond, User.deleted == False)  # noqa: E712
    )).scalar_one_or_none()
    if exists:
        raise BizError(BizCode.DUPLICATE_OPERATION, "账号或邮箱已存在")
    role = await session.get(Role, body.role_id)
    if role is None:
        raise BizError(BizCode.VALIDATION, "角色不存在")
    service = AuthService(session)
    new_user = User(
        id=gen_id("u"), name=body.name, account=body.account, email=body.email,
        password_hash=service.hash_password(body.password), dept=body.dept,
        roles=[role], skills=body.skills, status="invited", must_change_password=True,
    )
    session.add(new_user)
    await AuditService(session).record(
        actor=user.name, action="organization:user_create", target=f"{new_user.name}（{new_user.account}）",
        result="success",
    )
    await session.commit()
    return ok({"user": _brief(new_user)}, "已创建本地用户：初始密码生效，首登强制改密")


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None or target.deleted:
        raise BizError(BizCode.NOT_FOUND, "用户不存在")
    before = _brief(target)
    if body.dept is not None:
        target.dept = body.dept
    if body.email is not None:
        target.email = body.email.strip()
    if body.name is not None and body.name.strip():
        target.name = body.name.strip()
    if body.status is not None:
        target.status = body.status
    if body.skills is not None:
        target.skills = body.skills
    if body.roles is not None:
        roles = []
        for rid in body.roles:
            role = await session.get(Role, rid)
            if role is None:
                raise BizError(BizCode.VALIDATION, f"角色不存在：{rid}")
            roles.append(role)
        target.roles = roles
    await AuditService(session).record(
        actor=user.name, action="organization:user_update",
        target=f"{target.name}（{target.account}）", result="success",
        before=before, after=_brief(target),
    )
    await session.commit()
    return ok({"user": _brief(target)}, "已保存用户配置（角色/技能变更已审计）")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """软删用户：保留审计与历史任务引用，登录与用户列表立即不可见。

    account 加 deleted_ 前缀释放唯一槽位：删除后可用同一账号名/邮箱重建；
    任务/审计中按 user_id 引用不受影响（assignee 存的是姓名快照）。
    """
    auth = build_authorizer(user)
    auth.require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None or target.deleted:
        raise BizError(BizCode.NOT_FOUND, "用户不存在")
    if target.id == user.id:
        raise BizError(BizCode.VALIDATION, "不能删除自己的账号")
    if any(r.id == "system_admin" for r in target.roles) and not any(r.id == "system_admin" for r in user.roles):
        raise BizError(BizCode.PERM_DENIED, "仅系统管理员可删除系统管理员账号", http_status=403)
    before = _brief(target)
    target.deleted = True
    if target.status == "active":
        target.status = "disabled"  # 软删同时停用，防止残留会话继续接任务
    # account 带唯一约束：软删后释放槽位（保留原值于审计 before 中），避免同名重建撞唯一索引
    target.account = f"deleted_{target.id}_{target.account}"[:64]
    target.email = ""
    await AuditService(session).record(
        actor=user.name, action="organization:user_delete",
        target=f"{target.name}（{target.account}）", result="success", before=before,
    )
    await session.commit()
    return ok(message="用户已删除（软删，审计记录保留）")


@router.post("/users/{user_id}/unlock")
async def unlock_user(
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None:
        raise BizError(BizCode.NOT_FOUND, "用户不存在")
    target.status = "active"
    await AuditService(session).record(
        actor=user.name, action="organization:user_unlock", target=target.name, result="success",
    )
    await session.commit()
    return ok(message=f"已解锁 {target.name}：解锁人 {user.name} 已记录审计")


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: str,
    body: AdminResetPwdReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """管理员设置临时密码；不返回或记录明文，目标用户下次登录必须自行改密。"""
    build_authorizer(user).require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None or target.deleted:
        raise BizError(BizCode.NOT_FOUND, "用户不存在")
    if any(role.id == "system_admin" for role in target.roles) and not any(role.id == "system_admin" for role in user.roles):
        raise BizError(BizCode.PERM_DENIED, "仅系统管理员可重置系统管理员密码", http_status=403)
    target.password_hash = AuthService.hash_password(body.new_password)
    target.must_change_password = True
    await AuditService(session).record(
        actor=user.name, action="organization:user_reset_password",
        target=f"{target.name}（{target.account}）", result="success",
        after={"mustChangePassword": True},
    )
    session.add(NotificationItem(
        id=gen_id("n"), title="密码已被管理员重置",
        body=f"管理员 {user.name} 已重置你的本地账号密码。请使用收到的临时密码登录，并立即修改密码。",
        time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=[{"name": "站内", "ok": True}],
        unread=True, kind="info", target_user=target.account,
    ))
    await session.commit()
    return ok({"user": _brief(target)}, f"已重置 {target.name} 的密码；其下次登录必须修改密码")


@router.get("/users/by-role")
async def users_by_role(
    session: Annotated[AsyncSession, Depends(get_db)],
    role: str = Query(...),
    active_only: bool = True,
):
    """角色/技能 → 在职用户（节点绑定解析用）。"""
    all_users = (await session.execute(select(User))).scalars().all()
    matched = [
        u for u in all_users
        if (not active_only or u.status == "active")
        and (role in [r.id for r in u.roles] or role in u.skills)
    ]
    return ok({"role": role, "users": [_brief(u) for u in matched]})


@router.post("/sync")
async def sync_org(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """立即同步组织：调用钉钉 / 企微组织接口拉取通讯录 → upsert 用户（外部 ID 匹配）。

    需要 .env 配置 DINGTALK_APP_KEY/SECRET 或 WECOM_CORP_ID/SECRET；未配置明确提示。
    同步用户：本地已存在（按外部 ID）→ 更新姓名/部门；不存在 → 创建（随机密码、首登改密）。
    """
    auth = build_authorizer(user)
    auth.require("organization:user_manage")
    from flowhub_api.core.config import get_settings

    settings = get_settings()
    results: dict[str, dict] = {}
    if settings.dingtalk_app_key and settings.dingtalk_app_secret:
        results["钉钉"] = await _sync_dingtalk(session)
    if settings.wecom_corp_id and settings.wecom_corp_secret:
        results["企微"] = await _sync_wecom(session)
    if not results:
        raise BizError(
            BizCode.VALIDATION,
            "未配置组织同步凭证：请在 backend/.env 配置 DINGTALK_APP_KEY/SECRET 或 WECOM_CORP_ID/SECRET 后重试",
        )
    synced = sum(r.get("synced", 0) for r in results.values())
    failed = [k for k, r in results.items() if not r.get("ok")]
    await AuditService(session).record(
        actor=user.name, action="org:sync",
        target=f"同步用户 {synced} 人 · {', '.join(results.keys())}" + (f" · 失败: {', '.join(failed)}" if failed else ""),
        result="failed" if failed else "success",
    )
    await session.commit()
    message = f"组织同步完成：共处理 {synced} 人" + (f"，失败渠道 {', '.join(failed)}" if failed else "（全部成功）")
    return ok({"results": results, "last_sync": "刚刚", "synced": synced}, message)


async def _upsert_synced_user(session: AsyncSession, ext_field: str, ext_id: str, name: str, mobile: str, dept: str) -> bool:
    """按外部 ID（ding_talk/wecom）upsert 用户。返回是否新建。"""
    from flowhub_api.services.auth import AuthService

    q = select(User)
    if ext_field == "ding_talk":
        q = q.where(User.ding_talk == ext_id)
    else:
        q = q.where(User.wecom == ext_id)
    existing = (await session.execute(q)).scalar_one_or_none()
    if existing:
        existing.name = name or existing.name
        if dept:
            existing.dept = dept
        if existing.status == "invited":
            existing.status = "active"
        return False
    account = mobile or f"{ext_field.split('_')[0]}_{ext_id}"
    u = User(
        id=gen_id("u"), account=account, name=name or account,
        dept=dept or "未分配", status="active",
        password_hash=AuthService.hash_password(__import__("uuid").uuid4().hex[:12]),
        must_change_password=True,
    )
    if ext_field == "ding_talk":
        u.ding_talk = ext_id
    else:
        u.wecom = ext_id
    session.add(u)
    return True


async def _sync_dingtalk(session: AsyncSession) -> dict:
    """钉钉组织同步：gettoken → 部门 → 每部门用户列表 → upsert。"""
    import httpx

    from flowhub_api.core.config import get_settings

    s = get_settings()
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        r = await c.get("https://oapi.dingtalk.com/gettoken",
                        params={"appkey": s.dingtalk_app_key, "appsecret": s.dingtalk_app_secret})
        token = r.json().get("access_token")
        if not token:
            return {"ok": False, "synced": 0, "error": r.json().get("errmsg", "获取 access_token 失败")}
        dept_resp = (await c.post("https://oapi.dingtalk.com/topapi/v2/department/listsub",
                                  params={"access_token": token}, json={"dept_id": 1})).json()
        depts = (dept_resp.get("result") or {}).get("list") or []
        dept_ids = [d.get("dept_id") for d in depts] + [1]
        synced = 0
        for did in dept_ids:
            body = {"dept_id": did, "cursor": 0, "size": 100}
            resp = (await c.post("https://oapi.dingtalk.com/topapi/v2/user/list",
                                 params={"access_token": token}, json=body)).json()
            for u in (resp.get("result") or {}).get("list") or []:
                ext_id = str(u.get("userid", ""))
                if not ext_id:
                    continue
                created = await _upsert_synced_user(
                    session, "ding_talk", ext_id, u.get("name", ""), str(u.get("mobile", "")),
                    " / ".join(x for x in [u.get("dept_name", ""), "钉钉同步"] if x),
                )
                synced += 1
        return {"ok": True, "synced": synced, "departments": len(dept_ids)}


async def _sync_wecom(session: AsyncSession) -> dict:
    """企微组织同步：gettoken → 通讯录 user/list（fetch_child）→ upsert。"""
    import httpx

    from flowhub_api.core.config import get_settings

    s = get_settings()
    async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
        r = await c.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                        params={"corpid": s.wecom_corp_id, "corpsecret": s.wecom_corp_secret})
        token = r.json().get("access_token")
        if not token:
            return {"ok": False, "synced": 0, "error": r.json().get("errmsg", "获取 access_token 失败")}
        resp = (await c.get("https://qyapi.weixin.qq.com/cgi-bin/user/list",
                            params={"access_token": token, "department_id": 1, "fetch_child": 1})).json()
        synced = 0
        for u in resp.get("userlist") or []:
            ext_id = u.get("userid", "")
            if not ext_id:
                continue
            created = await _upsert_synced_user(
                session, "wecom", ext_id, u.get("name", ""), str(u.get("mobile", "")),
                " / ".join(x for x in ["企微同步", " / ".join(str(d) for d in (u.get("department") or []))] if x),
            )
            synced += 1
        return {"ok": resp.get("errcode", 0) == 0, "synced": synced, "error": resp.get("errmsg", "") if resp.get("errcode") else ""}
