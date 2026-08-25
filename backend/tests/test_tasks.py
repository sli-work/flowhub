"""任务测试：列表 / 详情 / 候选处理人 / 认领 / 转办 / 退回 / 提交流转 / 幂等保护。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def _created_project(client: TestClient, leader_headers: dict) -> str:
    code = f"TK{uuid.uuid4().hex[:6]}"
    payload = {
        "name": f"任务测试项目-{code}", "code": code, "status": "active", "desc": "task e2e",
        "manager": "李婷",
        "template_bindings": [
            {"template_id": "tpl-req", "version": "v3", "status": "active",
             "assignments": [
                 {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": ["product_manager"]},
             ]},
        ],
    }
    r = client.post("/api/v1/projects", headers=leader_headers, json=payload)
    assert r.status_code == 200
    return r.json()["data"]["item"]["id"]


@pytest.fixture(scope="session")
def _fresh_task(client: TestClient, leader_headers: dict, _created_project: str) -> str:
    """session 级：创建一个新工作项，返回第一个待处理任务 id（起始节点已自动完成并流转）。"""
    title = _uniq("任务流程")
    r = client.post("/api/v1/work-items", headers=leader_headers, json={
        "project_id": _created_project, "template_id": "tpl-req",
        "start_values": {"title": title, "description": "e2e", "priority": "P2"},
    })
    assert r.status_code == 200
    wi_id = r.json()["data"]["item"]["id"]
    detail = client.get(f"/api/v1/work-items/{wi_id}", headers=leader_headers).json()["data"]
    return next(t["id"] for t in detail["tasks"] if t["status"] != "completed")


class TestTaskList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/tasks")
        assert r.status_code == 401

    def test_list(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/tasks", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 1

    def test_list_filter_status(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/tasks", headers=leader_headers, params={"status": "in_progress"})
        data = r.json()["data"]
        assert all(t["status"] == "in_progress" for t in data["items"])


class TestTaskDetail:
    def test_detail(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/tasks/T-2026-0912", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["task"]["node"] == "测试"
        assert "suggestions" in data  # Agent 建议列表

    def test_detail_not_found(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/tasks/T-GHOST", headers=leader_headers)
        assert r.status_code == 404


class TestCandidates:
    def test_candidates(self, client: TestClient, leader_headers: dict):
        """T-2026-0912 项目「订单中心」在 seed 无同名项目绑定 → 空候选可接受，但接口正常。"""
        r = client.get("/api/v1/tasks/T-2026-0912/candidates", headers=leader_headers)
        assert r.status_code == 200
        assert "users" in r.json()["data"]

    def test_candidates_resolved(self, client: TestClient, leader_headers: dict, _created_project: str):
        """新项目 n2 绑定 u9+product_manager → 候选解析应包含吴凡。"""
        title = _uniq("候选解析")
        wi = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req",
            "start_values": {"title": title, "description": "x"},
        }).json()["data"]["item"]
        # 推进到 n2：起始任务 submit → n2 新任务 → 查候选
        detail = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]
        start_task = detail["tasks"][0]["id"]
        client.post(f"/api/v1/tasks/{start_task}/actions", headers=leader_headers,
                    json={"action": "submit", "node_id": "n1", "form_values": {"title": title}})
        n2_tasks = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]["tasks"]
        n2_task = next(t for t in n2_tasks if t["node"] == "需求分析")
        r = client.get(f"/api/v1/tasks/{n2_task['id']}/candidates", headers=leader_headers)
        assert r.status_code == 200
        names = {u["name"] for u in r.json()["data"]["users"]}
        assert "吴凡" in names


class TestTaskActions:
    def test_claim(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "claim"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["status"] == "accepted"

    def test_claim_twice_conflict(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        """先认领一次，再认领 → 40902 重复认领。"""
        client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers, json={"action": "claim"})
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers, json={"action": "claim"})
        assert r.status_code == 409
        assert r.json()["code"] == 40902

    def test_transfer(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "transfer", "to_user_id": "u2"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["assignee"] == "李婷"

    def test_transfer_without_target(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "transfer"})
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_transfer_unknown_user(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "transfer", "to_user_id": "ghost"})
        assert r.status_code == 404

    def test_return(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "return", "to_node_id": "n1"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["node"] == "n1"

    def test_return_without_node(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "return"})
        assert r.status_code == 400

    def test_request_info(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "request_info"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["status"] == "waiting_for_information"

    def test_unknown_action(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "fly_to_moon"})
        assert r.status_code == 400
        assert "未知动作" in r.json()["message"]

    def test_action_on_unknown_task(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/tasks/T-GHOST/actions", headers=leader_headers,
                        json={"action": "claim"})
        assert r.status_code == 404

    def test_task_not_found_idempotent_guard(self, client: TestClient, leader_headers: dict, _fresh_task: str):
        """完成后重复 submit → 409 幂等保护。"""
        client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                    json={"action": "submit", "node_id": "n1", "form_values": {}})
        r = client.post(f"/api/v1/tasks/{_fresh_task}/actions", headers=leader_headers,
                        json={"action": "submit", "node_id": "n1", "form_values": {}})
        assert r.status_code == 409
        assert r.json()["code"] == 40902


class TestTaskSubmitAdvance:
    def test_create_auto_advances_to_next_node(self, client: TestClient, leader_headers: dict, _created_project: str):
        """创建工作项后起始节点自动完成并直接流转到 n2（需求分析，吴凡），无需人工确认起始任务。"""
        title = _uniq("推进流程")
        wi = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req",
            "start_values": {"title": title, "description": "x"},
        }).json()["data"]["item"]
        detail = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]
        # 起始任务已自动完成（创建即流转，无需人工点提交）
        start = detail["tasks"][0]
        assert start["status"] == "completed", "起始节点任务应自动完成"
        # 下一节点任务已生成并分配
        n2 = next(t for t in detail["tasks"] if t["node"] == "需求分析")
        assert n2["assignee"] == "吴凡"
        assert n2["status"] != "completed"
        # 提交 n2 → 继续流转到下一节点
        r = client.post(f"/api/v1/tasks/{n2['id']}/actions", headers=leader_headers,
                        json={"action": "submit", "form_values": {"需求分析": "ok"}})
        assert r.status_code == 200
        assert r.json()["data"]["next_node"]["label"] not in (None, "")
        tasks = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]["tasks"]
        assert len(tasks) == 3

    def test_submit_to_end_closes_workitem(self, client: TestClient, leader_headers: dict, _created_project: str):
        """一路提交到结束节点 → 工作项 closed。"""
        title = _uniq("闭环流程")
        wi = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req",
            "start_values": {"title": title, "description": "x"},
        }).json()["data"]["item"]
        # 无画布配置的模板按节点顺序串联：n1→n2→...→n10(end)
        for _ in range(10):
            detail = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]
            open_tasks = [t for t in detail["tasks"] if t["status"] not in ("completed", "cancelled")]
            if not open_tasks:
                break
            tid = open_tasks[0]["id"]
            resp = client.post(f"/api/v1/tasks/{tid}/actions", headers=leader_headers,
                               json={"action": "submit", "node_id": "n1", "form_values": {"title": title}})
            if resp.json()["data"]["next_node"] is None:
                break
        final = client.get(f"/api/v1/work-items/{wi['id']}", headers=leader_headers).json()["data"]["item"]
        assert final["status"] == "closed"
