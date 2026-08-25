"""领域模型：工作项 / 流程实例 / 任务（docs/01 §3-4、docs/04）。"""
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String
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
    tasks: Mapped[list["TaskItem"]] = relationship(back_populates="work_item", lazy="selectin")


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
    agent_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 提交时的表单值（节点 schema）
    form_values: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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
