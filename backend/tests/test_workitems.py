"""工作项与流程实例测试：列表 / 详情 / 新建（发起流程）/ 去重 / 未绑定模板拦截。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _start_values(title: str) -> dict:
    return {"title": title, "description": "pytest 发起的流程", "priority": "P2", "labels": ["pytest"]}


@pytest.fixture(scope="session")
def _created_project(client: TestClient, leader_headers: dict) -> str:
    """session 级：创建一个绑定 tpl-req v3 且有 n2 处理人的项目。"""
    code = f"WF{uuid.uuid4().hex[:6]}"
    payload = {
        "name": f"工作流测试项目-{code}", "code": code, "status": "active", "desc": "workflow e2e",
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


class TestWorkItemList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/work-items")
        assert r.status_code == 401

    def test_list(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/work-items", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 1

    def test_list_filter_status(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/work-items", headers=leader_headers, params={"status": "closed"})
        data = r.json()["data"]
        assert all(w["status"] == "closed" for w in data["items"])

    def test_list_filter_priority(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/work-items", headers=leader_headers, params={"priority": "P0"})
        data = r.json()["data"]
        assert all(w["priority"] == "P0" for w in data["items"])


class TestWorkItemDetail:
    def test_detail(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/work-items/REQ-2026-0241", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["item"]["title"].startswith("订单中心")

    def test_detail_not_found(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/work-items/NOPE-0000", headers=leader_headers)
        assert r.status_code == 404
        assert r.json()["code"] == 40401


class TestWorkItemCreate:
    def test_create_flow(self, client: TestClient, leader_headers: dict, pm_token: str, _created_project: str):
        """新建工作项：发起流程 → 起始节点自动完成并直接流转到下一节点。"""
        title = _uniq("需求")
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req", "start_values": _start_values(title),
        })
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["item"]["status"] == "in_progress"
        # 创建即流转：当前节点为下一节点（需求分析），起始节点已自动完成
        assert data["instance"]["current_node"]["label"] == "需求分析"
        wi_id = data["item"]["id"]
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=leader_headers).json()["data"]
        assert len(detail["tasks"]) >= 2, "应包含起始节点(completed) + 下一节点(待处理)"
        assert detail["tasks"][0]["status"] == "completed", "起始节点任务应自动完成"
        assert any(t["status"] != "completed" for t in detail["tasks"]), "下一节点应有待处理任务"
        # 详情含流程实例与起始表单值
        assert detail["instance"]["templateId"] == "tpl-req"
        assert detail["instance"]["version"].startswith("v")
        assert "title" in detail["startValues"]
        # 发起人只收到创建通知；新待办必须投递给下一节点处理人，不能错发给发起人。
        ntf = client.get("/api/v1/notifications?page_size=100", headers=leader_headers).json()["data"]["items"]
        mine = [n for n in ntf if n.get("wiId") == wi_id]
        assert mine, "发起工作项应生成站内通知"
        assert all(n.get("wiId") == wi_id for n in mine), "通知应携带工作项 ID"
        assert not [n for n in mine if n["kind"] == "arrive"]
        assignee_notifications = client.get(
            "/api/v1/notifications?page_size=100", headers=auth_headers(pm_token),
        ).json()["data"]["items"]
        arrive = [n for n in assignee_notifications if n.get("wiId") == wi_id and n["kind"] == "arrive"]
        assert arrive and arrive[0].get("taskId"), "新待办任务应通知实际处理人并携带任务 ID"

    def test_create_duplicate_title_allowed(self, client: TestClient, leader_headers: dict, _created_project: str):
        """标题不做全局唯一校验：重复标题可创建多个工作项。"""
        title = _uniq("重复标题需求")
        payload = {"project_id": _created_project, "template_id": "tpl-req", "start_values": _start_values(title)}
        assert client.post("/api/v1/work-items", headers=leader_headers, json=payload).status_code == 200
        r = client.post("/api/v1/work-items", headers=leader_headers, json=payload)
        assert r.status_code == 200, f"重复标题应可创建: {r.status_code} {r.text}"
        assert r.json()["data"]["item"]["title"] == title

    def test_create_priority_normalization(self, client: TestClient, leader_headers: dict, _created_project: str):
        """priority 归一化：小写 p0 → DB 枚举 P0；非法值回落 P2（回归：曾因小写值插入枚举列报 500）。"""
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req",
            "start_values": {**_start_values(_uniq("小写优先级")), "priority": "p0"},
        })
        assert r.status_code == 200, f"小写 priority 不应报内部错误: {r.status_code} {r.text}"
        assert r.json()["data"]["item"]["priority"] == "P0"
        r2 = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req",
            "start_values": {**_start_values(_uniq("非法优先级")), "priority": "urgent"},
        })
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["item"]["priority"] == "P2"

    def test_create_without_title(self, client: TestClient, leader_headers: dict, _created_project: str):
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req", "start_values": {"description": "no title"},
        })
        assert r.status_code == 400
        assert r.json()["code"] == 40001

    def test_create_unbound_template(self, client: TestClient, leader_headers: dict):
        """项目未绑定模板 → 40301 拒绝发起（HTTP 状态受 BUG-A 影响为 400）。"""
        code = f"UB{uuid.uuid4().hex[:6]}"
        pid = client.post("/api/v1/projects", headers=leader_headers, json={
            "name": f"无绑定项目-{code}", "code": code, "status": "active",
            "desc": "no bindings", "manager": "李婷", "template_bindings": [],
        }).json()["data"]["item"]["id"]
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": pid, "template_id": "tpl-req", "start_values": _start_values(_uniq("无绑定")),
        })
        assert r.json()["code"] == 40301
        assert r.status_code == 403  # 契约 403

    def test_create_project_not_found(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": "ghost", "template_id": "tpl-req", "start_values": _start_values(_uniq("x")),
        })
        assert r.status_code == 404

    def test_create_template_not_found(self, client: TestClient, leader_headers: dict, _created_project: str):
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-ghost", "start_values": _start_values(_uniq("x")),
        })
        assert r.status_code == 404


class TestCreatePermission:
    """新建工作项权限：仅第一个节点的候选处理人（或管理员）可创建。"""

    def test_create_requires_start_node_handler(self, client: TestClient, leader_headers: dict, org_headers: dict):
        code = f"CP{uuid.uuid4().hex[:6]}"
        pid = client.post("/api/v1/projects", headers=org_headers, json={
            "name": f"权限项目-{code}", "code": code, "status": "active",
            "template_bindings": [{
                "template_id": "tpl-req", "version": "v3", "status": "active",
                "assignments": [{"node_id": "n1", "node_label": "需求提交", "users": ["u9"], "roles": []}],
            }],
        }).json()["data"]["item"]["id"]
        # 李婷不是 n1 处理人 → 403
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("无权限创建"), "description": "x"},
        })
        assert r.status_code == 403, f"非处理人应 403: {r.status_code} {r.text}"
        # 吴凡(u9) 是 n1 处理人 → 200 且创建后直接流转到下一节点
        wu_token = client.post("/api/v1/auth/login", json={"account": "wufan", "password": "Demo@1234"}).json()["data"]["token"]
        wu_headers = auth_headers(wu_token)
        r2 = client.post("/api/v1/work-items", headers=wu_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("有权限创建"), "description": "x"},
        })
        assert r2.status_code == 200, f"处理人应可创建: {r2.status_code} {r2.text}"
        wi_id = r2.json()["data"]["item"]["id"]
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=wu_headers).json()["data"]
        assert detail["tasks"][0]["status"] == "completed"
        assert any(t["status"] != "completed" for t in detail["tasks"])


class TestProjectStatusGate:
    """发起流程仅限 active 项目：draft/paused/cancelled/archived 一律 403。"""

    def _mk(self, client: TestClient, org_headers: dict, status: str) -> str:
        code = f"PS{uuid.uuid4().hex[:6]}"
        r = client.post("/api/v1/projects", headers=org_headers, json={
            "name": f"状态门禁-{code}", "code": code, "status": status,
            "template_bindings": [{"template_id": "tpl-req", "version": "v3", "status": "active"}],
        })
        assert r.status_code == 200, r.text
        return r.json()["data"]["item"]["id"]

    def test_draft_project_rejected(self, client: TestClient, org_headers: dict):
        pid = self._mk(client, org_headers, "draft")
        r = client.post("/api/v1/work-items", headers=org_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("草稿项目"), "description": "x"},
        })
        assert r.status_code == 403
        assert "草稿" in r.json()["message"]

    def test_paused_project_rejected(self, client: TestClient, org_headers: dict):
        pid = self._mk(client, org_headers, "paused")
        r = client.post("/api/v1/work-items", headers=org_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("暂停项目"), "description": "x"},
        })
        assert r.status_code == 403
        assert "暂停" in r.json()["message"]

    def test_cancelled_project_rejected(self, client: TestClient, org_headers: dict):
        pid = self._mk(client, org_headers, "cancelled")
        r = client.post("/api/v1/work-items", headers=org_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("取消项目"), "description": "x"},
        })
        assert r.status_code == 403
        assert "取消" in r.json()["message"]


class TestTaskAppend:
    """前序节点补充信息：仅原处理人/管理员可给已完成节点按 schema 追加，追加后通知当前节点处理人。"""

    def _mk(self, client: TestClient, org_headers: dict) -> tuple[str, str, dict, dict]:
        """建项目（n1→张伟、n2→吴凡）→ 张伟创建（自动流转 n2）→ 返回 (wid, n2_task_id, 张伟headers, 吴凡headers)."""
        code = f"TA{uuid.uuid4().hex[:6]}"
        pid = client.post("/api/v1/projects", headers=org_headers, json={
            "name": f"追加测试-{code}", "code": code, "status": "active",
            "template_bindings": [{
                "template_id": "tpl-req", "version": "v3", "status": "active",
                "assignments": [
                    {"node_id": "n1", "node_label": "需求提交", "users": ["u1"], "roles": []},
                    {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": []},
                ],
            }],
        }).json()["data"]["item"]["id"]
        H_zw = auth_headers(client.post("/api/v1/auth/login", json={"account": "zhang.wei", "password": "Demo@1234"}).json()["data"]["token"])
        r = client.post("/api/v1/work-items", headers=H_zw, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": _start_values(_uniq("追加链路")),
        })
        assert r.status_code == 200, r.text
        wid = r.json()["data"]["item"]["id"]
        detail = client.get(f"/api/v1/work-items/{wid}", headers=H_zw).json()["data"]
        n2 = next(t for t in detail["tasks"] if t["status"] != "completed")
        H_wf = auth_headers(client.post("/api/v1/auth/login", json={"account": "wufan", "password": "Demo@1234"}).json()["data"]["token"])
        return wid, n2["id"], H_zw, H_wf

    def test_original_handler_can_append(self, client: TestClient, org_headers: dict):
        wid, n2_id, H_zw, H_wf = self._mk(client, org_headers)
        start_task = client.get(f"/api/v1/work-items/{wid}", headers=H_zw).json()["data"]["tasks"][0]
        r = client.post(f"/api/v1/tasks/{start_task['id']}/appends", headers=H_zw,
                        json={"values": {"title": "补充链路标题", "description": "补充需求背景", "priority": "P1"}})
        assert r.status_code == 200, r.text
        assert "已补充" in r.json()["message"]
        # 当前节点处理人（吴凡）的任务详情：起始块带 appends
        d = client.get(f"/api/v1/tasks/{n2_id}", headers=H_wf).json()["data"]
        seg = next(u for u in d["upstream"] if u["task_id"] == start_task["id"])
        assert len(seg["appends"]) == 1
        assert seg["appends"][0]["values"]["description"] == "补充需求背景"
        assert seg["appends"][0]["appender"] == "张伟"
        # 吴凡收到站内通知
        ntf = client.get("/api/v1/notifications?page_size=50", headers=H_wf).json()["data"]["items"]
        assert any(n["title"] == "节点信息已补充" and n.get("wiId") == wid for n in ntf)

    def test_non_handler_rejected(self, client: TestClient, org_headers: dict):
        wid, _, H_zw, _ = self._mk(client, org_headers)
        start_task = client.get(f"/api/v1/work-items/{wid}", headers=H_zw).json()["data"]["tasks"][0]
        H_sl = auth_headers(client.post("/api/v1/auth/login", json={"account": "sunlin", "password": "Demo@1234"}).json()["data"]["token"])
        r = client.post(f"/api/v1/tasks/{start_task['id']}/appends", headers=H_sl, json={"values": {"title": "x"}})
        assert r.status_code == 403
        assert "处理人" in r.json()["message"]

    def test_unfinished_node_rejected(self, client: TestClient, org_headers: dict):
        wid, n2_id, H_zw, H_wf = self._mk(client, org_headers)
        r = client.post(f"/api/v1/tasks/{n2_id}/appends", headers=H_wf, json={"values": {"conclusion": "x"}})
        assert r.status_code == 409
        assert "尚未完成" in r.json()["message"]

    def test_required_field_validation(self, client: TestClient, org_headers: dict):
        wid, _, H_zw, _ = self._mk(client, org_headers)
        start_task = client.get(f"/api/v1/work-items/{wid}", headers=H_zw).json()["data"]["tasks"][0]
        r = client.post(f"/api/v1/tasks/{start_task['id']}/appends", headers=H_zw, json={"values": {"description": "缺标题"}})
        assert r.status_code == 400
        assert "必填" in r.json()["message"]


class TestWorkItemStop:
    def test_stop_flow(self, client: TestClient, leader_headers: dict, org_headers: dict, _created_project: str):
        """手动停止：权限拦截 → 停止后实例/任务/工作项全部终结 → 重复停止 409。"""
        title = _uniq("待停止需求")
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": _created_project, "template_id": "tpl-req", "start_values": _start_values(title),
        })
        assert r.status_code == 200
        wi_id = r.json()["data"]["item"]["id"]
        # leader 无 workflow_instance:cancel → 403
        denied = client.post(f"/api/v1/work-items/{wi_id}/stop", headers=leader_headers)
        assert denied.status_code == 403, f"无权限应被拒绝: {denied.status_code} {denied.text}"
        # 组织管理员（有 workflow_instance:cancel）停止成功
        stopped = client.post(f"/api/v1/work-items/{wi_id}/stop", headers=org_headers)
        assert stopped.status_code == 200, f"停止失败: {stopped.status_code} {stopped.text}"
        assert stopped.json()["data"]["item"]["status"] == "cancelled"
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=leader_headers).json()["data"]
        assert detail["instance"]["state"] == "cancelled", "流程实例应置为 cancelled"
        assert all(t["status"] in ("completed", "cancelled") for t in detail["tasks"]), "所有任务应终结"
        assert any(t["status"] == "cancelled" for t in detail["tasks"]), "待处理任务应被取消"
        # 重复停止 → 409
        again = client.post(f"/api/v1/work-items/{wi_id}/stop", headers=org_headers)
        assert again.status_code == 409, f"重复停止应 409: {again.status_code} {again.text}"

    def test_stop_not_found(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/work-items/NOPE-0000/stop", headers=org_headers)
        assert r.status_code == 404


class TestWorkItemDelete:
    def test_delete_cancelled_work_item_cascades_tasks(self, client: TestClient, org_headers: dict, _created_project: str):
        """取消后删除：工作项+实例+全部任务硬删；未取消的工作项拒绝删除。"""
        title = _uniq("待删除需求")
        r = client.post("/api/v1/work-items", headers=org_headers, json={
            "project_id": _created_project, "template_id": "tpl-req", "start_values": _start_values(title),
        })
        assert r.status_code == 200
        wi_id = r.json()["data"]["item"]["id"]
        task_ids = [t["id"] for t in client.get(f"/api/v1/work-items/{wi_id}", headers=org_headers).json()["data"]["tasks"]]
        assert task_ids, "前置：工作项应有任务"

        # 未取消（running）→ 409 拒绝
        denied = client.delete(f"/api/v1/work-items/{wi_id}", headers=org_headers)
        assert denied.status_code == 409, denied.text

        # 停止（cancelled）后删除成功
        assert client.post(f"/api/v1/work-items/{wi_id}/stop", headers=org_headers).status_code == 200
        r2 = client.delete(f"/api/v1/work-items/{wi_id}", headers=org_headers)
        assert r2.status_code == 200, r2.text
        # 工作项消失，任务一并级联删除
        assert client.get(f"/api/v1/work-items/{wi_id}", headers=org_headers).status_code == 404
        for tid in task_ids:
            assert client.get(f"/api/v1/tasks/{tid}", headers=org_headers).status_code == 404, f"任务 {tid} 应级联删除"
        # 列表中不再出现
        listed = client.get(f"/api/v1/work-items?q={title}", headers=org_headers).json()["data"]["items"]
        assert not [w for w in listed if w["id"] == wi_id]

    def test_delete_requires_perm(self, client: TestClient, leader_headers: dict, org_headers: dict, _created_project: str):
        """无 workflow_instance:cancel 权限 → 403。"""
        wi_id = client.post("/api/v1/work-items", headers=org_headers, json={
            "project_id": _created_project, "template_id": "tpl-req", "start_values": _start_values(_uniq("删除权限")),
        }).json()["data"]["item"]["id"]
        assert client.post(f"/api/v1/work-items/{wi_id}/stop", headers=org_headers).status_code == 200
        r = client.delete(f"/api/v1/work-items/{wi_id}", headers=leader_headers)
        assert r.status_code == 403

    def test_delete_not_found(self, client: TestClient, org_headers: dict):
        r = client.delete("/api/v1/work-items/NOPE-0000", headers=org_headers)
        assert r.status_code == 404
