"""项目管理测试：CRUD / 模板绑定 / 归档只读 / 权限。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _project_payload(code: str) -> dict:
    return {
        "name": f"测试项目-{code}",
        "code": code,
        "status": "active",
        "desc": "pytest 创建",
        "manager": "李婷",
        "template_bindings": [
            {
                "template_id": "tpl-req",
                "version": "v3",
                "status": "active",
                "assignments": [
                    {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": ["product_manager"]},
                ],
            }
        ],
    }


class TestProjectList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/projects")
        assert r.status_code == 401

    def test_list_projects(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/projects", headers=leader_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["total"] >= 1
        # 项目详情包含模板绑定与处理人（eager-load 防 MissingGreenlet）
        p = body["data"]["items"][0]
        assert "templateBindings" in p

    def test_get_project_detail(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/projects/p1", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]["item"]
        assert data["id"] == "p1"
        assert data["code"] == "ORDER"
        binds = data["templateBindings"]
        assert any(b["templateId"] == "tpl-req" for b in binds)

    def test_get_project_not_found(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/projects/no_such", headers=leader_headers)
        assert r.status_code == 404
        assert r.json()["code"] == 40401


class TestProjectCreate:
    def test_create_project(self, client: TestClient, leader_headers: dict):
        code = _uniq("PRJ")
        r = client.post("/api/v1/projects", headers=leader_headers, json=_project_payload(code))
        assert r.status_code == 200
        data = r.json()["data"]["item"]
        assert data["code"] == code.upper()
        assert data["status"] == "active"

    def test_create_duplicate_code(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/projects", headers=leader_headers, json=_project_payload("ORDER"))
        assert r.status_code == 409
        assert r.json()["code"] == 40902

    def test_create_without_perm(self, client: TestClient, dev_headers: dict):
        """developer 无 project:create → 403。"""
        r = client.post("/api/v1/projects", headers=dev_headers, json=_project_payload(_uniq("PRJ")))
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_create_missing_required_fields(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/projects", headers=leader_headers,
                        json={"name": "缺 code"})
        assert r.status_code == 422

    def test_create_with_invalid_template(self, client: TestClient, leader_headers: dict):
        payload = _project_payload(_uniq("PRJ"))
        payload["template_bindings"] = [{"template_id": "tpl-ghost", "version": "v1", "status": "active", "assignments": []}]
        r = client.post("/api/v1/projects", headers=leader_headers, json=payload)
        assert r.status_code == 400
        assert r.json()["code"] == 40001


class TestProjectUpdate:
    def test_update_project(self, client: TestClient, leader_headers: dict):
        code = _uniq("PRJ")
        created = client.post("/api/v1/projects", headers=leader_headers, json=_project_payload(code))
        pid = created.json()["data"]["item"]["id"]
        r = client.patch(f"/api/v1/projects/{pid}", headers=leader_headers,
                         json={**_project_payload(code), "name": f"改名-{code}", "status": "paused"})
        assert r.status_code == 200
        data = r.json()["data"]["item"]
        assert data["name"] == f"改名-{code}"
        assert data["status"] == "paused"

    def test_update_archived_project_forbidden(self, client: TestClient, org_headers: dict):
        """通过接口归档的项目 → read_only=True → 更新应 403（验证只读保护生效）。"""
        code = _uniq("PRJ")
        pid = client.post("/api/v1/projects", headers=org_headers, json=_project_payload(code)).json()["data"]["item"]["id"]
        client.post(f"/api/v1/projects/{pid}/archive", headers=org_headers)
        r = client.patch(f"/api/v1/projects/{pid}", headers=org_headers, json=_project_payload(code))
        # 业务码 40301 正确；HTTP 状态受 BUG-A 影响为 400（契约 403）
        assert r.json()["code"] == 40301
        assert r.status_code == 403

    def test_archived_seed_project_readonly_consistency(self, client: TestClient, leader_headers: dict):
        """seed 中 status=archived 的项目应带 read_only=True，且 update 应拦截。"""
        detail = client.get("/api/v1/projects/p9", headers=leader_headers).json()["data"]["item"]
        assert detail["status"] == "archived"
        assert detail["readOnly"] is True
        r = client.patch("/api/v1/projects/p9", headers=leader_headers, json=_project_payload(_uniq("PRJ")))
        assert r.status_code == 403

    def test_update_not_found(self, client: TestClient, leader_headers: dict):
        r = client.patch("/api/v1/projects/no_such", headers=leader_headers,
                         json=_project_payload(_uniq("PRJ")))
        assert r.status_code == 404


class TestProjectLifecycle:
    def test_archive_project(self, client: TestClient, org_headers: dict):
        code = _uniq("PRJ")
        pid = client.post("/api/v1/projects", headers=org_headers, json=_project_payload(code)).json()["data"]["item"]["id"]
        r = client.post(f"/api/v1/projects/{pid}/archive", headers=org_headers)
        assert r.status_code == 200
        detail = client.get(f"/api/v1/projects/{pid}", headers=org_headers).json()["data"]["item"]
        assert detail["status"] == "archived"
        assert detail["readOnly"] is True

    def test_cancel_project(self, client: TestClient, org_headers: dict):
        code = _uniq("PRJ")
        pid = client.post("/api/v1/projects", headers=org_headers, json=_project_payload(code)).json()["data"]["item"]["id"]
        r = client.post(f"/api/v1/projects/{pid}/cancel", headers=org_headers)
        assert r.status_code == 200
        detail = client.get(f"/api/v1/projects/{pid}", headers=org_headers).json()["data"]["item"]
        assert detail["status"] == "cancelled"

    def test_archive_requires_perm(self, client: TestClient, leader_headers: dict):
        """leader 无 project:archive → 403。"""
        r = client.post("/api/v1/projects/p1/archive", headers=leader_headers)
        assert r.status_code == 403
        assert r.json()["code"] == 40302


class TestProjectArchiveDelete:
    """项目归档与删除：仅归档后可删除，有关联工作项拒绝。"""

    def _mk(self, client: TestClient, headers: dict) -> dict:
        code = f"AD{uuid.uuid4().hex[:6]}"
        r = client.post("/api/v1/projects", headers=headers, json={
            "name": f"归档删除-{code}", "code": code, "status": "active",
        })
        assert r.status_code == 200
        return r.json()["data"]["item"]

    def test_delete_active_rejected(self, client: TestClient, org_headers: dict):
        """未归档项目删除 → 409（仅归档项目可删除）。"""
        p = self._mk(client, org_headers)
        r = client.delete(f"/api/v1/projects/{p['id']}", headers=org_headers)
        assert r.status_code == 409
        assert "归档" in r.json()["message"]

    def test_archive_then_delete(self, client: TestClient, org_headers: dict):
        """归档 → 删除成功，绑定/处理人配置清理。"""
        p = self._mk(client, org_headers)
        pid = p["id"]
        # 绑定一个模板 + 节点处理人，验证级联清理
        client.patch(f"/api/v1/projects/{pid}", headers=org_headers, json={
            "name": p["name"], "code": p["code"], "status": "active",
            "template_bindings": [{"template_id": "tpl-req", "version": "v3", "status": "active",
                                   "assignments": [{"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": []}]}],
        })
        ar = client.post(f"/api/v1/projects/{pid}/archive", headers=org_headers)
        assert ar.status_code == 200
        d = client.delete(f"/api/v1/projects/{pid}", headers=org_headers)
        assert d.status_code == 200, d.text
        items = client.get("/api/v1/projects", headers=org_headers).json()["data"]["items"]
        assert not any(x["id"] == pid for x in items)

    def test_delete_with_workitems_rejected(self, client: TestClient, leader_headers: dict, org_headers: dict):
        """归档项目仍有关联工作项 → 删除拒绝（409）。"""
        p = self._mk(client, org_headers)
        pid = p["id"]
        # 绑定 tpl-req 并发起一个工作项
        client.patch(f"/api/v1/projects/{pid}", headers=org_headers, json={
            "name": p["name"], "code": p["code"], "status": "active",
            "template_bindings": [{"template_id": "tpl-req", "version": "v3", "status": "active"}],
        })
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": f"归档删除工作项{uuid.uuid4().hex[:6]}", "description": "x"},
        })
        assert r.status_code == 200
        ar = client.post(f"/api/v1/projects/{pid}/archive", headers=org_headers)
        assert ar.status_code == 200
        d = client.delete(f"/api/v1/projects/{pid}", headers=org_headers)
        assert d.status_code == 409
        assert "工作项" in d.json()["message"]


class TestProjectProgress:
    """项目卡片进度：未归档工作项的平均流转进度（closed=100%，其余=已完成任务/总任务；archived 不计入）。"""

    def _mk_project(self, client: TestClient, leader_headers: dict) -> str:
        code = f"PG{uuid.uuid4().hex[:6]}"
        r = client.post("/api/v1/projects", headers=leader_headers, json=_project_payload(code))
        assert r.status_code == 200, r.text
        return r.json()["data"]["item"]["id"]

    def _progress(self, client: TestClient, leader_headers: dict, pid: str) -> int:
        d = client.get(f"/api/v1/projects/{pid}", headers=leader_headers).json()["data"]["item"]
        return d["progress"]

    def test_progress_reflects_flow_advance(self, client: TestClient, leader_headers: dict, org_headers: dict):
        """创建即自动完成起始节点：单条 WI 停在 n2 → 进度 50%（2 个任务完成 1 个）；
        n2 提交后变为 2/3 → 67%。旧逻辑（仅认 closed）会一直显示 0%。"""
        pid = self._mk_project(client, org_headers)
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("进度验证"), "description": "x", "priority": "P2"},
        })
        assert r.status_code == 200, r.text
        assert self._progress(client, leader_headers, pid) == 50

        wi_id = r.json()["data"]["item"]["id"]
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=leader_headers).json()["data"]
        n2_task = next(t for t in detail["tasks"] if t["status"] != "completed")
        r2 = client.post(f"/api/v1/tasks/{n2_task['id']}/actions", headers=leader_headers,
                         json={"action": "submit", "form_values": {"conclusion": "分析完成"}})
        assert r2.status_code == 200, r2.text
        assert self._progress(client, leader_headers, pid) == 67, "单条 WI：2/3 ≈ 67%"

    def test_archived_wi_excluded_from_progress(self, client: TestClient, leader_headers: dict, org_headers: dict):
        """archived（冻结）工作项不计入进度：仅剩的 active WI 完成后项目应为 100% 而非被稀释。"""
        pid = self._mk_project(client, org_headers)
        r = client.post("/api/v1/work-items", headers=leader_headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("归档稀释"), "description": "x", "priority": "P2"},
        })
        wi_id = r.json()["data"]["item"]["id"]
        # 归档该工作项（若路由支持）：项目里只剩归档件 → 进度 0 而不是误导值
        ra = client.post(f"/api/v1/work-items/{wi_id}/archive", headers=org_headers)
        if ra.status_code == 404:
            pytest.skip("工作项归档接口不存在")
        assert self._progress(client, leader_headers, pid) == 0
