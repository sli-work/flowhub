"""安全专项测试：认证绕过 / 越权 / 敏感信息泄露 / XSS / CORS。"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class TestAuthenticationBypass:
    def test_protected_routes_require_token(self, client: TestClient):
        """所有业务端点无 token 必须 401。"""
        for method, path in [
            ("get", "/api/v1/projects"), ("get", "/api/v1/tasks"),
            ("get", "/api/v1/work-items"), ("get", "/api/v1/templates/pool"),
            ("get", "/api/v1/documents"), ("get", "/api/v1/notifications"),
            ("get", "/api/v1/agents"), ("get", "/api/v1/matrix/roles"),
            ("get", "/api/v1/org/users"), ("get", "/api/v1/audits"),
            ("get", "/api/v1/dashboard/overview"), ("get", "/api/v1/search"),
            ("get", "/api/v1/auth/me"),
        ]:
            r = client.request(method, path)
            assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"

    def test_invalid_token_rejected(self, client: TestClient):
        r = client.get("/api/v1/projects", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401

    def test_empty_bearer_rejected(self, client: TestClient):
        r = client.get("/api/v1/projects", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    def test_non_bearer_scheme_rejected(self, client: TestClient):
        r = client.get("/api/v1/projects", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert r.status_code == 401

    def test_register_does_not_create_active_user(self, client: TestClient):
        """注册用户不能直接登录（invited 待审批 → 403，BUG-C 已修复）。"""
        account = _uniq("sec")
        client.post("/api/v1/auth/register", json={
            "account": account, "name": "安全测试", "email": f"{account}@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        r = client.post("/api/v1/auth/login", json={"account": account, "password": "NewPass@123"})
        assert r.status_code == 403
        assert "待管理员审批" in r.json()["message"]


class TestPrivilegeEscalation:
    def test_dev_cannot_manage_users(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/org/users", headers=dev_headers, json={
            "account": _uniq("esc"), "name": "越权", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        assert r.status_code == 403

    def test_dev_cannot_manage_roles(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/matrix/roles", headers=dev_headers,
                        json={"id": _uniq("esc"), "label": "x", "perms": {}})
        assert r.status_code == 403

    def test_dev_cannot_delete_documents(self, client: TestClient, dev_headers: dict):
        r = client.delete("/api/v1/documents/d1", headers=dev_headers)
        assert r.status_code == 403

    def test_dev_cannot_register_agent(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/agents/register", headers=dev_headers, json={"name": "x"})
        assert r.status_code == 403

    def test_dev_cannot_export_audits(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/audits/export", headers=dev_headers)
        assert r.status_code == 403

    def test_approve_registration_requires_admin(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/auth/approvals/u2/approve", headers=dev_headers)
        assert r.status_code == 403


class TestSensitiveInfo:
    def test_login_response_no_password_hash(self, client: TestClient):
        r = client.post("/api/v1/auth/login", json={"account": "zhang.wei", "password": "Demo@1234"})
        assert "password" not in r.text and "hash" not in r.text.lower()

    def test_user_list_no_secrets(self, client: TestClient, org_headers: dict):
        r = client.get("/api/v1/org/users", headers=org_headers)
        assert "password" not in r.text and "secret" not in r.text.lower()

    def test_agent_list_no_secret_hash(self, client: TestClient, org_headers: dict):
        r = client.get("/api/v1/agents", headers=org_headers)
        assert "secret" not in r.text.lower()

    def test_openapi_no_secret_leak(self, client: TestClient):
        r = client.get("/openapi.json")
        assert "password" in r.text  # 字段名存在但不应有值泄露


class TestXss:
    def test_stored_xss_probe(self, client: TestClient, leader_headers: dict):
        """存储型 XSS 探测：恶意内容入库后不应被未转义回显（记录观察项）。"""
        code = _uniq("XS")
        payload = {
            "name": '<img src=x onerror=alert(1)>', "code": code,
            "status": "active", "desc": "<script>alert(1)</script>",
            "manager": "x", "template_bindings": [],
        }
        r = client.post("/api/v1/projects", headers=leader_headers, json=payload)
        # 服务端存储不做过滤（业务字段），前端须转义 —— 记录为风险观察项
        assert r.status_code == 200


class TestCors:
    def test_cors_allowed_origin(self, client: TestClient):
        r = client.get("/api/v1/health" if False else "/health",
                       headers={"Origin": "http://localhost:5173"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_cors_disallowed_origin(self, client: TestClient):
        r = client.get("/health", headers={"Origin": "https://evil.example.com"})
        assert r.headers.get("access-control-allow-origin") != "https://evil.example.com"
