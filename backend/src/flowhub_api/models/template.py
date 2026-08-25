"""领域模型：流程模板 / 版本 / 画布节点（docs/01 §6-8）。"""
from sqlalchemy import JSON, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base
from flowhub_api.models.project import TEMPLATE_TYPE

VERSION_STATUS = ("draft", "reviewing", "published", "deprecated", "archived")
NODE_TYPE = (
    "start", "task", "decision", "parallel_split", "parallel_join",
    "acceptance", "closure", "timer", "end",
)


class GlobalTemplate(Base):
    __tablename__ = "global_templates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # tpl-req / tpl-issue / tpl-change
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(Enum(*TEMPLATE_TYPE, name="template_type"))
    versions: Mapped[list] = mapped_column(JSON, default=list)
    start_schema: Mapped[list] = mapped_column(JSON, default=list)  # FormField[]
    nodes: Mapped[list] = mapped_column(JSON, default=list)  # [{id,label,type}]


class TemplateVersion(Base):
    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # tpl-req:v3
    template_id: Mapped[str] = mapped_column(ForeignKey("global_templates.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(Enum(*VERSION_STATUS, name="version_status"), default="draft")
    updated: Mapped[str] = mapped_column(String(32), default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    instances: Mapped[int] = mapped_column(Integer, default=0)
    nodes: Mapped[int] = mapped_column(Integer, default=0)

    canvas: Mapped["TemplateCanvas"] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )


class TemplateCanvas(Base):
    """模板版本画布定义：节点 / 主边 / 回退边（整体存 JSON，对齐前端 CanvasNode）。"""

    __tablename__ = "template_canvases"

    version_id: Mapped[str] = mapped_column(
        ForeignKey("template_versions.id", ondelete="CASCADE"), primary_key=True
    )
    nodes: Mapped[list] = mapped_column(JSON, default=list)  # CanvasNode[]
    edges: Mapped[list] = mapped_column(JSON, default=list)  # [from,to][]
    fallbacks: Mapped[list] = mapped_column(JSON, default=list)  # [from,to][]

    version: Mapped["TemplateVersion"] = relationship(back_populates="canvas")
