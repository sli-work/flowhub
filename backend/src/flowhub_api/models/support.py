"""领域模型：文档 / 通知 / 审计（docs/01 §9-12）。"""
from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

DOC_LEVEL = ("L1", "L2", "L3")
DOC_SCAN = ("已扫描", "含毒", "扫描中")
NOTIFY_KIND = ("arrive", "agent", "timeout", "return", "complete", "transfer", "info", "fail")
ACTOR_TYPE = ("user", "agent", "system")
AUDIT_RESULT = ("success", "failed", "denied")
class DocItem(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    project: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[str] = mapped_column(String(32), default="v1")
    level: Mapped[str] = mapped_column(Enum(*DOC_LEVEL, name="doc_level"), default="L2")
    scan: Mapped[str] = mapped_column(Enum(*DOC_SCAN, name="doc_scan"), default="已扫描")
    uploader: Mapped[str] = mapped_column(String(64))
    size: Mapped[str] = mapped_column(String(32), default="")
    time: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(32), default="")
    wi: Mapped[str | None] = mapped_column(String(40), nullable=True)
    object_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # MinIO key
    deleted: Mapped[bool] = mapped_column(default=False)


class RuntimeConfig(Base):
    """管理员页面维护的运行配置；敏感值以 Fernet 密文保存。"""
    __tablename__ = "runtime_configs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    secret: Mapped[bool] = mapped_column(default=False)


class NotificationItem(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    body: Mapped[str] = mapped_column(String(500), default="")
    time: Mapped[str] = mapped_column(String(32), default="")
    channels: Mapped[list] = mapped_column(JSON, default=list)  # [{name,ok}]
    unread: Mapped[bool] = mapped_column(default=True)
    kind: Mapped[str] = mapped_column(Enum(*NOTIFY_KIND, name="notify_kind"), default="info")
    failed: Mapped[bool] = mapped_column(default=False)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    target_user: Mapped[str] = mapped_column(String(64), default="", index=True)
    # 业务跳转目标：通知点击 → 对应工作项 / 任务（空则跳通知中心列表）
    wi_id: Mapped[str] = mapped_column(String(40), default="")
    task_id: Mapped[str] = mapped_column(String(64), default="")


class AuditRow(Base):
    __tablename__ = "audits"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    time: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    actor_type: Mapped[str] = mapped_column(Enum(*ACTOR_TYPE, name="actor_type"), default="user")
    authorized: Mapped[str] = mapped_column(String(64), default="—")
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    result: Mapped[str] = mapped_column(Enum(*AUDIT_RESULT, name="audit_result"), default="success")
    req_id: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
