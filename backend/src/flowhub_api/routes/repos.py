"""代码仓库路由：托管平台连接管理 + 项目 ↔ 仓库绑定（多对多）。

- 连接（RepoConnection）：GitHub / GitLab / 自建 GitLab 凭证，token 加密落库，创建时即验证
- 绑定（ProjectRepoBinding）：添加时从远端拉取仓库快照建 Repo（按 connection + 远端 id 去重复用）
"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import Project, ProjectRepoBinding, Repo, RepoConnection, User
from flowhub_api.schemas.api import (
    RepoBindingCreateReq,
    RepoBindingUpdateReq,
    RepoConnectionCreateReq,
    RepoConnectionUpdateReq,
)
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService
from flowhub_api.services.crypto import decrypt_secret, encrypt_secret
from flowhub_api.services.repo_mirror import schedule_mirror_build
from flowhub_api.services.repo_providers import (
    ProviderError,
    get_provider,
    now_ts,
    token_hint_of,
)

router = APIRouter(prefix="/api/v1/repo-connections", tags=["repos"])
repos_router = APIRouter(prefix="/api/v1/repos", tags=["repos"])
project_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _conn_dict(c: RepoConnection, repo_count: int = 0) -> dict:
    return {
        "id": c.id, "name": c.name, "provider": c.provider,
        "baseUrl": c.base_url, "tokenHint": c.token_hint, "account": c.account,
        "status": c.status, "checkedAt": c.checked_at, "createdBy": c.created_by,
        "createdAt": c.created_at, "repoCount": repo_count,
    }


def _binding_dict(b: ProjectRepoBinding, repo: Repo, conn: RepoConnection | None = None) -> dict:
    return {
        "id": b.id, "projectId": b.project_id, "role": b.role,
        "defaultBranch": b.default_branch or repo.default_branch,
        "createdAt": b.created_at,
        "repo": {
            "id": repo.id, "fullName": repo.full_name, "webUrl": repo.web_url,
            "description": repo.description, "defaultBranch": repo.default_branch,
            "visibility": repo.visibility, "syncedAt": repo.synced_at,
            "provider": conn.provider if conn else "github",
            "connectionId": repo.connection_id,
            "connectionName": conn.name if conn else "",
            "connectionStatus": conn.status if conn else "ok",
        },
    }


async def _load_conn(session: AsyncSession, conn_id: str) -> RepoConnection:
    c = await session.get(RepoConnection, conn_id)
    if c is None:
        raise BizError(BizCode.NOT_FOUND, "代码仓库连接不存在")
    return c


async def _sync_remote_repos(session: AsyncSession, conn: RepoConnection) -> int:
    """将连接可见的远端仓库完整同步为本地快照，绑定关系按远端 id 保持不变。"""
    remote_repos = await get_provider(
        conn.provider, decrypt_secret(conn.token_ciphertext), conn.base_url,
    ).list_remote_repos(limit=500)
    existing = {
        repo.provider_repo_id: repo for repo in (await session.execute(
            select(Repo).where(Repo.connection_id == conn.id)
        )).scalars().all()
    }
    for remote in remote_repos:
        repo = existing.get(remote.provider_repo_id)
        if repo is None:
            repo = Repo(id=gen_id("rp"), connection_id=conn.id, provider_repo_id=remote.provider_repo_id)
            session.add(repo)
        repo.full_name = remote.full_name
        repo.web_url = remote.web_url
        repo.description = remote.description
        repo.default_branch = remote.default_branch
        repo.visibility = remote.visibility
        repo.synced_at = now_ts()
    return len(remote_repos)


async def _verify_and_fill(c: RepoConnection, token: str) -> None:
    """调用平台 API 验证 token，回填账号 / 状态 / hint；失败抛业务错误。"""
    try:
        account = await get_provider(c.provider, token, c.base_url).verify()
    except ProviderError as e:
        raise BizError(BizCode.VALIDATION, str(e))
    c.account = account
    c.token_ciphertext = encrypt_secret(token)
    c.token_hint = token_hint_of(token)
    c.status = "ok"
    c.checked_at = now_ts()


# ---------- 连接管理 ----------

@router.get("")
async def list_connections(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = (await session.execute(
        select(RepoConnection).order_by(RepoConnection.created_at.desc())
    )).scalars().all()
    counts = dict((await session.execute(
        select(Repo.connection_id, func.count(Repo.id)).group_by(Repo.connection_id)
    )).all())
    return ok({"items": [_conn_dict(c, counts.get(c.id, 0)) for c in rows], "total": len(rows)})


@router.post("")
async def create_connection(
    body: RepoConnectionCreateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("expert:resource_manage")
    if body.provider == "gitlab" and not body.base_url.strip():
        raise BizError(BizCode.VALIDATION, "自建 GitLab 需要填写 base_url（GitLab.com 可填 https://gitlab.com）")
    exists = (await session.execute(
        select(RepoConnection).where(RepoConnection.name == body.name.strip())
    )).scalar_one_or_none()
    if exists:
        raise BizError(BizCode.DUPLICATE_OPERATION, f"连接名称已存在：{body.name.strip()}")

    c = RepoConnection(
        id=gen_id("rc"), name=body.name.strip(), provider=body.provider,
        base_url=body.base_url.strip(), created_by=user.name, created_at=now_ts(),
    )
    await _verify_and_fill(c, body.token.strip())
    session.add(c)
    await AuditService(session).record(
        actor=user.name, action="repo_connection:create", target=c.name, result="success",
        after={"provider": c.provider, "baseUrl": c.base_url, "account": c.account},
    )
    await session.commit()
    return ok({"item": _conn_dict(c)}, f"连接已创建并验证通过（账号：{c.account}）")


@router.patch("/{connection_id}")
async def update_connection(
    connection_id: str,
    body: RepoConnectionUpdateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("expert:resource_manage")
    c = await _load_conn(session, connection_id)
    if body.name is not None and body.name.strip() != c.name:
        dup = (await session.execute(
            select(RepoConnection).where(RepoConnection.name == body.name.strip())
        )).scalar_one_or_none()
        if dup:
            raise BizError(BizCode.DUPLICATE_OPERATION, f"连接名称已存在：{body.name.strip()}")
        c.name = body.name.strip()
    token = body.token.strip() if body.token else None
    if body.base_url is not None and body.base_url.strip() != c.base_url:
        if c.provider == "gitlab" and not body.base_url.strip():
            raise BizError(BizCode.VALIDATION, "自建 GitLab 需要填写 base_url")
        c.base_url = body.base_url.strip()
    if token:
        await _verify_and_fill(c, token)  # 换 token / 换地址后重新验证
    elif c.status == "invalid":
        await _verify_and_fill(c, decrypt_secret(c.token_ciphertext))
    await AuditService(session).record(
        actor=user.name, action="repo_connection:update", target=c.name, result="success"
    )
    await session.commit()
    return ok({"item": _conn_dict(c)}, "连接已更新")


@router.post("/{connection_id}/verify")
async def verify_connection(
    connection_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """手动健康检查：重新验证 token，失效时标记 invalid（不抛错，返回状态）。"""
    c = await _load_conn(session, connection_id)
    c.checked_at = now_ts()
    try:
        c.account = await get_provider(c.provider, decrypt_secret(c.token_ciphertext), c.base_url).verify()
        c.status = "ok"
        message = f"连接有效（账号：{c.account}）"
    except (ProviderError, BizError):
        c.status = "invalid"
        message = "连接已失效：token 无效或平台不可达，请更新 token"
    await session.commit()
    return ok({"item": _conn_dict(c)}, message)


@router.delete("/{connection_id}")
async def delete_connection(
    connection_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("expert:resource_manage")
    c = (await session.execute(
        select(RepoConnection).options(selectinload(RepoConnection.repos)).where(RepoConnection.id == connection_id)
    )).scalar_one_or_none()
    if c is None:
        raise BizError(BizCode.NOT_FOUND, "代码仓库连接不存在")
    repo_count = len(c.repos or [])
    bind_count = (await session.execute(
        select(func.count(ProjectRepoBinding.id)).where(
            ProjectRepoBinding.repo_id.in_([r.id for r in (c.repos or [])] or [""])
        )
    )).scalar() or 0
    name = c.name
    await session.delete(c)  # 级联：repos → project_repo_bindings
    await AuditService(session).record(
        actor=user.name, action="repo_connection:delete",
        target=f"{name}（{repo_count} 仓库 / {bind_count} 绑定）", result="success",
    )
    await session.commit()
    return ok(message=f"已删除连接「{name}」及其 {repo_count} 个仓库记录与 {bind_count} 个项目绑定")


@router.get("/{connection_id}/remote-repos")
async def search_remote_repos(
    connection_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    q: str = "",
):
    """代理搜索远端仓库（添加绑定时浏览 token 可见仓库）。"""
    build_authorizer(user).require("expert:resource_manage")
    c = await _load_conn(session, connection_id)
    if c.status == "invalid":
        raise BizError(BizCode.VALIDATION, "连接已失效，请先更新 token")
    try:
        items = await get_provider(c.provider, decrypt_secret(c.token_ciphertext), c.base_url).list_remote_repos(q, limit=500)
    except ProviderError as e:
        raise BizError(BizCode.VALIDATION, str(e))
    bound_rows = (await session.execute(
        select(Repo, ProjectRepoBinding, Project.name)
        .join(ProjectRepoBinding, ProjectRepoBinding.repo_id == Repo.id)
        .join(Project, Project.id == ProjectRepoBinding.project_id)
        .where(Repo.connection_id == c.id)
    )).all()
    bindings_by_remote_id: dict[str, list[dict]] = {}
    for repo, binding, project_name in bound_rows:
        bindings_by_remote_id.setdefault(repo.provider_repo_id, []).append({
            "bindingId": binding.id, "projectId": binding.project_id, "projectName": project_name,
            "role": binding.role, "defaultBranch": binding.default_branch or repo.default_branch,
        })
    return ok({
        "items": [
            {
                "providerRepoId": r.provider_repo_id, "fullName": r.full_name,
                "webUrl": r.web_url, "description": r.description,
                "defaultBranch": r.default_branch, "visibility": r.visibility,
                "bindings": bindings_by_remote_id.get(r.provider_repo_id, []),
            } for r in items
        ]
    })


@router.post("/{connection_id}/sync")
async def sync_connection_repos(
    connection_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """刷新连接下全部远端仓库快照；现有项目绑定自动保留并在列表中回显。"""
    build_authorizer(user).require("expert:resource_manage")
    conn = await _load_conn(session, connection_id)
    if conn.status == "invalid":
        raise BizError(BizCode.VALIDATION, "连接已失效，请先更新 token")
    try:
        count = await _sync_remote_repos(session, conn)
    except ProviderError as e:
        raise BizError(BizCode.VALIDATION, str(e))
    await session.commit()
    return ok({"count": count}, f"已同步 {count} 个远端仓库")


# ---------- 项目 ↔ 仓库绑定 ----------

@repos_router.get("")
async def list_repos(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """全局仓库列表（含绑定项目）：代码仓库中心页使用。"""
    from flowhub_api.models import Project

    repos = (await session.execute(
        select(Repo, RepoConnection)
        .join(RepoConnection, RepoConnection.id == Repo.connection_id)
        .order_by(Repo.full_name.asc())
    )).all()
    binds = (await session.execute(
        select(ProjectRepoBinding, Project.name)
        .join(Project, Project.id == ProjectRepoBinding.project_id)
    )).all()
    by_repo: dict[str, list[dict]] = {}
    for b, project_name in binds:
        by_repo.setdefault(b.repo_id, []).append({
            "bindingId": b.id, "projectId": b.project_id, "projectName": project_name,
            "role": b.role, "defaultBranch": b.default_branch,
        })
    return ok({
        "items": [
            {
                "id": r.id, "fullName": r.full_name, "webUrl": r.web_url,
                "description": r.description, "defaultBranch": r.default_branch,
                "visibility": r.visibility, "syncedAt": r.synced_at,
                "provider": c.provider, "connectionId": c.id, "connectionName": c.name,
                "connectionStatus": c.status, "bindings": by_repo.get(r.id, []),
            } for r, c in repos
        ],
        "total": len(repos),
    })


@project_router.get("/{project_id}/repos")
async def list_project_repos(
    project_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = (await session.execute(
        select(ProjectRepoBinding, Repo, RepoConnection)
        .join(Repo, Repo.id == ProjectRepoBinding.repo_id)
        .join(RepoConnection, RepoConnection.id == Repo.connection_id)
        .where(ProjectRepoBinding.project_id == project_id)
        .order_by(ProjectRepoBinding.created_at.asc())
    )).all()
    return ok({"items": [_binding_dict(b, r, c) for b, r, c in rows], "total": len(rows)})


async def _fetch_remote(session: AsyncSession, conn: RepoConnection, body: RepoBindingCreateReq):
    provider = get_provider(conn.provider, decrypt_secret(conn.token_ciphertext), conn.base_url)
    try:
        if body.provider_repo_id:
            return await provider.get_remote_repo(body.provider_repo_id)
        if body.full_name:
            return await provider.get_remote_repo(body.full_name)
    except ProviderError as e:
        raise BizError(BizCode.VALIDATION, str(e))
    raise BizError(BizCode.VALIDATION, "需要提供 provider_repo_id 或 full_name")


@project_router.post("/{project_id}/repos")
async def bind_repo(
    project_id: str,
    body: RepoBindingCreateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("project:update")
    project = await session.get(Project, project_id)
    if project is None:
        raise BizError(BizCode.NOT_FOUND, "项目不存在")
    if project.read_only or project.status == "archived":
        raise BizError(BizCode.FORBIDDEN, "归档项目只读，无法绑定仓库")
    conn = await _load_conn(session, body.connection_id)
    remote = await _fetch_remote(session, conn, body)

    # Repo 按（连接，远端 id）全局唯一：已被其它项目绑定时直接复用记录
    repo = (await session.execute(
        select(Repo).where(Repo.connection_id == conn.id, Repo.provider_repo_id == remote.provider_repo_id)
    )).scalar_one_or_none()
    if repo is None:
        repo = Repo(
            id=gen_id("rp"), connection_id=conn.id, provider_repo_id=remote.provider_repo_id,
            full_name=remote.full_name, web_url=remote.web_url, description=remote.description,
            default_branch=remote.default_branch, visibility=remote.visibility, synced_at=now_ts(),
        )
        session.add(repo)
        await session.flush()
    else:
        # 快照刷新：以远端最新元信息为准
        repo.full_name, repo.web_url = remote.full_name, remote.web_url
        repo.description, repo.default_branch = remote.description, remote.default_branch
        repo.visibility, repo.synced_at = remote.visibility, now_ts()

    dup = (await session.execute(
        select(ProjectRepoBinding).where(
            ProjectRepoBinding.project_id == project_id, ProjectRepoBinding.repo_id == repo.id
        )
    )).scalar_one_or_none()
    if dup:
        raise BizError(BizCode.DUPLICATE_OPERATION, f"该仓库已绑定到此项目：{repo.full_name}")

    binding = ProjectRepoBinding(
        id=gen_id("rb"), project_id=project_id, repo_id=repo.id, role=body.role,
        default_branch=body.default_branch or remote.default_branch,
        created_by=user.name, created_at=now_ts(),
    )
    session.add(binding)
    await AuditService(session).record(
        actor=user.name, action="project_repo:bind",
        target=f"{project.name} ← {repo.full_name}（{binding.role}）", result="success",
    )
    await session.commit()
    await schedule_mirror_build(project.name)  # 新增绑定立即后台构建镜像，无需等首次聊天触发
    fresh = (await session.execute(
        select(ProjectRepoBinding, Repo, RepoConnection)
        .join(Repo, Repo.id == ProjectRepoBinding.repo_id)
        .join(RepoConnection, RepoConnection.id == Repo.connection_id)
        .where(ProjectRepoBinding.id == binding.id)
    )).one()
    return ok({"item": _binding_dict(*fresh)}, f"已绑定仓库 {repo.full_name}")


@project_router.patch("/{project_id}/repos/{binding_id}")
async def update_binding(
    project_id: str,
    binding_id: str,
    body: RepoBindingUpdateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("project:update")
    binding = (await session.execute(
        select(ProjectRepoBinding).where(
            ProjectRepoBinding.id == binding_id, ProjectRepoBinding.project_id == project_id
        )
    )).scalar_one_or_none()
    if binding is None:
        raise BizError(BizCode.NOT_FOUND, "仓库绑定不存在")
    if body.role:
        binding.role = body.role
    if body.default_branch is not None:
        binding.default_branch = body.default_branch.strip()
    await AuditService(session).record(
        actor=user.name, action="project_repo:update", target=binding_id, result="success"
    )
    await session.commit()
    row = (await session.execute(
        select(ProjectRepoBinding, Repo, RepoConnection)
        .join(Repo, Repo.id == ProjectRepoBinding.repo_id)
        .join(RepoConnection, RepoConnection.id == Repo.connection_id)
        .where(ProjectRepoBinding.id == binding.id)
    )).one()
    return ok({"item": _binding_dict(*row)}, "绑定已更新")


@project_router.delete("/{project_id}/repos/{binding_id}")
async def unbind_repo(
    project_id: str,
    binding_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("project:update")
    binding = (await session.execute(
        select(ProjectRepoBinding, Repo)
        .join(Repo, Repo.id == ProjectRepoBinding.repo_id)
        .where(ProjectRepoBinding.id == binding_id, ProjectRepoBinding.project_id == project_id)
    )).first()
    if binding is None:
        raise BizError(BizCode.NOT_FOUND, "仓库绑定不存在")
    b, repo = binding
    await session.delete(b)  # 只解绑；Repo 记录保留（可能仍被其它项目引用）
    await AuditService(session).record(
        actor=user.name, action="project_repo:unbind", target=repo.full_name, result="success"
    )
    await session.commit()
    return ok(message=f"已解绑仓库 {repo.full_name}（仓库记录保留，可再次绑定）")
