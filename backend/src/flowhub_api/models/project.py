"""领域模型：项目 / 模板绑定 / 节点处理人绑定（docs/01 §5）。"""
from sqlalchemy import JSON, Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

PROJECT_STATUS = ("draft", "active", "paused", "completed", "cancelled", "archived")
BINDING_STATUS = ("active", "disabled")
TEMPLATE_TYPE = ("requirement", "issue", "change")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    status: Mapped[str] = mapped_column(Enum(*PROJECT_STATUS, name="project_status"), default="draft")
    desc: Mapped[str] = mapped_column(String(500), default="")
    members: Mapped[int] = mapped_column(Integer, default=0)
    work_items: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    manager: Mapped[str] = mapped_column(String(64), default="")
    owner: Mapped[str] = mapped_column(String(128), default="")
    updated: Mapped[str] = mapped_column(String(32), default="")
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)

    template_bindings: Mapped[list["ProjectTemplateBinding"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectTemplateBinding(Base):
    __tablename__ = "project_template_bindings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(Enum(*TEMPLATE_TYPE, name="template_type"))
    version: Mapped[str] = mapped_column(String(16), default="v1")
    status: Mapped[str] = mapped_column(Enum(*BINDING_STATUS, name="binding_status"), default="active")

    project: Mapped["Project"] = relationship(back_populates="template_bindings")
    assignments: Mapped[list["NodeAssignment"]] = relationship(
        back_populates="binding", cascade="all, delete-orphan", lazy="selectin"
    )


class NodeAssignment(Base):
    """节点处理人绑定（PRD §4.5）：节点 → 多个用户 + 多个角色/技能。"""

    __tablename__ = "node_assignments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        ForeignKey("project_template_bindings.id", ondelete="CASCADE"), index=True
    )
    node_id: Mapped[str] = mapped_column(String(32))
    node_label: Mapped[str] = mapped_column(String(64))
    users: Mapped[list] = mapped_column(JSON, default=list)  # user ids
    roles: Mapped[list] = mapped_column(JSON, default=list)  # role / skill tags

    binding: Mapped["ProjectTemplateBinding"] = relationship(back_populates="assignments")
