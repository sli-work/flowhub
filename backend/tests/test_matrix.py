"""权限矩阵测试：角色 CRUD / 防自锁 / 成员分配。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class TestRoleList:
    def test_roles_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/matrix/roles")
        assert r.status_code == 401

    def test_roles_matrix(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/matrix/roles", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        ids = {x["id"] for x in data["items"]}
        assert {"system_admin", "organization_admin", "project_admin", "leader", "developer"} <= ids
        assert data["totalPerms"] == 34
        assert len(data["permMatrix"]) == 34
        # 权限矩阵与角色顺序对齐
        assert len(data["roleOrder"]) == 9


class TestRoleCreate:
    def test_create_role(self, client: TestClient, org_headers: dict):
        rid = _uniq("custom")
        r = client.post("/api/v1/matrix/roles", headers=org_headers, json={
            "id": rid, "label": "自定义角色", "desc": "pytest",
            "perms": {"project:read": True, "task:submit": True},
        })
        assert r.status_code == 200
        role = r.json()["data"]["role"]
        assert role["builtin"] is False
        assert role["perms"]["project:read"] is True

    def test_create_role_copy_from(self, client: TestClient, org_headers: dict):
        rid = _uniq("copy")
        r = client.post("/api/v1/matrix/roles", headers=org_headers, json={
            "id": rid, "label": "复制角色", "copy_from": "developer",
        })
        assert r.status_code == 200
        role = r.json()["data"]["role"]
        assert role["perms"]["task:submit"] is True  # developer 有此权限

    def test_create_role_duplicate_id(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/matrix/roles", headers=org_headers, json={
            "id": "developer", "label": "重复", "perms": {},
        })
        # 契约 40902→409（BUG-A 修复后）
        assert r.status_code == 409
        assert r.json()["code"] == 40902

    def test_create_role_invalid_id_pattern(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/matrix/roles", headers=org_headers, json={
            "id": "Bad ID!", "label": "非法标识", "perms": {},
        })
        assert r.status_code == 422

    def test_create_role_no_perm(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/matrix/roles", headers=leader_headers, json={
            "id": _uniq("x"), "label": "x", "perms": {},
        })
        assert r.status_code == 403


class TestRoleUpdate:
    def test_update_role_perms(self, client: TestClient, org_headers: dict):
        r = client.patch("/api/v1/matrix/roles/project_admin", headers=org_headers,
                         json={"id": "project_admin", "label": "项目管理员", "perms": {"dashboard:read": True}})
        assert r.status_code == 200
        assert r.json()["data"]["role"]["perms"]["dashboard:read"] is True

    def test_update_system_admin_self_lock_protection(self, client: TestClient, org_headers: dict):
        """防自锁：system_admin 移除 organization:user_manage → 403。"""
        r = client.patch("/api/v1/matrix/roles/system_admin", headers=org_headers,
                         json={"id": "system_admin", "label": "系统管理员", "perms": {"organization:user_manage": False}})
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_update_nonexistent_role(self, client: TestClient, org_headers: dict):
        r = client.patch("/api/v1/matrix/roles/ghost", headers=org_headers,
                         json={"id": "ghost", "label": "x", "perms": {}})
        assert r.status_code == 404


class TestRoleDelete:
    def test_delete_custom_role(self, client: TestClient, org_headers: dict):
        rid = _uniq("del")
        client.post("/api/v1/matrix/roles", headers=org_headers, json={"id": rid, "label": "待删", "perms": {}})
        r = client.delete(f"/api/v1/matrix/roles/{rid}", headers=org_headers)
        assert r.status_code == 200

    def test_delete_system_admin_forbidden(self, client: TestClient, org_headers: dict):
        r = client.delete("/api/v1/matrix/roles/system_admin", headers=org_headers)
        assert r.status_code == 403

    def test_delete_nonexistent(self, client: TestClient, org_headers: dict):
        r = client.delete("/api/v1/matrix/roles/ghost", headers=org_headers)
        assert r.status_code == 404


class TestRoleMembers:
    def test_set_members(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/matrix/roles/developer/members", headers=org_headers,
                        json={"user_ids": ["u3", "u6"]})
        assert r.status_code == 200
        assert r.json()["data"]["role"]["memberCount"] == 2

    def test_set_members_no_perm(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/matrix/roles/developer/members", headers=leader_headers,
                        json={"user_ids": ["u3"]})
        assert r.status_code == 403
