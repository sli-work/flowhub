"""基础健康检查与元信息（无认证端点）。"""
import json

import pytest
from fastapi.testclient import TestClient


class TestHealth:
    def test_root(self, client: TestClient):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["name"] == "FlowHub Backend API"
        assert "docs" in r.json() and "health" in r.json()

    def test_health(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_swagger_docs_available(self, client: TestClient):
        r = client.get("/docs")
        assert r.status_code == 200
        assert "swagger" in r.text.lower() or "openapi" in r.text.lower()

    def test_openapi_schema(self, client: TestClient):
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["info"]["title"] == "FlowHub Backend API"
        # 关键路由必须出现在 OpenAPI 契约中
        paths = schema["paths"]
        for path in ("/api/v1/auth/login", "/api/v1/projects", "/api/v1/tasks", "/api/v1/work-items"):
            assert path in paths, f"missing path {path}"
        # 统一响应体 code/message/data 约定应存在于健康接口
        assert "/health" in paths

    def test_mcp_sse_mount(self, client: TestClient):
        """外部 Agent 接入的 MCP SSE 端点已挂载：未带 access key → 401 认证拦截。"""
        r = client.get("/api/v1/mcp/sse")
        assert r.status_code == 401

    def test_mcp_streamable_http_mount(self, client: TestClient, org_headers: dict):
        """Zed 使用 POST 初始化远程 MCP；Streamable HTTP 端点必须存在并要求 access key。"""
        r = client.post("/api/v1/mcp/http/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 401

        access_key = client.post("/api/v1/access-keys", headers=org_headers, json={"name": "Zed MCP"}).json()["data"]["key"]["key"]
        initialized = client.post(
            "/api/v1/mcp/http/",
            headers={"Authorization": f"Bearer {access_key}", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}},
            },
        )
        assert initialized.status_code == 200
        payload = json.loads(next(line.removeprefix("data: ") for line in initialized.text.splitlines() if line.startswith("data: ")))
        assert payload["result"]["serverInfo"]["name"] == "FlowHub"


class TestExternalAgentDownloads:
    def test_download_origin_requires_explicit_public_base_url(self, monkeypatch):
        from types import SimpleNamespace

        from flowhub_api.core import config
        from flowhub_api.core.response import BizError
        from flowhub_api.routes.external_tools import _origin

        monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(public_base_url=""))
        with pytest.raises(BizError) as error:
            _origin()
        assert error.value.status_code == 503

    def test_downloads_require_authenticated_console_session(self, client: TestClient):
        assert client.get("/api/v1/external-tools/mcp-config").status_code == 401
        assert client.get("/api/v1/external-tools/skill-markdown").status_code == 401

    def test_downloads_render_real_mcp_and_skill_artifacts(self, client: TestClient, org_headers: dict):
        mcp = client.get("/api/v1/external-tools/mcp-config", headers=org_headers)
        assert mcp.status_code == 200
        assert "attachment" in mcp.headers["content-disposition"]
        assert "${FLOWHUB_ACCESS_KEY}" in mcp.text
        assert '"url": "http://testserver/api/v1/mcp/http/"' in mcp.text

        created = client.post("/api/v1/access-keys", headers=org_headers, json={"name": "MCP Export"}).json()["data"]["key"]
        configured = client.get(f"/api/v1/external-tools/mcp-config?key_id={created['id']}", headers=org_headers)
        assert configured.status_code == 200
        assert created["key"] in configured.text
        assert "${FLOWHUB_ACCESS_KEY}" not in configured.text

        skill = client.get("/api/v1/external-tools/skill-markdown", headers=org_headers)
        assert skill.status_code == 200
        assert skill.headers["content-type"].startswith("text/markdown")
        assert "FlowHub MCP 操作 Skill" in skill.text
        assert "http://testserver/api/v1/mcp/http/" in skill.text


class TestAccessKeyManagement:
    def test_creates_lists_and_revokes_access_key(self, client: TestClient, org_headers: dict):
        created = client.post("/api/v1/access-keys", headers=org_headers, json={"name": "Claude Desktop"})
        assert created.status_code == 200
        plain = created.json()["data"]["key"]["key"]
        assert plain.startswith("sk_")

        listed = client.get("/api/v1/access-keys", headers=org_headers)
        assert listed.status_code == 200
        item = next(key for key in listed.json()["data"]["items"] if key["name"] == "Claude Desktop")
        assert "key" not in item
        assert client.delete(f"/api/v1/access-keys/{item['id']}", headers=org_headers).status_code == 200
