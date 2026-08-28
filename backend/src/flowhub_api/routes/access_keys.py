"""用户 access key 管理路由（外部 Agent MCP/Skill 接入凭证）。

管理端点用 JWT（get_current_user）鉴权，仅本人可管理自己的 key；
明文 `sk_xxx` 仅在创建时返回一次（服务端只存 bcrypt 哈希）。
"""
import secrets
from datetime import datetime
from typing import Annotated
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import User, UserApiKey
from flowhub_api.services.audit import AuditService
from flowhub_api.services.crypto import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/api/v1/access-keys", tags=["access-keys"])


class AccessKeyCreateReq(BaseModel):
    name: str = "默认"


def _brief(k: UserApiKey) -> dict:
    return {
        "id": k.id, "name": k.name, "prefix": k.key_prefix,
        "status": k.status, "createdAt": k.created_at, "lastUsed": k.last_used,
    }


@router.get("")
async def list_keys(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """列出当前用户的 access keys（不含哈希/明文）。"""
    rows = (await session.execute(
        select(UserApiKey).where(UserApiKey.user_id == user.id).order_by(UserApiKey.created_at.desc())
    )).scalars().all()
    return ok({"items": [_brief(k) for k in rows]})


@router.post("")
async def create_key(
    body: AccessKeyCreateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """生成 access key：明文仅此一次返回（服务端只存 bcrypt 哈希）。"""
    raw = f"sk_{secrets.token_hex(16)}"
    key = UserApiKey(
        id=f"ak{uuid4().hex[:8]}", user_id=user.id,
        name=(body.name or "默认")[:64],
        key_hash=bcrypt.hashpw(raw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8"),
        key_ciphertext=encrypt_secret(raw),
        key_prefix=raw[:12],
        status="active",
        created_at=datetime.now().strftime("%m-%d %H:%M"),
    )
    session.add(key)
    await AuditService(session).record(
        actor=user.name, action="access_key_create", target=key.name, result="success",
    )
    await session.commit()
    return ok({"key": {"id": key.id, "name": key.name, "key": raw}}, "access key 已创建（明文仅此一次展示）")


@router.get("/{key_id}/value")
async def reveal_key(
    key_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Reveal a key only to its owner for an explicit configuration export."""
    key = await session.get(UserApiKey, key_id)
    if key is None or key.user_id != user.id or key.status != "active":
        raise BizError(BizCode.NOT_FOUND, "有效 access key 不存在")
    if not key.key_ciphertext:
        raise BizError(BizCode.NOT_FOUND, "该 access key 尚未启用可重复查看，请重新创建")
    await AuditService(session).record(actor=user.name, action="access_key_reveal", target=key.name, result="success")
    await session.commit()
    return ok({"id": key.id, "name": key.name, "key": decrypt_secret(key.key_ciphertext)}, "access key 已获取")


@router.delete("/{key_id}")
async def revoke_key(
    key_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """吊销 access key（仅本人；吊销后立即失效）。"""
    key = await session.get(UserApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "access key 不存在")
    key.status = "revoked"
    await AuditService(session).record(
        actor=user.name, action="access_key_revoke", target=key.name, result="success",
    )
    await session.commit()
    return ok({"key": _brief(key)}, "access key 已吊销，立即失效")
