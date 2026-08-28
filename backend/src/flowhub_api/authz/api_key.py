"""用户 access key 认证（外部 Agent 通过 MCP/Skill 接入平台）。

`Bearer <access_key>`（sk_xxx）→ 按 key_prefix 检索候选行 → bcrypt 校验 →
解析 User（复用现有 RBAC / can_read_task）。成功时更新 last_used。
"""
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import User, UserApiKey
from flowhub_api.services.time import now_iso

KEY_PREFIX = "sk_"


async def get_current_user_by_key(session: AsyncSession, authorization: str) -> User:
    """解析 `Authorization: Bearer <access_key>` 为 User；失败抛 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise BizError(BizCode.UNAUTH, "缺少访问凭证（Bearer access key）", http_status=401)
    token = authorization[len("Bearer "):].strip()
    if not token.startswith(KEY_PREFIX) or len(token) < 12:
        raise BizError(BizCode.UNAUTH, "access key 格式无效", http_status=401)

    # 1) 前缀检索候选行（bcrypt 无法按值索引）
    prefix = token[:12]
    rows = (await session.execute(
        select(UserApiKey).where(
            UserApiKey.status == "active", UserApiKey.key_prefix == prefix,
        )
    )).scalars().all()
    if not rows:
        raise BizError(BizCode.UNAUTH, "access key 无效或已吊销", http_status=401)

    # 2) bcrypt 逐条校验
    matched = None
    for row in rows:
        if bcrypt.checkpw(token.encode("utf-8")[:72], row.key_hash.encode("utf-8")):
            matched = row
            break
    if matched is None:
        raise BizError(BizCode.UNAUTH, "access key 无效或已吊销", http_status=401)

    # 3) 解析 User（须存在且 active）
    user = await session.get(User, matched.user_id)
    if user is None or user.status != "active":
        raise BizError(BizCode.UNAUTH, "access key 所属用户不存在或已停用", http_status=401)

    # 4) 更新 last_used（成功认证后记录，便于审计排查）
    matched.last_used = now_iso()
    await session.commit()
    return user
