"""基础健康检查与元信息（无认证端点）。"""
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
