"""领域模型：代码仓库连接 / 仓库 / 项目仓库绑定。

- RepoConnection：托管平台凭证（GitHub / GitLab / 自建 GitLab），token 加密落库，跨项目共享
- Repo：远端仓库快照（按 connection_id + provider_repo_id 唯一），独立于项目存在，可被多个项目绑定
- ProjectRepoBinding：项目 ↔ 仓库 多对多绑定（含角色定位与默认分支）
"""
from sqlalchemy import Enum, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowhub_api.db.session import Base

REPO_PROVIDER = ("github", "gitlab")  # 自建 GitLab = provider "gitlab" + 内网 base_url
CONNECTION_STATUS = ("ok", "invalid")
REPO_ROLE = ("main", "docs", "service", "lib")


class RepoConnection(Base):
    __tablename__ = "repo_connections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(Enum(*REPO_PROVIDER, name="repo_provider"))
    base_url: Mapped[str] = mapped_column(String(255), default="")  # 自建 GitLab 必填；GitHub 固定 api.github.com
    token_ciphertext: Mapped[str] = mapped_column(String(512), default="")
    token_hint: Mapped[str] = mapped_column(String(16), default="")  # 末 4 位展示用，不回传原文
    account: Mapped[str] = mapped_column(String(128), default="")  # verify 成功后的平台账号名
    status: Mapped[str] = mapped_column(Enum(*CONNECTION_STATUS, name="repo_connection_status"), default="ok")
    checked_at: Mapped[str] = mapped_column(String(32), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")

    repos: Mapped[list["Repo"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", lazy="selectin"
    )


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (UniqueConstraint("connection_id", "provider_repo_id", name="uq_repo_remote"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("repo_connections.id", ondelete="CASCADE"), index=True
    )
    provider_repo_id: Mapped[str] = mapped_column(String(64))  # 平台侧数字 id（str 存储）
    full_name: Mapped[str] = mapped_column(String(255))  # GitHub owner/repo；GitLab namespace/name
    web_url: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    default_branch: Mapped[str] = mapped_column(String(128), default="main")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    synced_at: Mapped[str] = mapped_column(String(32), default="")

    connection: Mapped["RepoConnection"] = relationship(back_populates="repos")
    bindings: Mapped[list["ProjectRepoBinding"]] = relationship(
        back_populates="repo", cascade="all, delete-orphan", lazy="selectin"
    )


class ProjectRepoBinding(Base):
    """项目 ↔ 仓库绑定：一个项目多个仓库，一个仓库可服务多个项目。"""

    __tablename__ = "project_repo_bindings"
    __table_args__ = (UniqueConstraint("project_id", "repo_id", name="uq_project_repo"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    repo_id: Mapped[str] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(Enum(*REPO_ROLE, name="repo_binding_role"), default="main")
    default_branch: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # 预留：webhook 订阅 / 事件配置等

    repo: Mapped["Repo"] = relationship(back_populates="bindings")
