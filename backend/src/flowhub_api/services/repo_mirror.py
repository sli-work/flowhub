"""仓库镜像与代码上下文服务（P0：地图层注入）。

绑定仓库首次使用时 `git clone --bare` 到本地镜像，之后 `fetch` 增量更新；
上下文只注入"地图层"——目录概览 + README + 依赖清单，按预算截断，
保证 LLM 用几百~一两千 token 就能了解仓库结构，不注入全量代码。
"""
import asyncio
import json
import os
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import StructuredTool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import Project, ProjectRepoBinding, Repo, RepoConnection, TaskItem, User, WorkItem
from flowhub_api.core.config import get_settings
from flowhub_api.services.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# 镜像根目录：容器内通过 REPO_MIRROR_ROOT=/app/.repo-mirror 对齐持久化卷；本地默认 backend/.repo-mirror
MIRROR_ROOT = Path(os.environ.get("REPO_MIRROR_ROOT") or Path(__file__).resolve().parent.parent.parent.parent / ".repo-mirror")
CLONE_TIMEOUT = 90
FETCH_TIMEOUT = 30
_MAX_TREE_ENTRIES = 600
_MAX_README_CHARS = 1200
_MAX_MANIFEST_CHARS = 600
_MAX_REPOS = 6
_GRAPHIFY_MAX_PROMPT_CHARS = 2400
_MAX_QUERY_SYMBOLS = 8
REPO_TOOL_MAX_READ_LINES = 300
REPO_TOOL_MAX_READ_CHARS = 16_000
REPO_TOOL_MAX_SEARCH_RESULTS = 60
REPO_TOOL_MAX_SEARCH_SCAN_RESULTS = 600
REPO_TOOL_MAX_LIST_RESULTS = 300
REPO_TOOL_MAX_DIFF_LINES = 300
GRAPHIFY_PREFLIGHT_MAX_CHARS = 12_000


class RepoToolError(ValueError):
    """A safe, user-visible rejection for a read-only repository tool."""


def _validated_repo_path(value: str) -> str:
    """Normalize a repository-relative path without allowing mirror escape."""
    raw = (value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("~"):
        raise RepoToolError("路径必须是非空的仓库相对路径")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise RepoToolError("路径不能包含空段、. 或 ..")
    if any(char in raw for char in (":", "*", "?", "[", "]", "(", ")")):
        raise RepoToolError("路径不能包含 Git pathspec 或 glob 特殊字符")
    return "/".join(parts)


def _validated_git_ref(value: str) -> str:
    """Restrict tool reads to immutable-looking Git object IDs, never ref names."""
    ref = (value or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", ref):
        raise RepoToolError("commit 必须是 7-64 位十六进制 Git 对象 ID")
    return ref.lower()


def _tool_result(tool: str, commit: str, evidence: str, *, truncated: bool = False, result_count: int = 0, **extra) -> dict:
    """Use one compact, prompt-safe envelope for every repository tool."""
    return {
        "tool": tool,
        "commit": commit,
        "evidence": evidence,
        "truncated": truncated,
        "result_count": result_count,
        **extra,
    }

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


def graphify_dir(repo_id: str) -> Path:
    """Separate generated AST artifacts from bare mirrors and checked-out code."""
    return MIRROR_ROOT / "graphify" / repo_id


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
                proc = await asyncio.to_thread(_run_git, ["fetch", "--all", "--prune"], path, FETCH_TIMEOUT)
                if proc.returncode != 0:
                    logger.warning("仓库镜像 fetch 失败 %s: %s", repo.full_name, proc.stderr[:200])
                    return None
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


def _mirror_commit(mirror: Path) -> str:
    proc = _run_git(["rev-parse", "HEAD"], mirror, 15)
    return proc.stdout.strip() if proc.returncode == 0 else ""


async def ensure_graphify_map(repo: Repo, mirror: Path) -> Path | None:
    """Build a versioned Graphify AST graph from an isolated, detached worktree.

    Graphify never receives the bare mirror and all generated output is staged
    before replacing an older valid graph. This keeps code maps tenant-scoped
    and lets callers fall back safely when the analyzer is unavailable.
    """
    settings = get_settings()
    if not settings.graphify_enabled:
        return None
    lock = _locks.setdefault(repo.id, asyncio.Lock())
    async with lock:
        commit = await asyncio.to_thread(_mirror_commit, mirror)
        if not commit:
            return None
        target = graphify_dir(repo.id)
        try:
            metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
            if metadata.get("commit") == commit and graphify_map_text(repo.full_name, "", target, max_chars=1):
                return target
        except (OSError, json.JSONDecodeError):
            pass

        work_root = Path(tempfile.mkdtemp(prefix=f"flowhub-{repo.id}-", dir=MIRROR_ROOT))
        checkout = work_root / "source"
        output_root = Path(tempfile.mkdtemp(prefix=f"flowhub-graph-{repo.id}-", dir=MIRROR_ROOT))
        branch = commit
        try:
            add = await asyncio.to_thread(
                _run_git, ["--git-dir", str(mirror), "worktree", "add", "--detach", str(checkout), branch], None, FETCH_TIMEOUT,
            )
            if add.returncode != 0:
                logger.warning("Graphify 工作树创建失败 %s: %s", repo.full_name, add.stderr[:200])
                return None
            command = [settings.graphify_command, "extract", str(checkout), "--code-only", "--out", str(output_root), "--max-workers", str(settings.graphify_max_workers)]
            process = await asyncio.to_thread(
                subprocess.run, command, capture_output=True, text=True, timeout=settings.graphify_timeout_seconds,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
            )
            if process.returncode != 0:
                logger.warning("Graphify 构图失败 %s: %s", repo.full_name, process.stderr[:300])
                return None
            (output_root / "metadata.json").write_text(json.dumps({"commit": commit}), encoding="utf-8")
            if not graphify_map_text(repo.full_name, "", output_root, max_chars=1):
                logger.warning("Graphify 产物无有效图谱 %s", repo.full_name)
                return None
            backup = target.with_name(f"{target.name}.previous")
            shutil.rmtree(backup, ignore_errors=True)
            if target.exists():
                os.replace(target, backup)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(output_root, target)
            shutil.rmtree(backup, ignore_errors=True)
            return target
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("Graphify 构图异常 %s: %s", repo.full_name, exc)
            return None
        finally:
            await asyncio.to_thread(_run_git, ["--git-dir", str(mirror), "worktree", "remove", "--force", str(checkout)], None, 30)
            shutil.rmtree(work_root, ignore_errors=True)
            shutil.rmtree(output_root, ignore_errors=True)


async def ready_graphify_map(repo: Repo, mirror: Path) -> Path | None:
    """Return only a current prebuilt graph; never block an interactive read."""
    commit = await asyncio.to_thread(_mirror_commit, mirror)
    if not commit:
        return None
    target = graphify_dir(repo.id)
    try:
        metadata = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("commit") == commit and graphify_map_text(repo.full_name, "", target, max_chars=1):
            return target
    except (OSError, json.JSONDecodeError):
        return None
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


def repo_read_file(mirror: Path, commit: str, path: str, *, start_line: int = 1, end_line: int = REPO_TOOL_MAX_READ_LINES) -> dict:
    """Read a bounded text range from an immutable repository object."""
    ref, filename = _validated_git_ref(commit), _validated_repo_path(path)
    first = max(1, int(start_line))
    last = min(max(first, int(end_line)), first + REPO_TOOL_MAX_READ_LINES - 1)
    proc = _run_git(["show", f"{ref}:{filename}"], mirror, 15)
    if proc.returncode != 0:
        raise RepoToolError("文件不存在、不可读，或不属于指定 commit")
    text = proc.stdout
    if "\x00" in text:
        raise RepoToolError("不支持读取二进制文件")
    lines = text.splitlines()
    selected = lines[first - 1:last]
    evidence_lines: list[str] = []
    chars = 0
    truncated = last < len(lines)
    for index, line in enumerate(selected, start=first):
        rendered = f"{filename}:L{index} {line}"
        if chars + len(rendered) + 1 > REPO_TOOL_MAX_READ_CHARS:
            truncated = True
            break
        evidence_lines.append(rendered)
        chars += len(rendered) + 1
    return _tool_result("repo_read_file", ref, "\n".join(evidence_lines) or "（指定范围为空）",
                        truncated=truncated, result_count=len(evidence_lines), path=filename,
                        start_line=first, end_line=first + len(evidence_lines) - 1 if evidence_lines else first)


def repo_search(mirror: Path, commit: str, query: str, *, path: str | None = None, glob: str | None = None,
                regex: bool = False, limit: int = REPO_TOOL_MAX_SEARCH_RESULTS) -> dict:
    """Search a pinned tree with argv-only Git invocation and bounded evidence."""
    ref = _validated_git_ref(commit)
    pattern = (query or "").strip()
    if not pattern:
        raise RepoToolError("搜索关键词不能为空")
    max_results = min(max(1, int(limit)), REPO_TOOL_MAX_SEARCH_RESULTS)
    args = ["grep", "-n"]
    if not regex:
        args.append("--fixed-strings")
    # -e keeps a user pattern from being parsed as a Git option while retaining
    # the following tree object as the search target.
    # A glob is applied after git emits a match, so scan a bounded larger page
    # first.  Otherwise a busy generated directory can hide the only relevant
    # service/controller match behind the initial result limit.
    scan_limit = max_results if not glob else min(REPO_TOOL_MAX_SEARCH_SCAN_RESULTS, max_results * 10)
    args.extend(["-m", str(scan_limit), "-e", pattern, ref])
    if path:
        args.extend(["--", _validated_repo_path(path)])
    proc = _run_git(args, mirror, 15)
    if proc.returncode not in (0, 1):
        raise RepoToolError("代码搜索失败")
    raw_lines = proc.stdout.splitlines()[:scan_limit]
    filtered = [line for line in raw_lines if not glob or __import__("fnmatch").fnmatch(line.split(":", 2)[1] if ":" in line else "", glob)]
    evidence: list[str] = []
    for line in filtered[:max_results]:
        # git grep prints <commit>:<path>:<line>:<text> for tree objects.
        match = re.match(r"(?:[0-9a-fA-F]{6,64}:)?(.+?):(\d+):(.*)", line)
        if match:
            evidence.append(f"{match.group(1)}:L{match.group(2)} {match.group(3)}")
    return _tool_result("repo_search", ref, "\n".join(evidence) or "未找到匹配代码",
                        truncated=len(raw_lines) >= scan_limit or len(filtered) > max_results, result_count=len(evidence), query=pattern,
                        path=_validated_repo_path(path) if path else "", glob=glob or "", regex=bool(regex))


def repo_list_files(mirror: Path, commit: str, *, path: str | None = None, limit: int = REPO_TOOL_MAX_LIST_RESULTS) -> dict:
    ref = _validated_git_ref(commit)
    max_results = min(max(1, int(limit)), REPO_TOOL_MAX_LIST_RESULTS)
    args = ["ls-tree", "-r", "--name-only", ref]
    prefix = _validated_repo_path(path) if path else ""
    if prefix:
        args.extend(["--", prefix])
    proc = _run_git(args, mirror, 15)
    if proc.returncode != 0:
        raise RepoToolError("无法列出指定 commit 的文件")
    files = [line for line in proc.stdout.splitlines() if line][:max_results]
    return _tool_result("repo_list_files", ref, "\n".join(files) or "未找到文件",
                        truncated=len(proc.stdout.splitlines()) > max_results, result_count=len(files), path=prefix)


def repo_find_files(mirror: Path, commit: str, pattern: str, *, path: str | None = None,
                    limit: int = REPO_TOOL_MAX_LIST_RESULTS) -> dict:
    import fnmatch

    raw_pattern = (pattern or "").strip()
    if not raw_pattern:
        raise RepoToolError("文件匹配模式不能为空")
    ref = _validated_git_ref(commit)
    prefix = _validated_repo_path(path) if path else ""
    args = ["ls-tree", "-r", "--name-only", ref]
    if prefix:
        args.extend(["--", prefix])
    proc = _run_git(args, mirror, 15)
    if proc.returncode != 0:
        raise RepoToolError("无法列出指定 commit 的文件")
    # Filter the complete Git response before applying the model-facing cap;
    # this prevents a false "0 results" on large repositories.
    matches = [name for name in proc.stdout.splitlines() if name and fnmatch.fnmatch(name, raw_pattern)]
    max_results = min(max(1, int(limit)), REPO_TOOL_MAX_LIST_RESULTS)
    return _tool_result("repo_find_files", ref, "\n".join(matches[:max_results]) or "未找到匹配文件",
                        truncated=len(matches) > max_results, result_count=min(len(matches), max_results),
                        pattern=raw_pattern, path=prefix)


def repo_git_diff(mirror: Path, base_commit: str, head_commit: str) -> dict:
    base, head = _validated_git_ref(base_commit), _validated_git_ref(head_commit)
    proc = _run_git(["diff", "--no-ext-diff", "--unified=3", base, head], mirror, 20)
    if proc.returncode != 0:
        raise RepoToolError("无法读取指定 commit 间的变更")
    lines = proc.stdout.splitlines()
    clipped = lines[:REPO_TOOL_MAX_DIFF_LINES]
    return _tool_result("repo_git_diff", head, "\n".join(clipped) or "两个 commit 间没有变更",
                        truncated=len(lines) > len(clipped), result_count=len(clipped), base_commit=base, head_commit=head)


@dataclass
class RepoToolBundle:
    """The model-facing tools plus trace metadata that must not expose source bodies."""

    tools: list[StructuredTool]
    traces: list[dict] = field(default_factory=list)
    evidence_context: list[str] = field(default_factory=list)


async def can_user_read_project(session: AsyncSession, user: User, project_name: str) -> bool:
    """Project-name bindings are untrusted until visibility is verified server-side."""
    project = (await session.execute(select(Project).where(Project.name == project_name))).scalar_one_or_none()
    if project is None:
        return False
    is_admin = user.name == "系统管理员" or any(role.id in ("system_admin", "organization_admin") for role in user.roles)
    if is_admin or user.name in (project.manager, project.owner):
        return True
    assigned_task = (await session.execute(
        select(TaskItem.id).join(WorkItem, TaskItem.wi_id == WorkItem.id)
        .where(WorkItem.project == project_name, TaskItem.assignee == user.name).limit(1)
    )).scalar_one_or_none()
    visible_work_item = (await session.execute(
        select(WorkItem.id).where(
            WorkItem.project == project_name,
            or_(WorkItem.assignee == user.name, WorkItem.creator == user.name),
        ).limit(1)
    )).scalar_one_or_none()
    return bool(assigned_task or visible_work_item)


async def create_repo_tool_bundle(session: AsyncSession, project_name: str, *, user: User, query: str = "", allow_clone: bool = False) -> RepoToolBundle:
    """Build Pi-style, project-scoped read-only tools for one LangGraph turn.

    The repository selection is closed over by this function.  The model can
    select a bound repository by full name, but cannot provide a filesystem
    path, connection ID, or arbitrary Git remote.
    """
    if not await can_user_read_project(session, user, project_name):
        logger.warning("拒绝未授权仓库工具访问 user=%s project=%s", user.id, project_name)
        return RepoToolBundle(tools=[])
    rows = (await session.execute(
        select(ProjectRepoBinding, Repo)
        .join(Project, ProjectRepoBinding.project_id == Project.id)
        .join(Repo, ProjectRepoBinding.repo_id == Repo.id)
        .where(Project.name == project_name)
        .order_by(ProjectRepoBinding.id)
        .limit(_MAX_REPOS)
    )).all()
    available: dict[str, tuple[Repo, ProjectRepoBinding, Path, str, set[str]]] = {}
    for binding, repo in rows:
        mirror = mirror_dir(repo.id)
        if not mirror.is_dir() and allow_clone:
            connection = await session.get(RepoConnection, repo.connection_id)
            mirror = await ensure_mirror(repo, connection) if connection else None
        if mirror is None or not mirror.is_dir():
            continue
        commit = await asyncio.to_thread(_mirror_commit, mirror)
        if commit:
            parent = await asyncio.to_thread(_run_git, ["rev-parse", f"{commit}^"], mirror, 15)
            allowed_commits = {commit}
            if parent.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{7,64}", parent.stdout.strip()):
                allowed_commits.add(parent.stdout.strip().lower())
            available[repo.full_name] = (repo, binding, mirror, commit, allowed_commits)
    if not available:
        return RepoToolBundle(tools=[])

    bundle = RepoToolBundle(tools=[])

    # Graphify is a deterministic preflight, not a tool the model may forget
    # to choose.  Its evidence is immediately available to the first model
    # invocation and does not consume the interactive tool-call budget.
    if query.strip():
        remaining_graph_chars = GRAPHIFY_PREFLIGHT_MAX_CHARS
        for repo, binding, mirror, _, _ in available.values():
            graph_dir = await ready_graphify_map(repo, mirror)
            if graph_dir is None:
                bundle.traces.append({"tool": "flowhub.repo.graphify", "status": "unavailable",
                                      "summary": f"{repo.full_name}：Graphify 图谱不可用，已准备定向文本检索降级"})
                continue
            evidence = graphify_query_text(
                repo.full_name, binding.role, graph_dir, query, mirror=mirror,
                max_chars=min(get_settings().graphify_max_prompt_chars, remaining_graph_chars),
            )
            if not evidence:
                evidence = graphify_map_text(
                    repo.full_name, binding.role, graph_dir,
                    max_chars=min(get_settings().graphify_max_prompt_chars, remaining_graph_chars),
                )
            if evidence:
                bundle.evidence_context.append(evidence)
                remaining_graph_chars -= len(evidence)
                bundle.traces.append({"tool": "flowhub.repo.graphify", "status": "succeeded",
                                      "summary": f"{repo.full_name}：Graphify 预分析已完成，已注入问题相关符号或代码地图"})
            else:
                bundle.traces.append({"tool": "flowhub.repo.graphify", "status": "not_found",
                                      "summary": f"{repo.full_name}：Graphify 未命中问题相关符号，允许定向文本检索"})
            if remaining_graph_chars <= 0:
                break

    def resolve(repo_name: str | None) -> tuple[Repo, ProjectRepoBinding, Path, str, set[str]]:
        chosen = (repo_name or "").strip() or next(iter(available))
        entry = available.get(chosen)
        if entry is None:
            raise RepoToolError("repo 必须是当前项目已绑定且镜像可用的仓库全名")
        return entry

    def render(tool_name: str, repo: Repo, result: dict) -> str:
        bundle.traces.append({
            "tool": tool_name, "status": "succeeded",
            "summary": f"{repo.full_name}@{result['commit'][:12]}：{result.get('result_count', 0)} 条结果"
                      + ("（已截断）" if result.get("truncated") else ""),
        })
        bundle.evidence_context.append(
            f"【代码证据｜{repo.full_name}｜commit {result['commit'][:12]}｜{tool_name}】\n{result['evidence']}"
        )
        return json.dumps({"repository": repo.full_name, **result}, ensure_ascii=False)

    async def list_files(repo: str = "", path: str = "", limit: int = 200) -> str:
        if not path.strip():
            raise RepoToolError("repo_list_files 必须指定已知目录；请先使用 Graphify、符号查询或定向搜索定位路径")
        selected, _, mirror, commit, _ = resolve(repo)
        return render("repo_list_files", selected, repo_list_files(mirror, commit, path=path or None, limit=limit))

    async def find_files(pattern: str, repo: str = "", path: str = "", limit: int = 200) -> str:
        selected, _, mirror, commit, _ = resolve(repo)
        return render("repo_find_files", selected, repo_find_files(mirror, commit, pattern, path=path or None, limit=limit))

    async def search(query: str, repo: str = "", path: str = "", glob: str = "", regex: bool = False, limit: int = 40) -> str:
        selected, _, mirror, commit, _ = resolve(repo)
        return render("repo_search", selected, repo_search(mirror, commit, query, path=path or None, glob=glob or None, regex=regex, limit=limit))

    async def read_file(path: str, repo: str = "", start_line: int = 1, end_line: int = REPO_TOOL_MAX_READ_LINES) -> str:
        selected, _, mirror, commit, _ = resolve(repo)
        return render("repo_read_file", selected, repo_read_file(mirror, commit, path, start_line=start_line, end_line=end_line))

    async def find_symbol(symbol: str, repo: str = "") -> str:
        selected, _, mirror, commit, _ = resolve(repo)
        graph_dir = await ensure_graphify_map(selected, mirror)
        evidence = graphify_query_text(selected.full_name, "绑定仓库", graph_dir, symbol, mirror=mirror) if graph_dir else ""
        if not evidence:
            result = repo_search(mirror, commit, symbol, limit=REPO_TOOL_MAX_SEARCH_RESULTS)
        else:
            result = _tool_result("repo_find_symbol", commit, evidence, result_count=1, symbol=symbol, graphify=True)
        return render("repo_find_symbol", selected, result)

    async def trace_symbol(symbol: str, repo: str = "") -> str:
        selected, _, mirror, commit, _ = resolve(repo)
        graph_dir = await ensure_graphify_map(selected, mirror)
        evidence = graphify_query_text(selected.full_name, "绑定仓库", graph_dir, f"{symbol} 调用链", mirror=mirror) if graph_dir else ""
        result = (_tool_result("repo_trace_symbol", commit, evidence, result_count=1, symbol=symbol, graphify=True)
                  if evidence else _tool_result("repo_trace_symbol", commit, "图谱不可用或未命中；请改用 repo_search 获取文本证据。", result_count=0, symbol=symbol))
        return render("repo_trace_symbol", selected, result)

    async def git_diff(base_commit: str, head_commit: str = "", repo: str = "") -> str:
        selected, _, mirror, current_commit, allowed_commits = resolve(repo)
        if _validated_git_ref(base_commit) not in allowed_commits or (head_commit and _validated_git_ref(head_commit) not in allowed_commits):
            raise RepoToolError("diff 仅允许当前快照及其直接父 commit")
        return render("repo_git_diff", selected, repo_git_diff(mirror, base_commit, head_commit or current_commit))

    bundle.tools = [
        StructuredTool.from_function(coroutine=list_files, name="repo_list_files", description="仅在已知目录时列出当前项目绑定仓库的文件；不要用它做根目录泛搜。只读、结果受限。"),
        StructuredTool.from_function(coroutine=find_files, name="repo_find_files", description="按 glob 模式查找当前项目绑定仓库的文件；只读。"),
        StructuredTool.from_function(coroutine=search, name="repo_search", description="在 Graphify 未覆盖的明确关键词、路径或 glob 上定向搜索代码文本，返回固定 commit 的文件和行号证据。"),
        StructuredTool.from_function(coroutine=read_file, name="repo_read_file", description="读取当前项目绑定仓库中某个文件的有限行范围；只读。"),
        StructuredTool.from_function(coroutine=find_symbol, name="repo_find_symbol", description="优先用代码图谱查找符号；未命中时返回源码文本证据。"),
        StructuredTool.from_function(coroutine=trace_symbol, name="repo_trace_symbol", description="查询符号的代码图谱调用或依赖关系；图谱不可用时会明确说明。"),
        StructuredTool.from_function(coroutine=git_diff, name="repo_git_diff", description="读取两个固定 commit 之间的受限代码差异；只读。"),
    ]
    return bundle


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


def graphify_map_text(repo_name: str, binding_role: str, graph_dir: Path, *, max_chars: int = _GRAPHIFY_MAX_PROMPT_CHARS) -> str:
    """Convert Graphify's local AST graph into a bounded, prompt-safe code map."""
    try:
        graph = json.loads((graph_dir / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
        metadata = json.loads((graph_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    raw_nodes = graph.get("nodes") or []
    if isinstance(raw_nodes, dict):
        nodes = [{"id": node_id, **(data if isinstance(data, dict) else {})} for node_id, data in raw_nodes.items()]
    else:
        nodes = [node for node in raw_nodes if isinstance(node, dict)]
    raw_edges = graph.get("edges") or []
    edges = raw_edges.values() if isinstance(raw_edges, dict) else raw_edges
    symbols: list[str] = []
    for node in nodes[:24]:
        label = str(node.get("label") or node.get("name") or node.get("id") or "").strip()
        source = str(node.get("source_file") or node.get("file") or "").strip()
        kind = str(node.get("type") or node.get("kind") or "符号").strip()
        location = str(node.get("source_location") or node.get("line") or "").strip()
        if label:
            symbols.append(f"{label}（{kind}{f' · {source}' if source else ''}{f':{location}' if location else ''}）")
    edge_count = sum(1 for edge in edges if isinstance(edge, dict))
    lines = [
        f"  - {repo_name}（{binding_role} · Graphify 代码地图 · 提交 {str(metadata.get('commit') or '未知')[:12]}）",
        f"    图谱规模：符号 {len(nodes)} · 调用关系 {edge_count}",
    ]
    if symbols:
        lines.append("    核心符号：" + "；".join(symbols))
    result = "\n".join(lines)
    return result[:max_chars]


def graphify_query_text(repo_name: str, binding_role: str, graph_dir: Path, query: str, *, mirror: Path | None = None, max_chars: int = _GRAPHIFY_MAX_PROMPT_CHARS) -> str:
    """Return query-ranked symbols and their graph neighbours as code evidence."""
    try:
        graph = json.loads((graph_dir / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
        metadata = json.loads((graph_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    raw_nodes = graph.get("nodes") or []
    nodes = ([{"id": key, **(value if isinstance(value, dict) else {})} for key, value in raw_nodes.items()]
             if isinstance(raw_nodes, dict) else [node for node in raw_nodes if isinstance(node, dict)])
    query_terms = {term.lower() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]{1,}|[\u4e00-\u9fff]{2,}", query)}
    query_terms.update(part for term in tuple(query_terms) for part in re.split(r"[_./-]", term) if len(part) >= 2)
    def score(node: dict) -> int:
        text = " ".join(str(node.get(key) or "") for key in ("id", "label", "name", "source_file", "file", "type")).lower()
        name = str(node.get("label") or node.get("name") or node.get("id") or "").lower()
        return sum((10 if term == name else 1) for term in query_terms if term in text)
    ranked = sorted((node for node in nodes if score(node)), key=score, reverse=True)[:_MAX_QUERY_SYMBOLS]
    if not ranked:
        if mirror is not None and query_terms:
            term = max(query_terms, key=len)
            proc = _run_git(["grep", "-n", "--fixed-strings", "-m", "8", term, str(metadata.get("commit") or "HEAD")], mirror, 15)
            if proc.returncode == 0 and proc.stdout.strip():
                return (f"【代码证据｜{repo_name}｜commit {str(metadata.get('commit') or '未知')[:12]}｜角色 {binding_role}】\n"
                        f"文本检索证据：{proc.stdout[:1200]}\n回答必须说明这是文本匹配，未解析调用关系。")[:max_chars]
        return ""
    selected_ids = {str(node.get("id") or node.get("name") or "") for node in ranked}
    node_names = {str(node.get("id") or node.get("name") or ""): str(node.get("label") or node.get("name") or node.get("id") or "") for node in nodes}
    raw_edges = graph.get("links") or graph.get("edges") or []
    edges = raw_edges.values() if isinstance(raw_edges, dict) else raw_edges
    relations: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_value, target_value = edge.get("source") or "", edge.get("target") or ""
        source = str(source_value.get("id") if isinstance(source_value, dict) else source_value)
        target = str(target_value.get("id") if isinstance(target_value, dict) else target_value)
        if source in selected_ids or target in selected_ids:
            relations.append(f"{node_names.get(source, source)} → {node_names.get(target, target)}")
    evidence = []
    commit = str(metadata.get("commit") or "")
    for node in ranked:
        name = str(node.get("label") or node.get("name") or node.get("id") or "未知符号")
        file = str(node.get("source_file") or node.get("file") or "未知文件")
        location = str(node.get("source_location") or node.get("line") or "")
        kind = str(node.get("type") or node.get("kind") or "符号")
        evidence.append(f"{name}（{kind}｜{file}{f':L{location}' if location else ''}）")
        if mirror is not None and file != "未知文件" and commit:
            try:
                line_number = int(re.search(r"\d+", location).group()) if re.search(r"\d+", location) else 1
                source = _blob_content(mirror, commit, file, 12000)
                source_lines = source.splitlines()
                snippet = "\n".join(source_lines[max(0, line_number - 2): line_number + 4])
                if snippet:
                    evidence.append(f"源码 {file}:L{line_number}：{snippet[:360]}")
            except (ValueError, AttributeError):
                pass
    lines = [
        f"【代码证据｜{repo_name}｜commit {commit[:12] or '未知'}｜角色 {binding_role}】",
        "命中符号：" + "；".join(evidence),
    ]
    if relations:
        lines.append("相关调用/依赖：" + "；".join(relations[:12]))
    lines.append("回答涉及代码事实时，必须引用以上文件、符号和 commit；图谱未命中则说明不确定。")
    return "\n".join(lines)[:max_chars]


async def repo_map_section(session: AsyncSession, project_name: str, *, user: User | None = None,
                           allow_clone: bool = True, query: str = "") -> str:
    """项目绑定仓库的"代码仓库"上下文段；无绑定/全部失败时返回空串。

    WorkItem.project 存项目名称（非 ID），经 Project.name 关联绑定表。
    """
    if user is not None and not await can_user_read_project(session, user, project_name):
        logger.warning("拒绝未授权仓库地图访问 user=%s project=%s", user.id, project_name)
        return "【关联代码仓库】无权读取该项目的代码仓库上下文。"
    rows = (await session.execute(
        select(ProjectRepoBinding, Repo)
        .join(Project, ProjectRepoBinding.project_id == Project.id)
        .join(Repo, ProjectRepoBinding.repo_id == Repo.id)
        .where(Project.name == project_name)
        .order_by(ProjectRepoBinding.id)
        .limit(_MAX_REPOS)
    )).all()
    if not rows:
        return "【关联代码仓库】未绑定代码仓库；请改为依据任务、表单、关联文档和项目上下文分析，并明确结论未经过代码实现验证。"
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
        graph_dir = await (ensure_graphify_map(repo, mirror) if allow_clone else ready_graphify_map(repo, mirror))
        if graph_dir is not None:
            graph_map = (graphify_query_text(repo.full_name, binding.role, graph_dir, query, mirror=mirror, max_chars=get_settings().graphify_max_prompt_chars)
                         if query else graphify_map_text(repo.full_name, binding.role, graph_dir, max_chars=get_settings().graphify_max_prompt_chars))
            if graph_map:
                blocks.append(graph_map)
                continue
        # Interactive paths must not synchronously build a graph. Keep a
        # compact map and let the existing background warmup build Graphify.
        fallback = repo_map_text(repo, binding.role, mirror)
        if not allow_clone:
            fallback += "\n    Graphify 图谱构建中：本轮仅提供基础代码地图，下一轮将优先使用图谱分析。"
        blocks.append(fallback)
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
