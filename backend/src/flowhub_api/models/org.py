"""领域模型：组织与角色（对齐 docs/01-领域模型.md）。"""
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, String, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

USER_STATUS = ("active", "disabled", "locked", "invited")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    account: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dept: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(Enum(*USER_STATUS, name="user_status"), default="active")
    avatar_grad: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ding_talk: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wecom: Mapped[str | None] = mapped_column(String(64), nullable=True)
    load: Mapped[int] = mapped_column(default=0)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    roles: Mapped[list["Role"]] = relationship(
        secondary="user_roles", back_populates="members", lazy="selectin"
    )
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    label: Mapped[str] = mapped_column(String(64))
    desc: Mapped[str] = mapped_column(String(255), default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    perms: Mapped[dict] = mapped_column(JSON, default=dict)

    members: Mapped[list["User"]] = relationship(
        secondary="user_roles", back_populates="roles", lazy="selectin"
    )


class UserRole(Base):
    """用户-角色多对多 join 表（用户维度与角色维度双向分配的唯一事实源）。"""

    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class UserApiKey(Base):
    """用户级 access key（外部 MCP/Skill 接入平台的凭证）。

    明文 `sk_xxx` 仅在创建时返回一次；落库 bcrypt 哈希 + 前缀索引（bcrypt 无法按值索引，
    key_prefix 仅用于检索候选行，泄露前缀不足以伪造）。可命名、可吊销、记录 last_used。
    """

    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)          # ak_<8hex>
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    key_hash: Mapped[str] = mapped_column(String(128))                     # bcrypt(sk_xxx)
    key_ciphertext: Mapped[str] = mapped_column(String(512), default="")  # 受控导出时解密
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)        # token 前 12 字符
    status: Mapped[str] = mapped_column(String(16), default="active")      # active / revoked
    created_at: Mapped[str] = mapped_column(String(32), default="")
    last_used: Mapped[str] = mapped_column(String(32), default="")
