"""认证：JWT 签发/校验、本地登录、注册审批（docs/02 §一）。"""
from datetime import UTC, datetime, timedelta

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import Role, User
from flowhub_api.seed.init import gen_id


class AuthService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    # ---------- 密码（bcrypt 原生，passlib 与 bcrypt 4.x 不兼容故弃用） ----------
    @staticmethod
    def hash_password(raw: str) -> str:
        return bcrypt.hashpw(raw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(raw: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(raw.encode("utf-8")[:72], hashed.encode("utf-8"))
        except ValueError:
            return False

    # ---------- JWT ----------
    def create_token(self, user_id: str) -> str:
        expire = datetime.now(UTC) + timedelta(minutes=self.settings.access_token_expire_minutes)
        payload = {"sub": user_id, "exp": expire}
        return jwt.encode(payload, self.settings.secret_key, algorithm="HS256")

    def decode_token(self, token: str) -> str | None:
        try:
            payload = jwt.decode(token, self.settings.secret_key, algorithms=["HS256"])
            return payload.get("sub")
        except JWTError:
            return None

    # ---------- 本地登录 ----------
    async def login(self, account: str, password: str) -> tuple[User, str]:
        # 登录名兼容两种输入：账号名（account）或邮箱（email，登录页「用户名/邮箱」）。
        # 仅当输入含 @ 时才匹配邮箱（email 默认空串，避免空值误匹配）
        cond = User.account == account
        if "@" in account:
            cond = or_(cond, User.email == account)
        user = (await self.session.execute(select(User).where(cond))).scalar_one_or_none()
        if user is None or user.deleted or not user.password_hash or not self.verify_password(password, user.password_hash):
            raise BizError(BizCode.UNAUTH, "账号或密码错误", http_status=401)
        if user.status == "invited":
            # 注册后须管理员审批（docs/02 §1.3-1.5）：待审批状态不可登录（BUG-C 修复）
            raise BizError(BizCode.FORBIDDEN, "账号待管理员审批，暂不可登录", http_status=403)
        if user.status == "locked":
            raise BizError(BizCode.LOCKED, "账号已锁定，请联系管理员解锁", http_status=423)
        if user.status == "disabled":
            raise BizError(BizCode.FORBIDDEN, "账号已停用", http_status=403)
        token = self.create_token(user.id)
        return user, token

    # ---------- 注册 / 审批 ----------
    async def register(self, account: str, name: str, email: str, dept: str, role_id: str, password: str) -> User:
        # 邮箱仅在非空时参与查重（email 默认空串，不能用空值去匹配存量用户）
        cond = User.account == account
        if email.strip():
            cond = or_(cond, User.email == email.strip())
        exists = (await self.session.execute(
            select(User).where(cond, User.deleted == False)  # noqa: E712
        )).scalar_one_or_none()
        if exists:
            raise BizError(BizCode.DUPLICATE_OPERATION, "账号或邮箱已存在")
        role = await self.session.get(Role, role_id)
        if role is None:
            raise BizError(BizCode.VALIDATION, "角色不存在")
        user = User(
            id=gen_id("u"), name=name, account=account, email=email,
            password_hash=self.hash_password(password), dept=dept,
            roles=[role], skills=[], status="invited", must_change_password=True,
        )
        self.session.add(user)
        await self.session.flush()
        return user
