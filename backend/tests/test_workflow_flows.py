"""流转流程专项测试：覆盖认领→提交→转办→退回→闭环全链路，以及并发实例、审计通知、边界场景。

所有测试使用函数级项目创建，避免 session 级状态冲突。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _make_project(client, headers, template_id, version, assignments, prefix="FW"):
    code = f"{prefix}{uuid.uuid4().hex[:6]}"
    r = client.post("/api/v1/projects", headers=headers, json={
        "name": f"流转测试-{code}", "code": code, "status": "active",
        "desc": "workflow flow e2e", "manager": "李婷",
        "template_bindings": [
            {"template_id": template_id, "version": version, "status": "active",
             "assignments": assignments},
        ],
    })
    assert r.status_code == 200, f"create project failed: {r.status_code} {r.text}"
    return r.json()["data"]["item"]["id"]


def _create_wi(client, headers, project_id, template_id, title):
    r = client.post("/api/v1/work-items", headers=headers, json={
        "project_id": project_id, "template_id": template_id,
        "start_values": {"title": title, "description": "流转测试"},
    })
    assert r.status_code == 200, f"create_wi failed: {r.status_code} {r.text}"
    return r.json()["data"]["item"]["id"]


def _get_open_tasks(client, headers, wi_id):
    detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
    return [t for t in detail["tasks"] if t["status"] not in ("completed", "cancelled")]


def _submit_task(client, headers, task_id, form_values=None):
    r = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                    json={"action": "submit", "node_id": "n1", "form_values": form_values or {}})
    assert r.status_code == 200, f"submit failed: {r.status_code} {r.text}"
    return r.json()["data"]


# ==================== 1. 认领→提交→转办→认领→提交 完整链路 ====================
class TestClaimSubmitTransferChain:
    def test_full_chain(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        org_headers = auth_headers(org_admin_token)
        pid = _make_project(client, org_headers, "tpl-req", "v3", [
            {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": ["product_manager"]},
        ])
        title = _uniq("协作链")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        open_tasks = _get_open_tasks(client, headers, wi_id)
        assert open_tasks
        task_id = open_tasks[0]["id"]

        # 认领
        r = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers, json={"action": "claim"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["status"] == "accepted"
        assert r.json()["data"]["task"]["assignee"] == "张伟"

        # 提交
        result = _submit_task(client, headers, task_id)
        # 提交成功（无论是否流转到下一节点）
        assert result["task"]["status"] == "completed"

        # 获取新任务并转办
        new_tasks = _get_open_tasks(client, headers, wi_id)
        if new_tasks:
            new_task_id = new_tasks[-1]["id"]
            r = client.post(f"/api/v1/tasks/{new_task_id}/actions", headers=headers,
                            json={"action": "transfer", "to_user_id": "u2"})
            assert r.status_code == 200
            assert r.json()["data"]["task"]["assignee"] == "李婷"
            assert r.json()["data"]["task"]["status"] == "transferred"

            # 李婷认领
            r = client.post(f"/api/v1/tasks/{new_task_id}/actions", headers=org_headers, json={"action": "claim"})
            assert r.status_code == 200
            assert r.json()["data"]["task"]["status"] == "accepted"

            # 李婷提交
            result2 = _submit_task(client, org_headers, new_task_id)
            assert result2["task"]["status"] == "completed"


# ==================== 2. 退回→重新提交 流程 ====================
class TestReturnAndResubmit:
    def test_return_then_resubmit(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("退回重提")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        start_task = _get_open_tasks(client, headers, wi_id)[0]["id"]

        # 提交起始任务
        result = _submit_task(client, headers, start_task)
        assert result["task"]["status"] == "completed"

        # 获取新任务，退回至 n1
        new_tasks = _get_open_tasks(client, headers, wi_id)
        if new_tasks:
            n2_task = new_tasks[-1]["id"]
            r = client.post(f"/api/v1/tasks/{n2_task}/actions", headers=headers,
                            json={"action": "return", "to_node_id": "n1"})
            assert r.status_code == 200
            assert r.json()["data"]["task"]["node"] == "n1"
            assert r.json()["data"]["task"]["status"] == "returned"

            # 退回后仍有待处理任务
            open_tasks = _get_open_tasks(client, headers, wi_id)
            assert len(open_tasks) >= 1


# ==================== 3. 并发工作项实例隔离 ====================
class TestConcurrentInstances:
    def test_two_instances_independent(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title1 = _uniq("实例A")
        title2 = _uniq("实例B")

        wi1 = _create_wi(client, headers, pid, "tpl-req", title1)
        wi2 = _create_wi(client, headers, pid, "tpl-req", title2)

        # 各自推进一步
        t1 = _get_open_tasks(client, headers, wi1)[0]["id"]
        t2 = _get_open_tasks(client, headers, wi2)[0]["id"]
        r1 = _submit_task(client, headers, t1)
        r2 = _submit_task(client, headers, t2)
        assert r1["task"]["status"] == "completed"
        assert r2["task"]["status"] == "completed"

        # 验证各自独立
        d1 = client.get(f"/api/v1/work-items/{wi1}", headers=headers).json()["data"]
        d2 = client.get(f"/api/v1/work-items/{wi2}", headers=headers).json()["data"]
        assert d1["item"]["id"] != d2["item"]["id"]
        # 两个工作项各自有任务
        assert len(d1["tasks"]) >= 1
        assert len(d2["tasks"]) >= 1


# ==================== 4. 闭环流程（工作项 status → closed） ====================
class TestFullClosure:
    def test_end_node_waits_for_assignee_submission(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        org_headers = auth_headers(org_admin_token)
        pid = _make_project(client, org_headers, "tpl-req", "v3", [
            {"node_id": "n10", "node_label": "完成", "users": ["u9"], "roles": []},
        ])
        title = _uniq("结束确认")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)

        for _ in range(12):
            open_tasks = _get_open_tasks(client, headers, wi_id)
            task = next((item for item in open_tasks if item["nodeId"] == "n10"), None)
            if task is not None:
                break
            assert open_tasks
            response = _submit_task(client, headers, open_tasks[0]["id"], {"title": title})
            assert not response.get("closed")
        else:
            pytest.fail("流程未到达结束节点")

        assert task["status"] == "assigned"
        assert task["assignee"] == "吴凡"
        before = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]["item"]
        assert before["status"] != "closed"

        end_headers = auth_headers(client.post("/api/v1/auth/login", json={
            "account": "wufan", "password": "Demo@1234",
        }).json()["data"]["token"])
        response = _submit_task(client, end_headers, task["id"], {"closure_note": "已确认完成"})
        assert response["closed"] is True

        final = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]["item"]
        assert final["status"] == "closed"
        assert final["progress"] == "完成"

    def test_closes_after_end_node(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("闭环验证")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)

        for _ in range(12):
            open_tasks = _get_open_tasks(client, headers, wi_id)
            if not open_tasks:
                break
            tid = open_tasks[0]["id"]
            resp = client.post(f"/api/v1/tasks/{tid}/actions", headers=headers,
                               json={"action": "submit", "node_id": "n1", "form_values": {"title": title}})
            assert resp.status_code == 200
            data = resp.json()["data"]
            if data["next_node"] is None or data.get("closed"):
                break

        final = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]["item"]
        assert final["status"] == "closed"
        assert final["progress"] == "完成"


# ==================== 5. 审计记录验证 ====================
class TestAuditTrail:
    def test_submit_creates_audit(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("审计验证")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)

        audits = client.get("/api/v1/audits", headers=headers,
                            params={"action": "workflow_instance:create", "page_size": 50}).json()["data"]["items"]
        create_audits = [a for a in audits if title in a.get("target", "")]
        assert len(create_audits) >= 1, "创建工作项应产生审计记录"

        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
        _submit_task(client, headers, task_id, {"title": title})

        audits2 = client.get("/api/v1/audits", headers=headers,
                             params={"action": "task:submit", "page_size": 50}).json()["data"]["items"]
        assert len(audits2) >= 1, "提交任务应产生审计记录"

    def test_transfer_creates_audit(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("转办审计")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                    json={"action": "transfer", "to_user_id": "u2"})

        audits = client.get("/api/v1/audits", headers=headers,
                            params={"action": "task:transfer", "page_size": 50}).json()["data"]["items"]
        assert len(audits) >= 1, "转办应产生审计记录"


# ==================== 6. 通知生成验证 ====================
class TestNotificationGeneration:
    def test_create_wi_generates_notifications(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("通知验证")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)

        ntf = client.get("/api/v1/notifications?page_size=50", headers=headers).json()["data"]["items"]
        relevant = [n for n in ntf if title in n.get("body", "")]
        assert len(relevant) >= 1, "创建工作项应生成站内通知"
        assert all(n.get("wiId") == wi_id for n in relevant)


# ==================== 7. 问题流程（不同模板） ====================
class TestIssueWorkflow:
    def test_issue_flow(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-issue", "v2", [], prefix="IF")
        title = _uniq("问题流转")
        wi_id = _create_wi(client, headers, pid, "tpl-issue", title)
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]

        assert detail["instance"]["templateId"] == "tpl-issue"
        assert len(detail["tasks"]) >= 1
        assert detail["item"]["type"] == "issue"
        assert detail["item"]["status"] == "in_progress"

        # 起始节点已自动完成并流转（创建即流转，无需人工确认起始任务）
        assert detail["tasks"][0]["status"] == "completed", "起始节点应自动完成"
        open_tasks = _get_open_tasks(client, headers, wi_id)
        assert open_tasks, "创建后应已流转到下一节点生成待处理任务"
        result = _submit_task(client, headers, open_tasks[0]["id"])
        assert result["next_node"] is not None, "问题流程提交后应流转"


# ==================== 8. 未授权用户流转操作 ====================
class TestUnauthorizedWorkflowActions:
    def test_dev_cannot_create_project(self, client: TestClient, dev_token: str):
        headers = auth_headers(dev_token)
        r = client.post("/api/v1/projects", headers=headers, json={
            "name": "非法项目", "code": "BAD", "status": "active",
        })
        assert r.status_code in (401, 403)

    def test_unauthenticated_cannot_submit(self, client: TestClient):
        r = client.post("/api/v1/tasks/T-2026-0912/actions",
                        json={"action": "submit", "form_values": {}})
        assert r.status_code == 401


# ==================== 9. 工作项详情完整性 ====================
class TestWorkItemDetailIntegrity:
    def test_detail_has_all_fields(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("详情完整性")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]

        assert detail["item"]["id"] == wi_id
        assert detail["item"]["title"] == title
        assert detail["item"]["status"] == "in_progress"
        assert detail["item"]["type"] == "requirement"
        assert detail["instance"] is not None
        assert detail["instance"]["templateId"] == "tpl-req"
        assert detail["instance"]["state"] == "running"
        assert "title" in detail["startValues"]
        # 起始节点自动完成，后续节点任务已生成（创建即流转）
        assert detail["tasks"][0]["status"] == "completed"
        assert any(t["status"] != "completed" for t in detail["tasks"])
        assert len(detail["tasks"]) >= 1


# ==================== 10. 任务候选处理人解析 ====================
class TestCandidateResolution:
    def test_candidates_endpoint_works(self, client: TestClient, leader_token: str, org_admin_token: str):
        """候选处理人接口正常返回，含 users 字段。"""
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [
            {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": ["product_manager"]},
        ])
        title = _uniq("候选验证")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        r = client.get(f"/api/v1/tasks/{task_id}/candidates", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "users" in data
        assert "node" in data

    def test_no_binding_returns_empty(self, client: TestClient, leader_token: str):
        headers = auth_headers(leader_token)
        r = client.get("/api/v1/tasks/T-2026-0912/candidates", headers=headers)
        assert r.status_code == 200
        assert "users" in r.json()["data"]


# ==================== 11. 幂等保护 ====================
class TestIdempotency:
    def test_double_submit_blocked(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("幂等测试")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        r1 = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                         json={"action": "submit", "node_id": "n1", "form_values": {}})
        assert r1.status_code == 200

        r2 = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                         json={"action": "submit", "node_id": "n1", "form_values": {}})
        assert r2.status_code == 409
        assert r2.json()["code"] == 40902

    def test_claim_completed_task_blocked(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("幂等认领")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                    json={"action": "submit", "node_id": "n1", "form_values": {}})

        r = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers, json={"action": "claim"})
        assert r.status_code == 409


# ==================== 12. 任务详情含 Agent 建议 ====================
class TestTaskDetailWithSuggestions:
    def test_detail_has_suggestions_field(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("Agent建议")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        r = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "suggestions" in data
        assert isinstance(data["suggestions"], list)


# ==================== 13. 请求补充信息 ====================
class TestRequestInfo:
    def test_request_info_flow(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("补信息")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)
        task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

        r = client.post(f"/api/v1/tasks/{task_id}/actions", headers=headers,
                        json={"action": "request_info"})
        assert r.status_code == 200
        assert r.json()["data"]["task"]["status"] == "waiting_for_information"

        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
        task_in_wi = next(t for t in detail["tasks"] if t["id"] == task_id)
        assert task_in_wi["status"] == "waiting_for_information"


# ==================== 14. 模板绑定不匹配拦截 ====================
class TestTemplateBindingGuard:
    def test_unbound_template_rejected(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-issue", "v2", [], prefix="UB")
        r = client.post("/api/v1/work-items", headers=headers, json={
            "project_id": pid, "template_id": "tpl-req",
            "start_values": {"title": _uniq("不匹配"), "description": "x"},
        })
        assert r.json()["code"] == 40301


# ==================== 15. 标题去重拦截 ====================
class TestTitleDedup:
    def test_duplicate_title_across_projects(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        org_h = auth_headers(org_admin_token)
        pid_a = _make_project(client, org_h, "tpl-req", "v3", [], prefix="DA")
        pid_b = _make_project(client, org_h, "tpl-issue", "v2", [], prefix="DB")
        title = _uniq("全局去重")

        r1 = client.post("/api/v1/work-items", headers=headers, json={
            "project_id": pid_a, "template_id": "tpl-req",
            "start_values": {"title": title, "description": "a"},
        })
        assert r1.status_code == 200

        r2 = client.post("/api/v1/work-items", headers=headers, json={
            "project_id": pid_b, "template_id": "tpl-issue",
            "start_values": {"title": title, "description": "b"},
        })
        # 标题不做全局唯一校验：跨项目重复标题可创建
        assert r2.status_code == 200, f"重复标题应可创建: {r2.status_code} {r2.text}"
        assert r2.json()["data"]["item"]["title"] == title


# ==================== 11. 并行分叉 / 汇合 ====================
class TestParallelSplitJoin:
    """并行分叉（多出边拆单）+ 汇合（所有分支完成才继续）。"""

    def test_parallel_split_and_join(self, client: TestClient, leader_token: str, org_admin_token: str):
        headers = auth_headers(leader_token)
        pid = _make_project(client, auth_headers(org_admin_token), "tpl-req", "v3", [])
        title = _uniq("并行链路")
        wi_id = _create_wi(client, headers, pid, "tpl-req", title)

        def submit_first(fv=None):
            open_t = _get_open_tasks(client, headers, wi_id)
            assert open_t, "应有待处理任务"
            return _submit_task(client, headers, open_t[0]["id"], fv)

        # 需求分析 → 产品评审 → 需求拆分（并行分叉）
        submit_first()
        submit_first()
        d = submit_first()
        assert d.get("parallel") is True, "需求拆分应并行拆分"
        branches = {t["node"] for t in _get_open_tasks(client, headers, wi_id)}
        assert {"后端开发", "前端开发"} <= branches, f"应生成并行分支任务: {branches}"
        # 只完成后端开发 → 等待汇合，不生成测试
        bd = next(t for t in _get_open_tasks(client, headers, wi_id) if t["node"] == "后端开发")
        r = client.post(f"/api/v1/tasks/{bd['id']}/actions", headers=headers,
                        json={"action": "submit", "form_values": {}})
        assert r.json()["data"]["waiting_join"] is True, "另一分支未完成应等待汇合"
        assert not any(t["node"] == "测试" for t in _get_open_tasks(client, headers, wi_id)), "汇合前不应生成测试任务"
        # 完成前端开发 → 汇合生成测试
        fd = next(t for t in _get_open_tasks(client, headers, wi_id) if t["node"] == "前端开发")
        r2 = client.post(f"/api/v1/tasks/{fd['id']}/actions", headers=headers,
                         json={"action": "submit", "form_values": {}})
        d2 = r2.json()["data"]
        assert not d2.get("waiting_join") and d2["next_node"]["label"] == "测试", "全部分支完成后应生成测试任务"
        # 剩余链路走完 → 关闭，任务链含两个并行分支
        while _get_open_tasks(client, headers, wi_id):
            submit_first()
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
        nodes = [t["node"] for t in detail["tasks"]]
        assert "后端开发" in nodes and "前端开发" in nodes, "两个并行分支都应执行"
        assert detail["item"]["status"] == "closed"
