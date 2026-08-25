"""RBAC 授权判定（docs/03 §4）：角色权限并集 + 防自锁 + 权限依赖。

流程：请求 → 取 User.roles → 汇总权限点并集 → 判定 → 无权限写审计 denied。
"""
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.db.session import get_db
from flowhub_api.models import User
from flowhub_api.seed.data import ANTI_LOCK_PERMS
from flowhub_api.services.auth import AuthService


class Authorizer:
    def __init__(self, user: User):
        self.user = user

    @property
    def perm_set(self) -> set[str]:
        """多角色权限点并集。"""
        merged: set[str] = set()
        for role in self.user.roles:
            merged |= {k for k, v in (role.perms or {}).items() if v}
        return merged

    def has(self, perm: str) -> bool:
        return perm in self.perm_set

    def require(self, perm: str) -> None:
        """无权限 → 40302（调用方负责写审计 denied）。"""
        if not self.has(perm):
            raise BizError(BizCode.PERM_DENIED, f"无权限：{perm}", http_status=status.HTTP_403_FORBIDDEN)

    def require_any(self, perms: list[str]) -> None:
        if not any(self.has(p) for p in perms):
            raise BizError(BizCode.PERM_DENIED, f"无权限：{' / '.join(perms)}", http_status=status.HTTP_403_FORBIDDEN)


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """FastAPI 依赖：从 Authorization: Bearer <token> 解析当前用户。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未认证")
    token = auth.removeprefix("Bearer ").strip()
    user_id = AuthService(session).decode_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="票据失效或已过期")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user


def build_authorizer(user: User) -> Authorizer:
    return Authorizer(user)


def ensure_no_self_lock(role_id: str, perm: str) -> None:
    """防自锁：system_admin 不可被降权 organization:user_manage（docs/03 §5）。"""
    if role_id == "system_admin" and perm in ANTI_LOCK_PERMS:
        raise BizError(BizCode.PERM_DENIED, "系统管理员不可被降权（避免权限自锁）", http_status=status.HTTP_403_FORBIDDEN)
