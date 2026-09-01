"""通知中心测试：列表 / 已读 / 实时推送 / 重试。"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers
from flowhub_api.services.notification_stream import NotificationStreamHub
from flowhub_api.services import notify
from flowhub_api.models import User


class TestNotificationStreamHub:
    def test_delivers_only_to_matching_subscriber(self):
        async def scenario():
            hub = NotificationStreamHub()
            leader = await hub.subscribe({"张伟", "zhang.wei"})
            developer = await hub.subscribe({"孙琳", "sunlin"})

            await hub.publish({"id": "ntf-live"}, "zhang.wei")

            assert await leader.get() == {"id": "ntf-live"}
            assert developer.empty()
            await hub.unsubscribe(leader)
            await hub.unsubscribe(developer)

        asyncio.run(scenario())


class TestPersonalChannelDelivery:
    def test_uses_synced_external_user_ids_for_app_messages(self, monkeypatch: pytest.MonkeyPatch):
        settings = SimpleNamespace(
            dingtalk_app_key="ding-key", dingtalk_app_secret="ding-secret", dingtalk_agent_id="1001",
            wecom_corp_id="wx-corp", wecom_app_secret="wx-secret", wecom_agent_id="1002",
            dingtalk_webhook="", dingtalk_secret="", wecom_webhook="", smtp_host="", smtp_user="",
        )
        calls: list[tuple[str, str]] = []

        async def send_dingtalk(*args: str) -> bool:
            calls.append(("钉钉", args[3]))
            return True

        async def send_wecom(*args: str) -> bool:
            calls.append(("企微", args[3]))
            return True

        monkeypatch.setattr(notify, "runtime_settings", lambda: settings)
        monkeypatch.setattr(notify, "_send_dingtalk_app", send_dingtalk)
        monkeypatch.setattr(notify, "_send_wecom_app", send_wecom)
        recipient = User(id="u-notify", name="处理人", account="handler", ding_talk="ding-user", wecom="wecom-user")

        results = asyncio.run(notify.deliver_channels("新待办任务", "请处理", recipient))

        assert calls == [("钉钉", "ding-user"), ("企微", "wecom-user")]
        assert results == [{"name": "站内", "ok": True}, {"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}]


class TestNotificationList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/notifications")
        assert r.status_code == 401

    def test_list(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/notifications", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "items" in data and "unread_count" in data

    def test_list_filter_kind(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/notifications", headers=leader_headers, params={"kind": "info"})
        data = r.json()["data"]
        assert all(n["kind"] == "info" for n in data["items"])

    def test_list_pagination_and_stats(self, client: TestClient, leader_headers: dict):
        d1 = client.get("/api/v1/notifications", headers=leader_headers, params={"page": 1, "page_size": 1}).json()["data"]
        assert "stats" in d1 and d1["stats"]["all"] == d1["total"]
        if d1["total"] >= 2:
            d2 = client.get("/api/v1/notifications", headers=leader_headers, params={"page": 2, "page_size": 1}).json()["data"]
            assert d2["items"] and d1["items"][0]["id"] != d2["items"][0]["id"]

    def test_list_filter_failed(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/notifications", headers=leader_headers, params={"failed": "true", "page_size": 100})
        data = r.json()["data"]
        assert all(n["failed"] for n in data["items"])


class TestMarkRead:
    def test_mark_all_read(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/notifications/read", headers=leader_headers, json={})
        assert r.status_code == 200
        data = client.get("/api/v1/notifications", headers=leader_headers).json()["data"]
        assert data["unread_count"] == 0

    def test_mark_read_empty_list(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/notifications/read", headers=leader_headers, json={"ids": []})
        assert r.status_code == 200


class TestRetry:
    def test_retry_success(self, client: TestClient, leader_headers: dict):
        """找到一条未失败且重试 0 次的通知，第一次重试应成功。"""
        items = client.get("/api/v1/notifications", headers=leader_headers).json()["data"]["items"]
        target = next((n for n in items if not n["failed"] and n["retries"] == 0), None)
        if target is None:
            pytest.skip("无可用重试通知")
        r = client.post(f"/api/v1/notifications/{target['id']}/retry", headers=leader_headers)
        assert r.status_code == 200
        assert r.json()["data"]["retries"] == 1
        assert "重试成功" in r.json()["message"]

    def test_retry_not_found(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/notifications/ghost/retry", headers=leader_headers)
        assert r.status_code == 404


class TestChannelTest:
    """渠道连通性测试：真实投递 + 落库为站内通知（通知中心可见）。"""

    def test_channel_test_records_notification(self, client: TestClient, leader_headers: dict):
        """发送测试消息后，通知中心列表出现「通知渠道连通性测试」记录。"""
        before = client.get("/api/v1/notifications?page_size=100", headers=leader_headers).json()["data"]["items"]
        before_ids = {n["id"] for n in before}
        r = client.post("/api/v1/notifications/channels/test", headers=leader_headers, json={})
        assert r.status_code == 200
        assert r.json()["data"]["results"]  # 至少含站内
        after = client.get("/api/v1/notifications?page_size=100", headers=leader_headers).json()["data"]["items"]
        new = [n for n in after if n["id"] not in before_ids]
        assert len(new) == 1
        assert new[0]["title"] == "通知渠道连通性测试"
        assert new[0]["kind"] == "info"
        assert any(c["name"] == "站内" and c["ok"] for c in new[0]["channels"])
