"""领域模型：工作项 / 流程实例 / 任务（docs/01 §3-4、docs/04）。"""
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

WORK_ITEM_TYPE = ("requirement", "issue", "change")
WORK_ITEM_STATUS = (
    "draft", "submitted", "in_progress", "waiting_for_information",
    "waiting_for_verification", "resolved", "accepted", "rejected",
    "closed", "cancelled", "archived",
)
PRIORITY = ("P0", "P1", "P2", "P3")
TASK_STATUS = (
    "assigned", "accepted", "in_progress", "waiting_for_information",
    "pending_confirmation", "submitted", "returned", "transferred",
    "completed", "cancelled",
)
ISSUE_STATUS = ("handling", "waiting_verification", "closed", "deferred")
INSTANCE_STATE = ("running", "paused", "cancelled", "closed", "archived")


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # REQ-2026-0241
    type: Mapped[str] = mapped_column(Enum(*WORK_ITEM_TYPE, name="work_item_type"))
    title: Mapped[str] = mapped_column(String(255), index=True)  # 普通索引（标题搜索）；不做全局唯一
    project: Mapped[str] = mapped_column(String(128))
    priority: Mapped[str] = mapped_column(Enum(*PRIORITY, name="priority"), default="P2")
    status: Mapped[str] = mapped_column(Enum(*WORK_ITEM_STATUS, name="work_item_status"), default="draft")
    assignee: Mapped[str] = mapped_column(String(64), default="—")
    creator: Mapped[str] = mapped_column(String(64))
    due: Mapped[str] = mapped_column(String(32), default="")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    progress: Mapped[str] = mapped_column(String(64), default="")
    # 起始表单值（startSchema 提交内容）
    start_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    instance: Mapped["WorkflowInstance"] = relationship(
        back_populates="work_item", cascade="all, delete-orphan", uselist=False
    )
    # delete-orphan：删除工作项时任务随 ORM 级联 DELETE（DB 层 wi_id 亦有 ondelete=CASCADE 兜底）
    tasks: Mapped[list["TaskItem"]] = relationship(
        back_populates="work_item", lazy="selectin", cascade="all, delete-orphan"
    )


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), unique=True)
    template_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(16))
    current_node: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[str] = mapped_column(Enum(*INSTANCE_STATE, name="instance_state"), default="running")

    work_item: Mapped["WorkItem"] = relationship(back_populates="instance")


class TaskItem(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # T-2026-0912
    wi_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    project: Mapped[str] = mapped_column(String(128))
    node: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[str] = mapped_column(String(32), default="")
    type: Mapped[str] = mapped_column(Enum(*WORK_ITEM_TYPE, name="work_item_type"))
    priority: Mapped[str] = mapped_column(Enum(*PRIORITY, name="priority"), default="P2")
    status: Mapped[str] = mapped_column(Enum(*TASK_STATUS, name="task_status"), default="assigned")
    assignee: Mapped[str] = mapped_column(String(64))
    due: Mapped[str] = mapped_column(String(32), default="")
    sla_hours: Mapped[int] = mapped_column(Integer, default=24)
    overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    expert_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 提交时的表单值（节点 schema）
    form_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 子任务拆分：parent_task_id 指向拆分时的父任务（普通列不加 FK，避免删除顺序约束）
    parent_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # 子线根任务：拆分得到的每条子线固定一个 root，后续流转/并行汇合按该 root 隔离
    lineage_root_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # 拆分子任务携带的需求说明；处理页「任务书」与 AI 简报优先展示
    brief: Mapped[str] = mapped_column(Text, default="")
    # 提交时验收清单勾选快照 {key: {text, checked}}，模板后续修改不影响历史审计
    acceptance_checks: Mapped[dict] = mapped_column(JSON, default=dict)
    # 创建时间（ISO 字符串）：列表"最新创建"排序与展示用；存量数据由 migrate 从 id 回填
    created_at: Mapped[str] = mapped_column(String(40), default="")

    work_item: Mapped["WorkItem"] = relationship(back_populates="tasks")


class TaskAppend(Base):
    """前序节点追加信息：已完成节点由原处理人按节点 schema 结构化补充，独立留痕、不覆盖原值。"""

    __tablename__ = "task_appends"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(32), default="")
    wi_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    appender: Mapped[str] = mapped_column(String(64))
    time: Mapped[str] = mapped_column(String(32), default="")
    values: Mapped[dict] = mapped_column(JSON, default=dict)


class WorkflowIssue(Base):
    """A local rework loop created by any workflow node without moving the main cursor."""

    __tablename__ = "workflow_issues"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    wi_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    source_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    source_node_id: Mapped[str] = mapped_column(String(32), default="")
    source_node: Mapped[str] = mapped_column(String(64), default="")
    target_task_id: Mapped[str] = mapped_column(String(40), default="")
    target_node_id: Mapped[str] = mapped_column(String(32), default="")
    target_node: Mapped[str] = mapped_column(String(64), default="")
    handler_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    verification_task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    # Tiptap JSON 是问题正文的权威格式；description 保留给存量记录及旧客户端。
    description_doc: Mapped[dict] = mapped_column(JSON, default=dict)
    description_text: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[str] = mapped_column(Enum(*PRIORITY, name="issue_priority"), default="P2")
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Enum(*ISSUE_STATUS, name="workflow_issue_status"), default="handling")
    reporter: Mapped[str] = mapped_column(String(64))
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    round: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")
