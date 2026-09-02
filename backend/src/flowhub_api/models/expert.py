"""Versioned Expert runtime models backed by LangGraph checkpoints."""
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from flowhub_api.db.session import Base


class LlmProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    base_url: Mapped[str] = mapped_column(String(255))
    api_key: Mapped[str] = mapped_column(String(512), default="")
    credential_configured: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="healthy")
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=1_000_000)  # 模型最大上下文窗口（token），压缩预算 = 80%
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class LlmProviderModel(Base):
    __tablename__ = "llm_provider_models"
    __table_args__ = (UniqueConstraint("provider_id", "model", name="uq_llm_provider_model"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("llm_providers.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ExpertSkill(Base):
    __tablename__ = "expert_skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32), default="v1.0.0")
    package_type: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    object_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(Enum("draft", "testing", "published", "archived", name="expert_skill_status"), default="draft")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    direction: Mapped[str] = mapped_column(String(16), default="outbound")
    transport: Mapped[str] = mapped_column(String(32), default="streamable-http")
    endpoint: Mapped[str] = mapped_column(String(512), default="")
    auth_type: Mapped[str] = mapped_column(String(24), default="none")
    credentials: Mapped[str] = mapped_column(String(2048), default="")
    status: Mapped[str] = mapped_column(String(16), default="unhealthy")
    health: Mapped[str] = mapped_column(String(64), default="未检测")
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class McpTool(Base):
    __tablename__ = "mcp_tools"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(24), default="read")
    approval: Mapped[str] = mapped_column(String(24), default="none")
    status: Mapped[str] = mapped_column(String(24), default="discovered")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class Expert(Base):
    __tablename__ = "experts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(96), unique=True)
    kind: Mapped[str] = mapped_column(Enum("builtin", "custom", name="expert_kind"), default="custom")
    status: Mapped[str] = mapped_column(Enum("draft", "testing", "published", "suspended", "archived", name="expert_status"), default="draft")
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column("owner_user_id", ForeignKey("users.id"), index=True)
    current_version_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertVersion(Base):
    __tablename__ = "expert_versions"
    __table_args__ = (UniqueConstraint("expert_id", "version", name="uq_expert_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    expert_id: Mapped[str] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(Enum("draft", "testing", "published", "deprecated", name="expert_version_status"), default="draft")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    output_contract_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    knowledge_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    memory_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(128), default="")
    published_by: Mapped[str] = mapped_column(String(32), default="")
    provider_model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_provider_models.id"), nullable=True)
    provider_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_base_ids: Mapped[list] = mapped_column(JSON, default=list)
    tool_policies: Mapped[dict] = mapped_column(JSON, default=dict)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    tested_at: Mapped[str] = mapped_column(String(40), default="")
    published_at: Mapped[str] = mapped_column(String(40), default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertDeployment(Base):
    __tablename__ = "expert_deployments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    expert_id: Mapped[str] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), index=True)
    expert_version_id: Mapped[str] = mapped_column(ForeignKey("expert_versions.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    environment: Mapped[str] = mapped_column(String(16))
    alias: Mapped[str] = mapped_column(String(96), default="")
    status: Mapped[str] = mapped_column(Enum("pending", "active", "suspended", "revoked", "archived", name="deployment_status"), default="pending")
    # Kept for databases created by the retired Agent runtime. Deployments do
    # not currently use overrides, but the columns remain mandatory there.
    workflow_binding_json: Mapped[dict] = mapped_column(JSON, default=dict)
    provider_override_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertRun(Base):
    __tablename__ = "expert_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    expert_id: Mapped[str] = mapped_column(ForeignKey("experts.id"), index=True)
    expert_version_id: Mapped[str] = mapped_column(ForeignKey("expert_versions.id"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("expert_deployments.id"), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # 流程节点触发（Expert 自动/协助填充）：关联的任务，用于把运行结果回填节点表单
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    input: Mapped[str] = mapped_column(Text, default="")
    # 用户重新执行时补充的执行上下文（留空=按任务书原样生成）；展示 + prompt 拼接
    context: Mapped[str] = mapped_column(Text, default="")
    output: Mapped[str] = mapped_column(Text, default="")
    # 解析快照：Run 成功后按节点 schema 解析的 {values, warnings}，供前端预览与采纳幂等消费
    parsed: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(64), unique=True)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertRunEvent(Base):
    __tablename__ = "expert_run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_expert_run_event_sequence"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("expert_runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(160))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertApproval(Base):
    __tablename__ = "expert_approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("expert_runs.id", ondelete="CASCADE"), unique=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    risk: Mapped[str] = mapped_column(String(24))
    scope: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    expires_at: Mapped[str] = mapped_column(String(40))
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class ExpertChatSession(Base):
    __tablename__ = "expert_chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expert_id: Mapped[str | None] = mapped_column(ForeignKey("experts.id"), nullable=True, index=True)
    # 版本绑定（编辑器「在 AiChat 中测试」）：不加 FK，避免删除 Expert 级联版本时连带删掉会话
    expert_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("expert_deployments.id"), nullable=True)
    provider_model_id: Mapped[str | None] = mapped_column(ForeignKey("llm_provider_models.id"), nullable=True)
    # 会话绑定的项目（存项目名称，与 WorkItem.project 一致）：用于注入项目绑定仓库的地图层上下文
    project_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    title: Mapped[str] = mapped_column(String(160), default="新会话")
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")
    compaction_sequence: Mapped[int] = mapped_column(Integer, default=0)   # 已压缩的最大消息 sequence（摘要截止标记）
    compaction_summary: Mapped[str] = mapped_column(Text, default="")      # 固化摘要文本（标记之前的早期对话）


class ExpertChatMessage(Base):
    __tablename__ = "expert_chat_messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence", name="uq_expert_chat_message_sequence"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("expert_chat_sessions.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("expert_runs.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="completed")
    tool_trace: Mapped[list] = mapped_column(JSON, default=list)
    files: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default="")
