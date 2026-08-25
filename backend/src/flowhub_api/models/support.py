"""领域模型：Agent / 文档 / 通知 / 审计（docs/01 §9-12）。"""
from sqlalchemy import JSON, Boolean, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

AGENT_STATUS = ("pending", "active", "suspended", "revoked")
CAP_MODE = ("direct", "confirm", "forbid")
DOC_LEVEL = ("L1", "L2", "L3")
DOC_SCAN = ("已扫描", "含毒", "扫描中")
NOTIFY_KIND = ("arrive", "agent", "timeout", "return", "complete", "transfer", "info", "fail")
ACTOR_TYPE = ("user", "agent", "system")
AUDIT_RESULT = ("success", "failed", "denied")
INVOCATION_STATUS = ("success", "failed", "draft", "cancelled")
CONFIRM_STATUS = ("pending", "approved", "rejected", "expired")
SUGGESTION_STATUS = ("suggestion", "approved", "rejected", "applied", "expired")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(64), unique=True)
    desc: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(Enum(*AGENT_STATUS, name="agent_status"), default="pending")
    scope: Mapped[str] = mapped_column(String(128), default="")
    bindings: Mapped[str] = mapped_column(String(255), default="")
    owner: Mapped[str] = mapped_column(String(64), default="")
    calls: Mapped[int] = mapped_column(Integer, default=0)
    success_rate: Mapped[float] = mapped_column(default=0.0)
    avg_ms: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[str] = mapped_column(String(32), default="")
    # Agent 执行配置（干净库 ALTER 新增；既有行走默认值）
    engine: Mapped[str] = mapped_column(String(16), default="opencode")          # opencode / api / custom
    provider: Mapped[str] = mapped_column(String(32), default="")                # OpenAI / DeepSeek / 通义 / Kimi …
    model: Mapped[str] = mapped_column(String(64), default="")                   # api 引擎存纯模型名；opencode 存 provider/model
    agent_type: Mapped[str] = mapped_column(String(32), default="")              # 已弃用（兼容旧数据）
    secret_hash: Mapped[str] = mapped_column(String(128), default="")             # bcrypt(sk_...)，明文仅注册返回一次
    # API 直连引擎（engine=api）：key 加密落库，不参与任何展示
    api_key: Mapped[str] = mapped_column(String(512), default="")                 # Fernet 密文
    base_url: Mapped[str] = mapped_column(String(255), default="")                # OpenAI 兼容 endpoint（含 /v1 等版本路径）
    system_prompt: Mapped[str] = mapped_column(Text, default="")                  # 默认系统提示词（权威；desc 为其截断）
    tool_id: Mapped[str] = mapped_column(String(32), default="")                  # 客户端工具（agent_tools.id）；空=旧数据走自身字段

    capabilities: Mapped[list["AgentCapability"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )
    invocations: Mapped[list["AgentInvocation"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", lazy="selectin"
    )


class AgentCapability(Base):
    __tablename__ = "agent_capabilities"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(Enum(*CAP_MODE, name="cap_mode"), default="confirm")

    agent: Mapped["Agent"] = relationship(back_populates="capabilities")


class AgentInvocation(Base):
    """Agent 调用记录：所有 opencode 执行都必须落一行（direct 成功 / confirm 草稿 / 失败 / 取消）。"""

    __tablename__ = "agent_invocations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    wi_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    node_id: Mapped[str] = mapped_column(String(32), default="")
    capability: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="direct")
    action: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Enum(*INVOCATION_STATUS, name="invocation_status"), default="draft")
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    authorized_user: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    finished_at: Mapped[str] = mapped_column(String(32), default="")
    audit_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="invocations")


class AgentConfirmRequest(Base):
    """确认请求：confirm 能力产出待人工批准/拒绝，带过期时间。冗余存 prompt/result 供弹窗一次拉取。"""

    __tablename__ = "agent_confirm_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    wi_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    node_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(64), default="")
    capability: Mapped[str] = mapped_column(String(64), default="")
    op_scope: Mapped[str] = mapped_column(String(255), default="")
    authorized_user: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(Enum(*CONFIRM_STATUS, name="confirm_status"), default="pending")
    prompt: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expire_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    decided_at: Mapped[str] = mapped_column(String(32), default="")
    decision_note: Mapped[str] = mapped_column(String(255), default="")

    agent: Mapped["Agent"] = relationship()


class AgentSuggestion(Base):
    """Agent 建议：独立表持久化（有状态生命周期 + 关联 invocation 全链路追溯）。"""

    __tablename__ = "agent_suggestions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    wi_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    node_id: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(128), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Enum(*SUGGESTION_STATUS, name="suggestion_status"), default="suggestion")
    time: Mapped[str] = mapped_column(String(32), default="")
    applied_at: Mapped[str] = mapped_column(String(32), default="")
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    agent: Mapped["Agent"] = relationship()


class AgentModel(Base):
    """可配置模型（页面上配置其他模型：provider + model 唯一）。"""

    __tablename__ = "agent_models"
    __table_args__ = (UniqueConstraint("provider", "model", name="uq_agent_model"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(64), default="")
    desc: Mapped[str] = mapped_column(String(255), default="")
    base_url: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class AgentType(Base):
    """Agent 业务角色类型（数据分析/测试用例/代码审查…），带默认能力集与系统提示词模板。"""

    __tablename__ = "agent_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    label: Mapped[str] = mapped_column(String(64))
    desc: Mapped[str] = mapped_column(String(255), default="")
    default_caps: Mapped[dict] = mapped_column(JSON, default=dict)
    system_prompt: Mapped[str] = mapped_column(Text, default="")   # 类型默认系统提示词模板（创建 Agent 带出）
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class AgentTool(Base):
    """客户端工具配置（第一类）：opencode 等。承载执行引擎/模型/API Key（加密）。"""

    __tablename__ = "agent_tools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    engine: Mapped[str] = mapped_column(String(16), default="opencode")   # opencode / api
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    models: Mapped[list] = mapped_column(JSON, default=list)
    base_url: Mapped[str] = mapped_column(String(255), default="")
    api_key: Mapped[str] = mapped_column(String(512), default="")          # Fernet 密文，永不回显
    desc: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[str] = mapped_column(String(32), default="")


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
