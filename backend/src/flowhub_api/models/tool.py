"""Immutable tool contracts and the durable invocation/automation ledgers."""
from datetime import datetime
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from flowhub_api.db.session import Base


class ToolDefinition(Base):
    __tablename__ = 'tool_definitions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default='')
    source: Mapped[str] = mapped_column(String(16))
    server_id: Mapped[str | None] = mapped_column(ForeignKey('mcp_servers.id'), nullable=True)
    external_name: Mapped[str] = mapped_column(String(160), default='')
    status: Mapped[str] = mapped_column(String(16), default='active')
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(32), default='')
    created_at: Mapped[str] = mapped_column(String(40), default='')
    updated_at: Mapped[str] = mapped_column(String(40), default='')


class ToolVersion(Base):
    __tablename__ = 'tool_versions'
    __table_args__ = (UniqueConstraint('tool_id', 'revision', name='uq_tool_version_revision'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tool_id: Mapped[str] = mapped_column(ForeignKey('tool_definitions.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default='draft')
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    output_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    risk: Mapped[str] = mapped_column(String(24), default='read')
    approval: Mapped[str] = mapped_column(String(24), default='none')
    checksum: Mapped[str] = mapped_column(String(64), default='')
    tested_at: Mapped[str] = mapped_column(String(40), default='')
    created_at: Mapped[str] = mapped_column(String(40), default='')


class ToolInvocation(Base):
    __tablename__ = 'tool_invocations'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    tool_version_id: Mapped[str] = mapped_column(ForeignKey('tool_versions.id'), index=True)
    expert_version_id: Mapped[str | None] = mapped_column(ForeignKey('expert_versions.id'), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey('expert_runs.id'), nullable=True, index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    requested_by: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    source: Mapped[str] = mapped_column(String(16), default='expert')
    input_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    error: Mapped[str] = mapped_column(Text, default='')
    approval_status: Mapped[str] = mapped_column(String(24), default='not_required')
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default='')
    finished_at: Mapped[str] = mapped_column(String(40), default='')


class ToolAutomationJob(Base):
    __tablename__ = 'tool_automation_jobs'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    task_id: Mapped[str] = mapped_column(String(40), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    expert_version_id: Mapped[str] = mapped_column(String(32))
    rule_json: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    error: Mapped[str] = mapped_column(Text, default='')
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str] = mapped_column(String(32), default='')
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String(40), default='')
    finished_at: Mapped[str] = mapped_column(String(40), default='')
