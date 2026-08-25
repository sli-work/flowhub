"""看板与全局搜索测试。"""
import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


class TestDashboard:
    def test_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/dashboard/overview")
        assert r.status_code == 401

    def test_overview(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/dashboard/overview", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        for key in ("kpis", "weekly", "type_split", "dept_load", "timeout_top", "node_heat"):
            assert key in data, f"missing {key}"

        # 趋势必须来自持久化审计事件，而不是固定的占位序列。leader_headers
        # 会产生当日登录审计，因此当前 7 天窗口至少包含一条真实记录。
        assert data["weekly_label"] == "近 7 日活动"
        assert sum(day["v"] for day in data["weekly"]) >= 1
        assert all("delta" not in kpi for kpi in data["kpis"])

    def test_overview_no_perm(self, client: TestClient, dev_headers: dict):
        """developer 无 dashboard:read → 403。"""
        r = client.get("/api/v1/dashboard/overview", headers=dev_headers)
        assert r.status_code == 403
        assert r.json()["code"] == 40302


class TestSearch:
    def test_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/search", params={"q": "订单"})
        assert r.status_code == 401

    def test_search_workitem(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/search", headers=leader_headers, params={"q": "订单", "type": "workitem"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert "items" in data

    def test_search_empty_query(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/search", headers=leader_headers, params={"q": ""})
        assert r.status_code == 200
