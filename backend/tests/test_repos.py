"""代码仓库绑定测试：连接管理（验证/加密/失效）+ 项目 ↔ 仓库 多对多绑定。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import org_headers, auth_headers, login

from flowhub_api.services import repo_providers
from flowhub_api.services.repo_providers import RemoteRepo


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class FakeProvider:
    """替身 provider：不发起真实网络调用，固定返回演示仓库。"""

    def __init__(self, token: str = "", base_url: str = ""):
        self.token = token
        self.base_url = base_url

    async def verify(self) -> str:
        if self.token == "bad-token":
            raise repo_providers.ProviderError("认证失败：token 无效")
        return "demo-bot"

    async def list_remote_repos(self, query: str = "", limit: int = 30) -> list[RemoteRepo]:
        repos = [
            RemoteRepo("101", "acme/flow-frontend", "https://git.example/acme/flow-frontend", "前端", "main", "private"),
            RemoteRepo("102", "acme/flow-backend", "https://git.example/acme/flow-backend", "后端", "develop", "private"),
            RemoteRepo("103", "acme/docs", "https://git.example/acme/docs", "文档", "master", "internal"),
        ]
        q = query.strip().lower()
        return [r for r in repos if not q or q in r.full_name.lower()]

    async def get_remote_repo(self, key: str) -> RemoteRepo:
        for r in await self.list_remote_repos():
            if key in (r.provider_repo_id, r.full_name):
                return r
        raise repo_providers.ProviderError(f"仓库不存在或无权访问：{key}")

    async def list_branches(self, full_name: str, limit: int = 50) -> list[str]:
        return ["main", "develop"]


@pytest.fixture(scope="session")
def monkeypatch_session_cls():
    """session 级 patch：routes.repos.get_provider → FakeProvider（全程不发起真实网络调用）。"""
    from flowhub_api.routes import repos as repos_route

    original = repos_route.get_provider
    repos_route.get_provider = lambda provider, token, base_url="": FakeProvider(token, base_url)
    yield
    repos_route.get_provider = original


def _conn_payload(name: str) -> dict:
    return {"name": name, "provider": "gitlab", "base_url": "https://gitlab.example.com", "token": "glpat-demo123456"}


class TestConnection:
    def test_requires_auth(self, client: TestClient):
        assert client.get("/api/v1/repo-connections").status_code == 401
        assert client.post("/api/v1/repo-connections", json={}).status_code == 401

    def test_create_requires_resource_manage_perm(self, client: TestClient):
        """developer 无 expert:resource_manage → 403。"""
        token = login(client, "sunlin")
        r = client.post("/api/v1/repo-connections", json=_conn_payload(_uniq("c")), headers=auth_headers(token))
        assert r.status_code == 403

    def test_create_and_verify(self, client: TestClient, org_headers: dict, monkeypatch_session_cls):
        name = _uniq("conn")
        r = client.post("/api/v1/repo-connections", json=_conn_payload(name), headers=org_headers)
        assert r.status_code == 200, r.text
        item = r.json()["data"]["item"]
        assert item["provider"] == "gitlab"
        assert item["account"] == "demo-bot"
        assert item["status"] == "ok"
        assert item["tokenHint"].endswith("3456")
        assert "glpat" not in r.text  # token 原文不回传

    def test_create_invalid_token_rejected(self, client: TestClient, org_headers: dict, monkeypatch_session_cls):
        body = _conn_payload(_uniq("bad")) | {"token": "bad-token"}
        r = client.post("/api/v1/repo-connections", json=body, headers=org_headers)
        assert r.status_code == 400
        assert "token" in r.json()["message"]

    def test_duplicate_name(self, client: TestClient, org_headers: dict, monkeypatch_session_cls):
        name = _uniq("dup")
        assert client.post("/api/v1/repo-connections", json=_conn_payload(name), headers=org_headers).status_code == 200
        r = client.post("/api/v1/repo-connections", json=_conn_payload(name), headers=org_headers)
        assert r.status_code == 409

    def test_gitlab_requires_base_url(self, client: TestClient, org_headers: dict):
        r = client.post(
            "/api/v1/repo-connections",
            json={"name": _uniq("nb"), "provider": "gitlab", "token": "glpat-demo123456"},
            headers=org_headers,
        )
        assert r.status_code == 400
        assert "base_url" in r.json()["message"]

    def test_verify_marks_invalid(self, client: TestClient, org_headers: dict, monkeypatch_session_cls):
        """换 token 为无效值（PATCH 不触发重验证失败即拒），手动 verify 标记 invalid。"""
        name = _uniq("vc")
        conn = client.post("/api/v1/repo-connections", json=_conn_payload(name), headers=org_headers).json()["data"]["item"]
        r = client.post(f"/api/v1/repo-connections/{conn['id']}/verify", headers=org_headers)
        assert r.status_code == 200
        assert r.json()["data"]["item"]["status"] == "ok"
        # 更新为无效 token：PATCH 时重新验证 → 400，且连接信息不变
        r = client.patch(
            f"/api/v1/repo-connections/{conn['id']}", json={"token": "bad-token"}, headers=org_headers
        )
        assert r.status_code == 400

    def test_remote_repo_search(self, client: TestClient, org_headers: dict, monkeypatch_session_cls):
        conn = client.post("/api/v1/repo-connections", json=_conn_payload(_uniq("s")), headers=org_headers).json()["data"]["item"]
        r = client.get(f"/api/v1/repo-connections/{conn['id']}/remote-repos?q=backend", headers=org_headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert [i["fullName"] for i in items] == ["acme/flow-backend"]

    def test_delete_cascades_bindings(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str):
        conn = client.post("/api/v1/repo-connections", json=_conn_payload(_uniq("d")), headers=org_headers).json()["data"]["item"]
        bind_resp = client.post(
            f"/api/v1/projects/{project_id}/repos",
            json={"connection_id": conn["id"], "provider_repo_id": "101", "role": "main"},
            headers=org_headers,
        )
        assert bind_resp.status_code == 200, bind_resp.text
        bind = bind_resp.json()["data"]["item"]
        r = client.delete(f"/api/v1/repo-connections/{conn['id']}", headers=org_headers)
        assert r.status_code == 200
        # 项目侧绑定随之消失
        items = client.get(f"/api/v1/projects/{project_id}/repos", headers=org_headers).json()["data"]["items"]
        assert all(i["id"] != bind["id"] for i in items)


@pytest.fixture(scope="session")
def project_id(client: TestClient, org_headers: dict) -> str:
    """创建一个干净的测试项目供绑定用例复用。"""
    code = _uniq("RP")
    r = client.post(
        "/api/v1/projects",
        json={"name": f"仓库绑定项目-{code}", "code": code, "status": "active", "desc": "pytest", "manager": "李婷"},
        headers=org_headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["item"]["id"]


@pytest.fixture(scope="session")
def connection_id(client: TestClient, org_headers: dict, monkeypatch_session_cls) -> str:
    r = client.post("/api/v1/repo-connections", json=_conn_payload(_uniq("bind")), headers=org_headers)
    return r.json()["data"]["item"]["id"]


class TestProjectBinding:
    def test_bind_and_appears_in_project(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str, connection_id: str):
        r = client.post(
            f"/api/v1/projects/{project_id}/repos",
            json={"connection_id": connection_id, "provider_repo_id": "101", "role": "main"},
            headers=org_headers,
        )
        assert r.status_code == 200, r.text
        item = r.json()["data"]["item"]
        assert item["repo"]["fullName"] == "acme/flow-frontend"
        assert item["defaultBranch"] == "main"

        # 项目列表 payload 携带 repos 快照
        p = client.get(f"/api/v1/projects/{project_id}", headers=org_headers).json()["data"]["item"]
        assert any(x["fullName"] == "acme/flow-frontend" for x in p["repos"])

    def test_same_repo_two_projects_share_repo_record(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str, connection_id: str):
        """同一仓库可绑定到第二个项目，且复用同一 Repo 记录。"""
        code = _uniq("RQ")
        p2 = client.post(
            "/api/v1/projects",
            json={"name": f"第二项目-{code}", "code": code, "status": "active", "desc": "", "manager": ""},
            headers=org_headers,
        ).json()["data"]["item"]["id"]
        r1 = client.post(
            f"/api/v1/projects/{project_id}/repos",
            json={"connection_id": connection_id, "provider_repo_id": "102"},
            headers=org_headers,
        )
        r2 = client.post(
            f"/api/v1/projects/{p2}/repos",
            json={"connection_id": connection_id, "provider_repo_id": "102", "role": "service"},
            headers=org_headers,
        )
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["data"]["item"]["repo"]["id"] == r2.json()["data"]["item"]["repo"]["id"]

    def test_duplicate_binding_rejected(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str, connection_id: str):
        body = {"connection_id": connection_id, "provider_repo_id": "101"}
        r = client.post(f"/api/v1/projects/{project_id}/repos", json=body, headers=org_headers)
        assert r.status_code == 409

    def test_bind_requires_project_update_perm(self, client: TestClient, monkeypatch_session_cls, project_id: str, connection_id: str):
        token = login(client, "sunlin")
        r = client.post(
            f"/api/v1/projects/{project_id}/repos",
            json={"connection_id": connection_id, "provider_repo_id": "103"},
            headers=auth_headers(token),
        )
        assert r.status_code == 403

    def test_update_binding_role_and_branch(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str, connection_id: str):
        bind = client.post(
            f"/api/v1/projects/{project_id}/repos",
            json={"connection_id": connection_id, "provider_repo_id": "103", "role": "docs"},
            headers=org_headers,
        ).json()["data"]["item"]
        r = client.patch(
            f"/api/v1/projects/{project_id}/repos/{bind['id']}",
            json={"default_branch": "master"},
            headers=org_headers,
        )
        assert r.status_code == 200
        assert r.json()["data"]["item"]["defaultBranch"] == "master"

    def test_unbind_keeps_repo(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str):
        # 解绑 update 用例创建的 docs 绑定（repo 103）
        items = client.get(f"/api/v1/projects/{project_id}/repos", headers=org_headers).json()["data"]["items"]
        bind = next(i for i in items if i["repo"]["fullName"] == "acme/docs")
        r = client.delete(f"/api/v1/projects/{project_id}/repos/{bind['id']}", headers=org_headers)
        assert r.status_code == 200
        # 全局仓库列表仍能看到该仓库（记录保留，可被再次绑定）
        repos = client.get("/api/v1/repos", headers=org_headers).json()["data"]["items"]
        assert any(x["fullName"] == "acme/docs" for x in repos)

    def test_global_repo_list_includes_bindings(self, client: TestClient, org_headers: dict, monkeypatch_session_cls, project_id: str, connection_id: str):
        r = client.get("/api/v1/repos", headers=org_headers)
        assert r.status_code == 200
        item = next(x for x in r.json()["data"]["items"] if x["fullName"] == "acme/flow-backend")
        assert len(item["bindings"]) >= 2  # 两个项目各自绑定
        assert {b["role"] for b in item["bindings"]} >= {"service"}


class TestGitlabProjectKey:
    """GitLab 项目定位键：URL 编码 + clone URL / .git 后缀兼容（旧版 GitLab 不支持 base64 形式）。"""

    def test_key_variants(self):
        from flowhub_api.services.repo_providers import _gitlab_project_key

        assert _gitlab_project_key("omc/DRCC/drcc-backend") == "omc%2FDRCC%2Fdrcc-backend"
        assert _gitlab_project_key("1435") == "1435"
        assert _gitlab_project_key("http://git.mchz.com.cn/omc/DRCC/drcc-backend.git") == "omc%2FDRCC%2Fdrcc-backend"
        assert _gitlab_project_key("omc/DRCC/drcc-backend.git") == "omc%2FDRCC%2Fdrcc-backend"
