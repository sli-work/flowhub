"""通知路由（docs/06 §二）：列表 / 实时推送 / 已读 / 渠道健康 / 重试。"""
import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import NotificationItem, RuntimeConfig, User
from flowhub_api.services.audit import AuditService
from flowhub_api.services.notify import channel_health, deliver_channels
from flowhub_api.services.notification_stream import notification_stream_hub

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

_CONFIG_KEYS = {
    "public_base_url": False, "dingtalk_app_key": False, "dingtalk_app_secret": True, "dingtalk_agent_id": False,
    "wecom_corp_id": False, "wecom_app_secret": True, "wecom_agent_id": False,
    # 邮件渠道（SMTP）：页面上可直接配置，敏感项（密码/授权码）密文保存不回显
    "smtp_host": False, "smtp_port": False, "smtp_user": False, "smtp_password": True, "smtp_from": False,
}


def _brief(n: NotificationItem) -> dict:
    return {
        "id": n.id, "title": n.title, "body": n.body, "time": n.time,
        "channels": n.channels, "unread": n.unread, "kind": n.kind,
        "failed": n.failed, "retries": n.retries,
        "wiId": n.wi_id, "taskId": n.task_id,
    }


async def publish_notification(n: NotificationItem) -> None:
    """事务提交后分发，保证客户端收到的通知已可通过列表接口查询。"""
    await notification_stream_hub.publish(_brief(n), n.target_user)


@router.get("/stream")
async def stream_notifications(
    user: Annotated[User, Depends(get_current_user)],
):
    """SSE 站内信流；认证沿用 Bearer token，避免在 URL 中暴露凭证。"""
    queue = await notification_stream_hub.subscribe({user.name, user.account})

    async def events():
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"event: notification\ndata: {json.dumps(notification, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await notification_stream_hub.unsubscribe(queue)

    return StreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("")
async def list_notifications(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    kind: str = "", unread: bool | None = None, failed: bool | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    def _scope():
        return NotificationItem.target_user.in_([user.name, user.account, "admin", ""])

    conds = [_scope()]
    if kind:
        conds.append(NotificationItem.kind == kind)
    if unread is not None:
        conds.append(NotificationItem.unread == unread)
    if failed is not None:
        conds.append(NotificationItem.failed == failed)
    stmt = select(NotificationItem).where(*conds)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(NotificationItem.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    # Tab 计数聚合（与用户可见范围一致，一条 SQL 4 个计数）：all / unread / agent / failed
    all_count, unread_n, agent_n, failed_n = (await session.execute(
        select(
            func.count(NotificationItem.id),
            func.count(NotificationItem.id).filter(NotificationItem.unread == True),  # noqa: E712
            func.count(NotificationItem.id).filter(NotificationItem.kind == "agent"),
            func.count(NotificationItem.id).filter(NotificationItem.failed == True),  # noqa: E712
        ).where(_scope())
    )).one()
    return ok({"items": [_brief(n) for n in rows], "total": total, "unread_count": unread_n, "page": page, "page_size": page_size,
               "stats": {"all": all_count, "unread": unread_n, "agent": agent_n, "failed": failed_n}})


@router.post("/read")
async def mark_read(
    payload: dict,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    ids = payload.get("ids") or []
    stmt = select(NotificationItem)
    if ids:
        stmt = stmt.where(NotificationItem.id.in_(ids))
    rows = (await session.execute(stmt)).scalars().all()
    for n in rows:
        n.unread = False
    await session.commit()
    return ok(message=f"已标记 {len(rows)} 条已读")


@router.get("/channels/health")
async def channels_health(
    _: Annotated[User, Depends(get_current_user)],
):
    """渠道可用性（docs/02 §通知）：钉钉 / 企微 / 邮件 / 站内配置状态。"""
    return ok({"channels": channel_health()})


@router.get("/channels/config")
async def get_channel_config(user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("organization:user_manage")
    rows = {row.key: row for row in (await session.execute(select(RuntimeConfig).where(RuntimeConfig.key.in_(_CONFIG_KEYS)))).scalars().all()}
    from flowhub_api.services.runtime_config import settings as runtime_settings
    s = runtime_settings()
    return ok({"values": {key: ("" if secret else str(getattr(s, key, ""))) for key, secret in _CONFIG_KEYS.items() if not secret}, "configured": {key: bool(rows.get(key) or getattr(s, key, "")) for key in _CONFIG_KEYS}})


@router.put("/channels/config")
async def put_channel_config(payload: dict, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("organization:user_manage")
    values = payload.get("values") or {}
    from flowhub_api.services.crypto import encrypt_secret
    from flowhub_api.services.runtime_config import update_cache
    saved: dict[str, str] = {}
    for key, secret in _CONFIG_KEYS.items():
        if key not in values or not isinstance(values[key], str):
            continue
        raw = values[key].strip()
        if secret and not raw:  # 空值表示不覆盖已有密钥
            continue
        row = await session.get(RuntimeConfig, key)
        stored = encrypt_secret(raw) if secret else raw
        if row is None:
            session.add(RuntimeConfig(key=key, value=stored, secret=secret))
        else:
            row.value, row.secret = stored, secret
        saved[key] = raw
    await session.commit()
    update_cache(saved)
    return ok(message="企业应用配置已保存，敏感凭证不会回显")


@router.post("/channels/test")
async def test_channels(
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """向所有已配置渠道发送一条连通性测试消息（真实投递），返回各渠道结果。

    payload.email 可选：填写后同时验证邮件渠道（SMTP 真实发送到该地址）；
    未配置 / 无收件人的渠道自动跳过，不报错。
    同时落库一条站内测试通知（channels 记录各渠道投递结果），通知中心可查看。
    """
    from datetime import UTC, datetime

    from flowhub_api.seed.init import gen_id

    email = (payload or {}).get("email") or ""
    results = await deliver_channels(
        "FlowHub 通知渠道连通性测试",
        f"这是一条来自 FlowHub 的渠道连通性测试消息（发起人 {user.name}，时间见站内）",
        email or None,
    )
    summary = " / ".join(f"{r['name']}{'✓' if r['ok'] else '✗'}" for r in results)
    notification = NotificationItem(
        id=gen_id("ntf"), title="通知渠道连通性测试",
        body=f"发起人 {user.name} · 渠道投递：{summary}",
        time=datetime.now(UTC).strftime("%m-%d %H:%M"),
        channels=results, kind="info", unread=False,
        target_user=user.account,
    )
    session.add(notification)
    await session.commit()
    await publish_notification(notification)
    return ok(
        {"results": results, "channels": channel_health()},
        "测试消息已发送：已投递到配置渠道并记录到通知中心",
    )


@router.post("/{notify_id}/retry")
async def retry_notify(
    notify_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    n = await session.get(NotificationItem, notify_id)
    if n is None:
        raise BizError(BizCode.NOT_FOUND, "通知不存在")
    n.retries += 1
    # 真实重发已启用渠道（站内恒达；webhook/SMTP 重新投递，失败渠道保留标记可继续重试）
    recipient = (await session.execute(
        select(User).where(User.account == n.target_user)
    )).scalar_one_or_none()
    fresh = await deliver_channels(n.title, n.body, recipient)
    n.channels = fresh
    n.failed = any(not c["ok"] for c in fresh)
    await AuditService(session).record(
        actor="Worker", actor_type="system", action="notification:retry", target=n.title,
        result="failed" if n.failed else "success",
    )
    await session.commit()
    if n.failed:
        return ok({"retries": n.retries}, "重试失败：部分渠道仍不可达（已记录审计，可再次重试）")
    return ok({"retries": n.retries}, "重试成功：通知已重新投递")
