"""审计中心测试：分页查询 / 导出（导出本身记审计）/ 权限。"""
import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


class TestAuditList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/audits")
        assert r.status_code == 401

    def test_list_requires_perm(self, client: TestClient, dev_headers: dict):
        """developer 无 audit:read → 403。"""
        r = client.get("/api/v1/audits", headers=dev_headers)
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_list(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/audits", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 1
        assert len(data["items"]) <= 5  # 默认 page_size=5

    def test_list_filter_action(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/audits", headers=leader_headers, params={"action": "login"})
        data = r.json()["data"]
        assert all("login" in a["action"] for a in data["items"])

    def test_list_filter_result(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/audits", headers=leader_headers, params={"result": "success"})
        data = r.json()["data"]
        assert all(a["result"] == "success" for a in data["items"])


class TestAuditExport:
    def test_export_requires_perm(self, client: TestClient, leader_headers: dict):
        """leader 有 audit:read 但无 audit:export → 403。"""
        r = client.post("/api/v1/audits/export", headers=leader_headers)
        assert r.status_code == 403

    def test_export_records_audit(self, client: TestClient, org_headers: dict):
        before = client.get("/api/v1/audits", headers=org_headers).json()["data"]["total"]
        r = client.post("/api/v1/audits/export", headers=org_headers)
        assert r.status_code == 200
        after = client.get("/api/v1/audits", headers=org_headers).json()["data"]["total"]
        assert after == before + 1  # 导出本身新增一条审计
