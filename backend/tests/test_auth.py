"""认证模块测试：登录/注册/改密/审批/me + 异常分支。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN_PASSWORD, auth_headers, login
from flowhub_api.services import enterprise_sso


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class TestLogin:
    def test_login_success(self, client: TestClient):
        """演示用户正常登录，返回 token + 用户信息。"""
        r = client.post("/api/v1/auth/login", json={"account": "zhang.wei", "password": "Demo@1234"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["token"]
        assert body["data"]["user"]["account"] == "zhang.wei"
        assert "roles" in body["data"]["user"]
        assert body["data"]["user"]["theme"] == "dark"

    def test_login_wrong_password(self, client: TestClient):
        r = client.post("/api/v1/auth/login", json={"account": "zhang.wei", "password": "wrong-pass-1"})
        assert r.status_code == 401
        assert r.json()["code"] == 40101

    def test_login_unknown_account(self, client: TestClient):
        r = client.post("/api/v1/auth/login", json={"account": "no_such_user", "password": "Demo@1234"})
        assert r.status_code == 401
        assert r.json()["code"] == 40101

    def test_login_locked_user(self, client: TestClient):
        """u11 周敏 seed 状态为 locked → 423 锁定。"""
        r = client.post("/api/v1/auth/login", json={"account": "zhoumin", "password": "Demo@1234"})
        assert r.status_code == 423
        assert r.json()["code"] == 42301

    def test_login_disabled_user(self, client: TestClient):
        """u8 孙磊 seed 状态为 disabled → 403 停用。"""
        r = client.post("/api/v1/auth/login", json={"account": "sunlei", "password": "Demo@1234"})
        assert r.status_code == 403
        assert r.json()["code"] == 40301

    def test_login_missing_fields(self, client: TestClient):
        r = client.post("/api/v1/auth/login", json={"account": "zhang.wei"})
        assert r.status_code == 422

    def test_login_sql_injection_probe(self, client: TestClient):
        """SQL 注入探测：不应返回 500，也不应绕过认证。"""
        r = client.post("/api/v1/auth/login", json={"account": "' OR '1'='1", "password": "' OR '1'='1"})
        assert r.status_code in (401, 422)

    def test_login_with_email(self, client: TestClient, org_headers: dict):
        """邮箱 + 密码登录：与账号名等价（登录页「用户名/邮箱」）。"""
        account = _uniq("mail")
        email = f"{account}@x.dev"
        reg = client.post("/api/v1/org/users", headers=org_headers, json={
            "account": account, "name": "邮箱登录", "email": email,
            "dept": "测试部", "role_id": "developer", "password": "NewPass@123",
        })
        assert reg.status_code == 200, reg.text
        uid = reg.json()["data"]["user"]["id"]
        assert client.post(f"/api/v1/auth/approvals/{uid}/approve", headers=org_headers).status_code == 200
        # 邮箱可登录（invited → active 后）
        r = client.post("/api/v1/auth/login", json={"account": email, "password": "NewPass@123"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["user"]["account"] == account
        # 账号名登录同样可用（回归）
        assert client.post("/api/v1/auth/login", json={"account": account, "password": "NewPass@123"}).status_code == 200


class TestThemePreference:
    def test_user_can_persist_own_theme_preference(self, client: TestClient, leader_headers: dict):
        updated = client.patch("/api/v1/auth/me/preferences", headers=leader_headers, json={"theme": "light"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["user"]["theme"] == "light"

        current = client.get("/api/v1/auth/me", headers=leader_headers)
        assert current.json()["data"]["user"]["theme"] == "light"

    def test_theme_preference_rejects_unknown_theme(self, client: TestClient, leader_headers: dict):
        response = client.patch("/api/v1/auth/me/preferences", headers=leader_headers, json={"theme": "system"})
        assert response.status_code == 422


class TestEnterpriseSso:
    def test_sso_verifies_state_and_maps_synced_dingtalk_user(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        state = enterprise_sso._state("dingtalk", "/")

        async def external_id(_: str, __: str) -> str:
            return "ding_zw"

        monkeypatch.setattr(enterprise_sso, "external_user_id", external_id)
        response = client.post("/api/v1/auth/sso/verify", json={"provider": "dingtalk", "code": "one-time-code", "state": state})

        assert response.status_code == 200
        assert response.json()["data"]["user"]["account"] == "zhang.wei"

    def test_sso_rejects_tampered_state(self, client: TestClient):
        response = client.post("/api/v1/auth/sso/verify", json={"provider": "wecom", "code": "x", "state": "tampered"})
        assert response.status_code == 401


class TestRegister:
    def test_register_success(self, client: TestClient):
        account = _uniq("reg")
        r = client.post("/api/v1/auth/register", json={
            "account": account, "name": "测试新人", "email": f"{account}@flowhub.dev",
            "dept": "测试部", "role_id": "developer", "password": "NewPass@123",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["user"]["status"] == "invited"  # 注册后待审批

    def test_register_duplicate_account(self, client: TestClient):
        r = client.post("/api/v1/auth/register", json={
            "account": "zhang.wei", "name": "重复", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        assert r.status_code == 409
        assert r.json()["code"] == 40902

    def test_register_invalid_role(self, client: TestClient):
        r = client.post("/api/v1/auth/register", json={
            "account": _uniq("reg"), "name": "无角色", "email": "x@x.dev",
            "dept": "", "role_id": "not_exist_role", "password": "NewPass@123",
        })
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_register_weak_password(self, client: TestClient):
        r = client.post("/api/v1/auth/register", json={
            "account": _uniq("reg"), "name": "弱密码", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "short",
        })
        assert r.status_code == 422

    def test_register_short_account(self, client: TestClient):
        r = client.post("/api/v1/auth/register", json={
            "account": "ab", "name": "短账号", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        assert r.status_code == 422


class TestApprovals:
    def test_approvals_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/auth/approvals")
        assert r.status_code == 401

    def test_approve_registration_requires_perm(self, client: TestClient, dev_token: str):
        """普通开发者无 organization:user_manage，审批应 403。"""
        r = client.post("/api/v1/auth/approvals/u1/approve", headers=auth_headers(dev_token))
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_approve_registration_flow(self, client: TestClient, org_headers: dict):
        """注册 → 管理员审批通过：invited → active。"""
        account = _uniq("appr")
        reg = client.post("/api/v1/auth/register", json={
            "account": account, "name": "待审批用户", "email": f"{account}@x.dev",
            "dept": "测试部", "role_id": "developer", "password": "NewPass@123",
        })
        new_id = reg.json()["data"]["user"]["id"]

        approvals = client.get("/api/v1/auth/approvals", headers=org_headers)
        ids = [i["id"] for i in approvals.json()["data"]["items"]]
        assert new_id in ids

        r = client.post(f"/api/v1/auth/approvals/{new_id}/approve", headers=org_headers)
        assert r.status_code == 200
        # 已处理 → 重复审批应被幂等拦截（契约 40902→409，BUG-A 已修复）
        r2 = client.post(f"/api/v1/auth/approvals/{new_id}/approve", headers=org_headers)
        assert r2.json()["code"] == 40902
        assert r2.status_code == 409

    def test_approve_nonexistent_user(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/auth/approvals/no_such_id/approve", headers=org_headers)
        assert r.status_code == 404
        assert r.json()["code"] == 40401


class TestChangePassword:
    def test_change_password_flow(self, client: TestClient, org_admin_token: str):
        """org_admin 改密：旧密码校验 + 新密码生效。"""
        headers = auth_headers(org_admin_token)
        new_pwd = "Changed@123"
        r = client.post("/api/v1/auth/change-password", headers=headers,
                        json={"old_password": "Demo@1234", "new_password": new_pwd})
        assert r.status_code == 200
        # 新密码可登录
        assert login(client, "liting", new_pwd)
        # 旧密码失效
        r2 = client.post("/api/v1/auth/login", json={"account": "liting", "password": "Demo@1234"})
        assert r2.status_code == 401
        # 恢复原密码（保证其他用例不依赖 liting 密码）
        client.post("/api/v1/auth/change-password", headers=headers,
                    json={"old_password": new_pwd, "new_password": "Demo@1234"})

    def test_change_password_wrong_old(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/auth/change-password", headers=org_headers,
                        json={"old_password": "wrong-old", "new_password": "NewPass@123"})
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_change_password_requires_auth(self, client: TestClient):
        r = client.post("/api/v1/auth/change-password",
                        json={"old_password": "x", "new_password": "NewPass@123"})
        assert r.status_code == 401


class TestMe:
    def test_me(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/auth/me", headers=leader_headers)
        assert r.status_code == 200
        assert r.json()["data"]["user"]["account"] == "zhang.wei"

    def test_me_invalid_token(self, client: TestClient):
        r = client.get("/api/v1/auth/me", headers=auth_headers("invalid.token.value"))
        assert r.status_code == 401

    def test_me_missing_header(self, client: TestClient):
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401


class TestSso:
    def test_sso_not_implemented(self, client: TestClient):
        """SSO 验签请求必须符合当前 code/state 契约。"""
        r = client.post("/api/v1/auth/sso/verify", json={"provider": "dingtalk", "code": "x", "state": "x"})
        assert r.status_code in (400, 401, 404)
