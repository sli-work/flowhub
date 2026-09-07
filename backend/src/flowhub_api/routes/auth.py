"""认证路由（docs/02 §一）：登录 / 注册 / 改密 / 免登占位。"""
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import NotificationItem, User
from flowhub_api.schemas.api import ChangePwdReq, LoginReq, RegisterReq, RejectRegistrationReq, SsoVerifyReq, ThemePreferenceReq
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_brief(user: User) -> dict:
    return {
        "id": user.id, "name": user.name, "account": user.account,
        "dept": user.dept, "status": user.status,
        "roles": [r.id for r in user.roles],
        "skills": user.skills,
        "theme": user.theme or "dark",
        # 首次登录强制改密标记（前端据此决定是否弹改密框）
        "mustChangePassword": bool(user.must_change_password),
    }


@router.post("/login")
async def login(body: LoginReq, session: Annotated[AsyncSession, Depends(get_db)], request: Request):
    service = AuthService(session)
    try:
        user, token = await service.login(body.account, body.password)
    except BizError:
        await AuditService(session).record(
            actor=body.account, actor_type="system", action="auth:login",
            target="本地账号登录", result="failed", ip=request.client.host if request.client else None,
        )
        await session.commit()
        raise
    await AuditService(session).record(
        actor=user.name, action="auth:login", target=f"本地账号 {user.account}", result="success",
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return ok({"token": token, "user": _user_brief(user)}, "登录成功")


@router.get("/sso/{provider}/start")
async def sso_start(provider: str, return_to: str = "/"):
    from flowhub_api.services.enterprise_sso import authorization_url

    return ok({"authorization_url": authorization_url(provider, return_to)})


@router.post("/sso/verify")
async def sso_verify(body: SsoVerifyReq, session: Annotated[AsyncSession, Depends(get_db)], request: Request):
    """校验 OAuth state，使用企业授权码映射同步后的外部 userid。"""
    from flowhub_api.services.enterprise_sso import external_user_id, validate_state

    validate_state(body.provider, body.state)
    external_id = await external_user_id(body.provider, body.code)
    field = User.wecom if body.provider == "wecom" else User.ding_talk
    user = (await session.execute(select(User).where(field == external_id))).scalar_one_or_none()
    if user is None:
        raise BizError(BizCode.NOT_FOUND, "该企业账号尚未同步或未绑定 FlowHub 用户")
    service = AuthService(session)
    if user.status != "active":
        raise BizError(BizCode.FORBIDDEN, "该 FlowHub 账号当前不可用", http_status=403)
    token = service.create_token(user.id)
    await AuditService(session).record(actor=user.name, action="auth:sso_login", target=body.provider, result="success", ip=request.client.host if request.client else None)
    await session.commit()
    return ok({"token": token, "user": _user_brief(user)}, "企业免登成功")


@router.post("/register")
async def register(body: RegisterReq, session: Annotated[AsyncSession, Depends(get_db)]):
    service = AuthService(session)
    user = await service.register(body.account, body.name, body.email, body.dept, body.role_id, body.password)
    notification = NotificationItem(
        id=gen_id("n"), title="注册待审批", body=f"{body.name} 提交了本地账号注册申请",
        time="刚刚", channels=[{"name": "站内", "ok": True}], kind="info", target_user="admin",
    )
    session.add(notification)
    await session.commit()
    from flowhub_api.routes.notifications import publish_notification

    await publish_notification(notification)
    return ok({"user": _user_brief(user)}, "注册成功，等待管理员审批")


@router.get("/approvals")
async def list_approvals(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """注册审批列表：invited 状态的用户（docs/02 §1.5）。"""
    rows = (await session.execute(
        select(User).where(User.status == "invited").order_by(User.id)
    )).scalars().all()
    return ok({
        "items": [
            {"id": u.id, "name": u.name, "account": u.account, "email": u.account, "dept": u.dept,
             "role": u.roles[0].id if u.roles else "", "applied": "待审批"}
            for u in rows
        ],
    })


@router.post("/approvals/{user_id}/approve")
async def approve_registration(
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """审批通过：invited → active，创建凭证并发送欢迎通知（站内 + 邮件）。"""
    from flowhub_api.authz.authorizer import build_authorizer
    from flowhub_api.services.notify import deliver_channels, welcome_email_body

    build_authorizer(user).require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None:
        raise BizError(BizCode.NOT_FOUND, "申请不存在")
    if target.status != "invited":
        raise BizError(BizCode.DUPLICATE_OPERATION, "该申请已处理")
    target.status = "active"
    target.must_change_password = True
    await AuditService(session).record(
        actor=user.name, action="auth:registration_approve",
        target=f"{target.name}（{target.account}）", result="success",
    )
    # 欢迎通知：站内 + 邮件（邮件未配置时 deliver_channels 自动跳过）
    results = await deliver_channels(
        "欢迎加入 FlowHub",
        welcome_email_body(target.name),
        target.email or None,  # 邮件渠道收件人（未填邮箱时自动跳过邮件渠道）
    )
    session.add(NotificationItem(
        id=gen_id("n"), title="账号已激活", body="管理员已激活你的账号，登录后请先修改初始密码。详见欢迎邮件。",
        time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=results, unread=True, kind="complete", target_user=target.account,
    ))
    await session.commit()
    return ok({"channels": results}, message=f"审批通过：{target.name} 已激活，欢迎通知已发送")


@router.post("/approvals/{user_id}/reject")
async def reject_registration(
    user_id: str,
    body: RejectRegistrationReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """拒绝注册申请：记录原因、通知申请人，并保留审计以便其修正资料后重新提交。"""
    from flowhub_api.authz.authorizer import build_authorizer
    from flowhub_api.services.notify import deliver_channels

    build_authorizer(user).require("organization:user_manage")
    target = await session.get(User, user_id)
    if target is None:
        raise BizError(BizCode.NOT_FOUND, "申请不存在")
    if target.status != "invited" or target.deleted:
        raise BizError(BizCode.DUPLICATE_OPERATION, "该申请已处理")

    reason = body.reason.strip()
    if not reason:
        raise BizError(BizCode.VALIDATION, "拒绝原因不能为空")
    results = await deliver_channels("FlowHub 注册申请未通过", f"你的注册申请未通过。原因：{reason}\n\n你可修正资料后重新提交申请。", target.email or None)
    target.status = "disabled"
    target.deleted = True
    await AuditService(session).record(
        actor=user.name, action="auth:registration_reject",
        target=f"{target.name}（{target.account}）", result="success",
        after={"reason": reason},
    )
    notification = NotificationItem(
        id=gen_id("n"), title="注册申请未通过",
        body=f"管理员拒绝了你的注册申请。原因：{reason}。你可修正资料后重新提交。",
        time=datetime.now(UTC).strftime("%m-%d %H:%M"), channels=results, unread=True,
        kind="fail", target_user=target.account,
    )
    session.add(notification)
    await session.commit()
    from flowhub_api.routes.notifications import publish_notification

    await publish_notification(notification)
    return ok({"channels": results}, message=f"已拒绝 {target.name} 的注册申请，并已通知申请人")


@router.post("/change-password")
async def change_password(
    body: ChangePwdReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    service = AuthService(session)
    if not user.password_hash or not service.verify_password(body.old_password, user.password_hash):
        raise BizError(BizCode.VALIDATION, "原密码错误")
    user.password_hash = service.hash_password(body.new_password)
    user.must_change_password = False
    await AuditService(session).record(
        actor=user.name, action="auth:change_password", target=user.account, result="success",
    )
    await session.commit()
    return ok(message="密码已修改")


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]):
    return ok({"user": _user_brief(user)})


@router.patch("/me/preferences")
async def update_preferences(
    body: ThemePreferenceReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    user.theme = body.theme
    await AuditService(session).record(
        actor=user.name, action="auth:theme_preference", target=user.account,
        result="success", after={"theme": body.theme},
    )
    await session.commit()
    return ok({"user": _user_brief(user)}, "主题偏好已保存")
