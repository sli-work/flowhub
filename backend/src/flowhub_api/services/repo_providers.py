"""代码托管平台适配层：GitHub / GitLab（含自建）统一接口。

差异（认证头、搜索与分页、字段命名）全部关闭在 provider 内部，
路由层只依赖 verify / list_remote_repos / get_remote_repo 三个操作。
"""
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"
GITLAB_API = "https://gitlab.com"
_TIMEOUT = 10


def _gitlab_project_key(full_name: str) -> str:
    """GitLab 项目定位键：URL 编码的 namespace/path（部分旧版本 GitLab 不支持 base64 形式）。

    兼容粘贴输入：数字 id 原样保留；完整 clone URL（http://host/group/repo.git）
    截取 path 并去掉 .git 后缀。
    """
    key = full_name.strip()
    if "://" in key:
        key = "/".join(key.split("://", 1)[1].split("/")[1:])
    if key.endswith(".git"):
        key = key[: -len(".git")]
    return quote(key, safe="")


class ProviderError(Exception):
    """平台 API 调用失败（认证失效 / 网络错误 / 仓库不存在）。"""


@dataclass
class RemoteRepo:
    """远端仓库元信息快照（provider 无关）。"""

    provider_repo_id: str
    full_name: str
    web_url: str
    description: str
    default_branch: str
    visibility: str


def _visibility_github(repo: dict) -> str:
    return "private" if repo.get("private") else "public"


class GithubProvider:
    """github.com（token = PAT，需 repo 读权限）。"""

    provider = "github"

    def __init__(self, token: str, base_url: str = ""):
        self.token = token
        self.api_base = (base_url.rstrip("/") or GITHUB_API).replace("https://github.com", GITHUB_API)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as c:
            resp = await c.get(f"{self.api_base}{path}", headers=self._headers(), params=params)
        if resp.status_code in (401, 403):
            raise ProviderError("GitHub 认证失败：token 无效或权限不足")
        return resp

    async def verify(self) -> str:
        resp = await self._get("/user")
        if resp.status_code != 200:
            raise ProviderError(f"GitHub 连接失败（HTTP {resp.status_code}）")
        return resp.json().get("login", "")

    async def list_remote_repos(self, query: str = "", limit: int = 500) -> list[RemoteRepo]:
        """列出 token 可见的仓库（owner / collaborator / org member），按关键字本地过滤。"""
        items: list[dict] = []
        page = 1
        while len(items) < limit:
            per_page = min(100, limit - len(items))
            resp = await self._get("/user/repos", params={
                "visibility": "all", "affiliation": "owner,collaborator", "per_page": per_page,
                "sort": "updated", "page": page,
            })
            if resp.status_code != 200:
                raise ProviderError(f"仓库列表获取失败（HTTP {resp.status_code}）")
            batch = resp.json()
            if not isinstance(batch, list):
                raise ProviderError("仓库列表响应格式无效")
            items.extend(batch)
            if len(batch) < per_page or 'rel="next"' not in resp.headers.get("link", ""):
                break
            page += 1
        q = query.strip().lower()
        return [self._to_remote(r) for r in items if not q or q in (r.get("full_name") or "").lower()]

    async def get_remote_repo(self, full_name: str) -> RemoteRepo:
        resp = await self._get(f"/repos/{full_name}")
        if resp.status_code == 404:
            raise ProviderError(f"仓库不存在或无权访问：{full_name}")
        if resp.status_code != 200:
            raise ProviderError(f"仓库信息获取失败（HTTP {resp.status_code}）")
        return self._to_remote(resp.json())

    async def list_branches(self, full_name: str, limit: int = 50) -> list[str]:
        resp = await self._get(f"/repos/{full_name}/branches", params={"per_page": limit})
        if resp.status_code != 200:
            raise ProviderError(f"分支列表获取失败（HTTP {resp.status_code}）")
        return [b.get("name", "") for b in resp.json() if b.get("name")]

    def _to_remote(self, r: dict) -> RemoteRepo:
        return RemoteRepo(
            provider_repo_id=str(r.get("id", "")),
            full_name=r.get("full_name") or r.get("name") or "",
            web_url=r.get("html_url", ""),
            description=r.get("description") or "",
            default_branch=r.get("default_branch") or "main",
            visibility=_visibility_github(r),
        )


class GitlabProvider:
    """GitLab SaaS 与自建实例共用实现（base_url 区分，token = Personal/Project Access Token）。"""

    provider = "gitlab"

    def __init__(self, token: str, base_url: str = ""):
        self.token = token
        self.api_base = (base_url.rstrip("/") or GITLAB_API).rstrip("/api/v4")
        if not self.api_base.startswith("http"):
            raise ProviderError("自建 GitLab 需要提供有效的 base_url（如 https://gitlab.example.com）")

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.token}

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        url = f"{self.api_base}/api/v4{path}"
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as c:
            resp = await c.get(url, headers=self._headers(), params=params)
        if resp.status_code in (401, 403):
            raise ProviderError("GitLab 认证失败：token 无效或权限不足")
        return resp

    async def verify(self) -> str:
        resp = await self._get("/user")
        if resp.status_code != 200:
            raise ProviderError(f"GitLab 连接失败（HTTP {resp.status_code}）")
        return resp.json().get("username", "")

    async def list_remote_repos(self, query: str = "", limit: int = 500) -> list[RemoteRepo]:
        items: list[dict] = []
        page = 1
        while len(items) < limit:
            params: dict = {"membership": "true", "per_page": min(100, limit - len(items)), "order_by": "updated_at", "page": page}
            if query.strip():
                params["search"] = query.strip()
            resp = await self._get("/projects", params=params)
            if resp.status_code != 200:
                raise ProviderError(f"仓库列表获取失败（HTTP {resp.status_code}）")
            batch = resp.json()
            if not isinstance(batch, list):
                raise ProviderError("仓库列表响应格式无效")
            items.extend(batch)
            if not resp.headers.get("x-next-page") or not batch:
                break
            page += 1
        return [self._to_remote(r) for r in items]

    async def get_remote_repo(self, full_name: str) -> RemoteRepo:
        resp = await self._get(f"/projects/{_gitlab_project_key(full_name)}")
        if resp.status_code == 404:
            raise ProviderError(f"仓库不存在或无权访问：{full_name}")
        if resp.status_code != 200:
            raise ProviderError(f"仓库信息获取失败（HTTP {resp.status_code}）")
        return self._to_remote(resp.json())

    async def list_branches(self, full_name: str, limit: int = 50) -> list[str]:
        resp = await self._get(
            f"/projects/{_gitlab_project_key(full_name)}/repository/branches", params={"per_page": limit}
        )
        if resp.status_code != 200:
            raise ProviderError(f"分支列表获取失败（HTTP {resp.status_code}）")
        return [b.get("name", "") for b in resp.json() if b.get("name")]

    def _to_remote(self, r: dict) -> RemoteRepo:
        return RemoteRepo(
            provider_repo_id=str(r.get("id", "")),
            full_name=r.get("path_with_namespace") or r.get("path") or "",
            web_url=r.get("web_url", ""),
            description=r.get("description") or "",
            default_branch=r.get("default_branch") or "main",
            visibility=r.get("visibility") or "private",
        )


def get_provider(provider: str, token: str, base_url: str = "") -> GithubProvider | GitlabProvider:
    if provider == "github":
        return GithubProvider(token, base_url)
    if provider == "gitlab":
        return GitlabProvider(token, base_url)
    raise ProviderError(f"不支持的代码托管平台：{provider}")


def token_hint_of(token: str) -> str:
    """展示用 hint：固定前缀 + 末 4 位，不暴露原文。"""
    if not token:
        return ""
    return f"****{token[-4:]}" if len(token) > 4 else "****"


def now_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime())
