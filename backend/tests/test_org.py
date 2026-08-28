"""组织管理测试：概览 / 用户 CRUD / 解锁 / 角色解析 / 同步。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class TestOrgOverview:
    def test_overview_requires_auth(self, client: TestClient, leader_headers: dict):
        """组织概览属于业务数据，必须认证后返回统计。"""
        r = client.get("/api/v1/org/overview", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["users"] >= 1
        assert data["departments"] >= 1


class TestUserList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/org/users")
        assert r.status_code == 401

    def test_list_users(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/org/users", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 12
        accounts = {u["account"] for u in data["items"]}
        assert "zhang.wei" in accounts

    def test_list_filter_by_status(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/org/users", headers=leader_headers, params={"status": "locked"})
        data = r.json()["data"]
        assert data["total"] >= 1
        assert all(u["status"] == "locked" for u in data["items"])

    def test_list_keyword(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/org/users", headers=leader_headers, params={"keyword": "张伟"})
        data = r.json()["data"]
        assert any(u["name"] == "张伟" for u in data["items"])

    def test_list_pagination(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/org/users", headers=leader_headers, params={"page": 1, "page_size": 5})
        data = r.json()["data"]
        assert len(data["items"]) == 5


class TestUserCreate:
    def test_create_user(self, client: TestClient, org_headers: dict):
        account = _uniq("u")
        r = client.post("/api/v1/org/users", headers=org_headers, json={
            "account": account, "name": "新用户", "email": f"{account}@x.dev",
            "dept": "测试部", "role_id": "developer", "skills": ["backend"], "password": "NewPass@123",
        })
        assert r.status_code == 200
        data = r.json()["data"]["user"]
        assert data["status"] == "invited"
        assert data["roles"] == ["developer"]

    def test_create_duplicate(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/org/users", headers=org_headers, json={
            "account": "zhang.wei", "name": "x", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        # 契约 40902→409（BUG-A 修复后）
        assert r.status_code == 409
        assert r.json()["code"] == 40902

    def test_create_no_perm(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/org/users", headers=dev_headers, json={
            "account": _uniq("u"), "name": "x", "email": "x@x.dev",
            "dept": "", "role_id": "developer", "password": "NewPass@123",
        })
        assert r.status_code == 403


class TestUserUpdate:
    def test_update_skills_and_status(self, client: TestClient, org_headers: dict):
        r = client.patch("/api/v1/org/users/u3", headers=org_headers,
                         json={"skills": ["qa", "frontend", "performance"], "status": "active"})
        assert r.status_code == 200
        data = r.json()["data"]["user"]
        assert "performance" in data["skills"]

    def test_update_invalid_role(self, client: TestClient, org_headers: dict):
        r = client.patch("/api/v1/org/users/u3", headers=org_headers, json={"roles": ["ghost_role"]})
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_update_not_found(self, client: TestClient, org_headers: dict):
        r = client.patch("/api/v1/org/users/no_such", headers=org_headers, json={"dept": "x"})
        assert r.status_code == 404


class TestUnlock:
    def test_unlock_locked_user(self, client: TestClient, org_headers: dict):
        """u11 周敏（locked）→ 解锁后 active，且可登录。"""
        r = client.post("/api/v1/org/users/u11/unlock", headers=org_headers)
        assert r.status_code == 200
        r2 = client.post("/api/v1/auth/login", json={"account": "zhoumin", "password": "Demo@1234"})
        assert r2.status_code == 200

    def test_unlock_no_perm(self, client: TestClient, leader_headers: dict):
        """leader 无 organization:user_manage → 403。"""
        r = client.post("/api/v1/org/users/u11/unlock", headers=leader_headers)
        assert r.status_code == 403


class TestUsersByRole:
    def test_by_role_developer(self, client: TestClient):
        r = client.get("/api/v1/org/users/by-role", params={"role": "developer", "active_only": True})
        assert r.status_code == 200
        data = r.json()["data"]
        # developer 角色在职用户（u3/u6/u7，u8 disabled 不含，u11 已解锁）
        assert all(u["status"] == "active" for u in data["users"])
        names = {u["name"] for u in data["users"]}
        assert "孙琳" in names

    def test_by_role_skill_backend(self, client: TestClient):
        """按技能匹配：backend 技能用户。"""
        r = client.get("/api/v1/org/users/by-role", params={"role": "backend", "active_only": True})
        data = r.json()["data"]
        assert len(data["users"]) >= 2


class TestOrgSync:
    def test_sync_requires_perm(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/org/sync", headers=leader_headers)
        assert r.status_code == 403

    def test_sync_no_credentials(self, client: TestClient, org_headers: dict):
        """未配置钉钉/企微同步凭证 → 400 明确提示（避免假同步）。"""
        r = client.post("/api/v1/org/sync", headers=org_headers)
        assert r.status_code == 400
        assert "未配置组织同步凭证" in r.json()["message"]
