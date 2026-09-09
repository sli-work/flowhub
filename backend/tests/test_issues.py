"""Generic task issue lifecycle API tests."""
import uuid

from fastapi.testclient import TestClient


def test_task_can_create_a_blocking_issue_for_completed_predecessor(client: TestClient, org_headers: dict):
    code = f"IS{uuid.uuid4().hex[:7]}"
    project = client.post("/api/v1/projects", headers=org_headers, json={
        "name": f"问题闭环-{code}", "code": code, "status": "active", "manager": "李婷",
        "template_bindings": [{"template_id": "tpl-req", "version": "v3", "status": "active", "assignments": []}],
    }).json()["data"]["item"]["id"]
    wi = client.post("/api/v1/work-items", headers=org_headers, json={
        "project_id": project, "template_id": "tpl-req", "start_values": {"title": "问题闭环测试", "description": "e2e"},
    }).json()["data"]["item"]["id"]
    open_task = next(task["id"] for task in client.get(f"/api/v1/work-items/{wi}", headers=org_headers).json()["data"]["tasks"] if task["status"] != "completed")
    assert client.post(f"/api/v1/tasks/{open_task}/actions", headers=org_headers, json={"action": "submit", "form_values": {"conclusion": "需求分析完成"}}).status_code == 200
    source = next(task["id"] for task in client.get(f"/api/v1/work-items/{wi}", headers=org_headers).json()["data"]["tasks"] if task["status"] != "completed")
    detail = client.get(f"/api/v1/tasks/{source}", headers=org_headers)
    assert detail.status_code == 200
    targets = detail.json()["data"].get("issueTargets", [])
    assert targets, "seed test task must expose completed predecessor targets"

    invalid_screenshot = client.post(
        f"/api/v1/tasks/{source}/issues", headers=org_headers,
        json={"title": "无效截图", "description": "截图必须来自当前工作项", "target_task_id": targets[0]["taskId"], "attachments": [{"id": "not-a-document", "name": "fake.png"}]},
    )
    assert invalid_screenshot.status_code == 400
    assert "截图" in invalid_screenshot.json()["message"]

    created = client.post(
        f"/api/v1/tasks/{source}/issues", headers=org_headers,
        # P1 未显式选择阻断时，采用紧急问题的阻断默认值。
        json={"title": "接口返回字段错误", "description": "复现：调用创建接口后字段缺失", "description_doc": {
            "type": "doc", "content": [{"type": "paragraph", "content": [
                {"type": "text", "text": "复现："}, {"type": "text", "marks": [{"type": "bold"}], "text": "调用创建接口后字段缺失"},
            ]}],
        }, "target_task_id": targets[0]["taskId"], "priority": "P1"},
    )
    assert created.status_code == 200, created.text
    issue = created.json()["data"]["issue"]
    assert issue["status"] == "handling"
    assert issue["blocking"] is True
    assert issue["handlerTaskId"]
    assert issue["descriptionText"] == "复现：调用创建接口后字段缺失"
    assert issue["descriptionDoc"]["type"] == "doc"
    # 返工子任务只能在问题闭环中观察，绝不能插入主流程图。
    main_task_ids = {row["id"] for row in client.get(f"/api/v1/work-items/{wi}", headers=org_headers).json()["data"]["tasks"]}
    assert issue["handlerTaskId"] not in main_task_ids

    refreshed = client.get(f"/api/v1/tasks/{source}", headers=org_headers).json()["data"]
    assert refreshed["issueSummary"]["blocking"] >= 1
    assert any(row["id"] == issue["id"] for row in refreshed["issues"])

    blocked = client.post(f"/api/v1/tasks/{source}/actions", headers=org_headers, json={"action": "submit", "form_values": {"verdict": "pass", "opinion": "继续"}})
    assert blocked.status_code == 409

    fixed = client.post(f"/api/v1/tasks/{issue['handlerTaskId']}/actions", headers=org_headers, json={"action": "submit", "form_values": {"conclusion": "已修复接口字段"}})
    assert fixed.status_code == 200, fixed.text
    verification_id = fixed.json()["data"]["next_task_id"]
    verified = client.post(f"/api/v1/tasks/issues/{issue['id']}/verify", headers=org_headers, json={"passed": True, "notes": "回归通过"})
    assert verified.status_code == 200, verified.text
    assert verified.json()["data"]["issue"]["status"] == "closed"
    assert verification_id

    retried = client.post(
        f"/api/v1/tasks/{source}/issues", headers=org_headers,
        json={"title": "第二轮问题", "description": "需要再次处理", "target_task_id": targets[0]["taskId"], "priority": "P2", "blocking": False},
    )
    assert retried.status_code == 200, retried.text
    retry_issue = retried.json()["data"]["issue"]
    assert client.post(f"/api/v1/tasks/{retry_issue['handlerTaskId']}/actions", headers=org_headers, json={"action": "submit", "form_values": {"conclusion": "已修复"}}).status_code == 200
    failed_verification = client.post(f"/api/v1/tasks/issues/{retry_issue['id']}/verify", headers=org_headers, json={"passed": False, "notes": "仍未修复"})
    assert failed_verification.status_code == 200, failed_verification.text
    assert failed_verification.json()["data"]["issue"]["status"] == "handling"
    assert failed_verification.json()["data"]["issue"]["round"] == 2
    assert failed_verification.json()["data"]["nextTaskId"]
