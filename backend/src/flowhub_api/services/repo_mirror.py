"""仓库镜像与代码上下文服务（P0：地图层注入）。

绑定仓库首次使用时 `git clone --bare` 到本地镜像，之后 `fetch` 增量更新；
上下文只注入"地图层"——目录概览 + README + 依赖清单，按预算截断，
保证 LLM 用几百~一两千 token 就能了解仓库结构，不注入全量代码。
"""
import asyncio
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import Project, ProjectRepoBinding, Repo, RepoConnection
from flowhub_api.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

MIRROR_ROOT = Path(__file__).resolve().parent.parent.parent.parent / ".repo-mirror"
CLONE_TIMEOUT = 90
FETCH_TIMEOUT = 30
_MAX_TREE_ENTRIES = 600
_MAX_README_CHARS = 1200
_MAX_MANIFEST_CHARS = 600
_MAX_REPOS = 6

# 依赖清单：识别技术栈最便宜的来源
_MANIFESTS = ("package.json", "pyproject.toml", "go.mod", "pom.xml", "build.gradle", "Cargo.toml", "requirements.txt")
_README_CANDIDATES = ("README.md", "readme.md", "README.rst", "README.txt", "README")

# 每 repo 一把构建锁，避免并发首次 clone
_locks: dict[str, asyncio.Lock] = {}


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = CLONE_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, timeout=timeout,
        capture_output=True, text=True,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )


def _authed_clone_url(web_url: str, token: str, provider: str) -> str:
    """把 token 注入 clone URL（凭证只存在于子进程参数与镜像 remote，不落日志/上下文）。"""
    parts = urlsplit(web_url)
    user = "x-access-token" if provider == "github" else "oauth2"
    return urlunsplit((parts.scheme, f"{user}:{token}@{parts.netloc}", parts.path, "", ""))


def mirror_dir(repo_id: str) -> Path:
    return MIRROR_ROOT / f"{repo_id}.git"


async def ensure_mirror(repo: Repo, connection: RepoConnection) -> Path | None:
    """确保本地 bare 镜像存在且较新；失败返回 None（调用方降级为无代码上下文）。"""
    if not connection.token_ciphertext or not repo.web_url:
        return None
    path = mirror_dir(repo.id)
    lock = _locks.setdefault(repo.id, asyncio.Lock())
    async with lock:
        try:
            token = decrypt_secret(connection.token_ciphertext)
            url = _authed_clone_url(repo.web_url, token, connection.provider)
            if path.is_dir():
                await asyncio.to_thread(_run_git, ["fetch", "--all", "--prune"], path, FETCH_TIMEOUT)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                proc = await asyncio.to_thread(_run_git, ["clone", "--bare", url, str(path)], None, CLONE_TIMEOUT)
                if proc.returncode != 0:
                    logger.warning("仓库镜像 clone 失败 %s: %s", repo.full_name, proc.stderr[:200])
                    return None
            return path
        except Exception as exc:  # noqa: BLE001 — 镜像不可用时降级，不阻断流程
            logger.warning("仓库镜像维护失败 %s: %s", repo.full_name, exc)
            return None


def _ls_tree(path: Path, branch: str) -> list[str]:
    proc = _run_git(["ls-tree", "-r", "--name-only", branch], path, 15)
    if proc.returncode != 0 and branch != "HEAD":
        proc = _run_git(["ls-tree", "-r", "--name-only", "HEAD"], path, 15)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line][:_MAX_TREE_ENTRIES]


def _top_level_summary(files: list[str]) -> str:
    dirs: dict[str, int] = {}
    root_files = 0
    for f in files:
        if "/" in f:
            top = f.split("/", 1)[0]
            dirs[top] = dirs.get(top, 0) + 1
        else:
            root_files += 1
    parts = [f"{d}/({n} 文件)" for d, n in sorted(dirs.items(), key=lambda kv: -kv[1])[:12]]
    if root_files:
        parts.append(f"根目录文件 ×{root_files}")
    return " · ".join(parts)


def _blob_content(path: Path, branch: str, filename: str, limit: int) -> str:
    proc = _run_git(["show", f"{branch}:{filename}"], path, 10)
    if proc.returncode != 0:
        return ""
    text = proc.stdout[:limit]
    return text + ("\n…（已截断）" if len(proc.stdout) > limit else "")


def repo_map_text(repo: Repo, binding_role: str, mirror: Path) -> str:
    """单个仓库的地图层文本：概览 + 目录摘要 + README + 依赖清单。"""
    branch = repo.default_branch or "HEAD"
    files = _ls_tree(mirror, branch)
    if not files:
        return f"  - {repo.full_name}（{binding_role}）：镜像不可用，无法读取代码结构"
    lines = [
        f"  - {repo.full_name}（{binding_role} · 默认分支 {branch}）"
        + (f"｜{repo.description}" if repo.description else ""),
        f"    目录概览：{_top_level_summary(files)}",
    ]
    for candidate in _README_CANDIDATES:
        if candidate in files:
            body = _blob_content(mirror, branch, candidate, _MAX_README_CHARS)
            if body:
                lines.append(f"    {candidate} 摘要：\n      " + body.replace("\n", "\n      "))
            break
    for name in _MANIFESTS:
        if name in files:
            body = _blob_content(mirror, branch, name, _MAX_MANIFEST_CHARS)
            if body:
                lines.append(f"    依赖清单 {name}：\n      " + body.replace("\n", "\n      "))
    return "\n".join(lines)


async def repo_map_section(session: AsyncSession, project_name: str, *, allow_clone: bool = True) -> str:
    """项目绑定仓库的"代码仓库"上下文段；无绑定/全部失败时返回空串。

    WorkItem.project 存项目名称（非 ID），经 Project.name 关联绑定表。
    """
    rows = (await session.execute(
        select(ProjectRepoBinding, Repo)
        .join(Project, ProjectRepoBinding.project_id == Project.id)
        .join(Repo, ProjectRepoBinding.repo_id == Repo.id)
        .where(Project.name == project_name)
        .order_by(ProjectRepoBinding.id)
        .limit(_MAX_REPOS)
    )).all()
    if not rows:
        return ""
    blocks: list[str] = []
    for binding, repo in rows:
        mirror = mirror_dir(repo.id)
        if not mirror.is_dir():
            if not allow_clone:
                blocks.append(f"  - {repo.full_name}（{binding.role}）：镜像构建中，稍后可用")
                continue
            connection = await session.get(RepoConnection, repo.connection_id)
            if connection is None:
                continue
            mirror = await ensure_mirror(repo, connection)
            if mirror is None:
                blocks.append(f"  - {repo.full_name}（{binding.role}）：镜像不可用")
                continue
        blocks.append(repo_map_text(repo, binding.role, mirror))
    if not blocks:
        return ""
    return "【关联代码仓库】\n" + "\n".join(blocks)


async def schedule_mirror_build(project_name: str) -> None:
    """后台为项目绑定仓库构建镜像（请求结束后继续执行，不阻塞页面/任务书渲染）。"""
    from flowhub_api.db.session import SessionFactory

    async def _build() -> None:
        try:
            async with SessionFactory() as session:
                await repo_map_section(session, project_name, allow_clone=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("仓库镜像后台构建失败 project=%s: %s", project_name, exc)

    asyncio.create_task(_build())


async def schedule_all_mirror_builds() -> None:
    """启动预热：为所有已绑定仓库的项目后台构建/刷新镜像（不阻塞启动）。

    覆盖两类场景：后端重启丢失在途构建任务；历史绑定从未触发过构建。
    ensure_mirror 幂等（已存在则 fetch 增量更新），重复执行无副作用。
    """
    from flowhub_api.db.session import SessionFactory

    async def _warmup() -> None:
        try:
            async with SessionFactory() as session:
                names = (await session.execute(
                    select(Project.name).distinct()
                    .join(ProjectRepoBinding, ProjectRepoBinding.project_id == Project.id)
                )).scalars().all()
            for name in names:
                await schedule_mirror_build(name)
            if names:
                logger.info("仓库镜像预热：为 %d 个绑定项目后台构建镜像 %s", len(names), list(names))
        except Exception as exc:  # noqa: BLE001
            logger.warning("仓库镜像启动预热失败: %s", exc)

    asyncio.create_task(_warmup())
