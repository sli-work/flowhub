"""节点表单 AI 填充：schema 解析矫正 / 协助填充 / Expert 自动闭环 / 失败兜底。"""
import json
import time
import uuid

import pytest

from test_workflow_flows import _create_wi, _get_open_tasks, _make_project


# ---------- 纯函数：schema 输出解析 ----------

SCHEMA = [
    {"key": "conclusion", "label": "分析结论", "type": "textarea", "required": True},
    {"key": "verdict", "label": "结论", "type": "select", "required": True,
     "options": [{"label": "通过", "value": "pass"}, {"label": "不通过", "value": "reject"}]},
    {"key": "score", "label": "评分", "type": "number", "required": False},
    {"key": "tags", "label": "标签", "type": "multiselect", "required": False,
     "options": [{"label": "高优", "value": "p0"}, {"label": "普通", "value": "p2"}]},
    {"key": "report", "label": "分析报告", "type": "upload", "required": True},
]


def test_parse_schema_output_coerces_and_maps():
    from flowhub_api.services.expert_runtime import parse_schema_output

    raw = "前置说明 {\"conclusion\": \"结论内容\", \"verdict\": \"通过\", \"score\": \"3\", \"tags\": [\"高优\", \"p2\"], \"report\": \"# 报告正文\"} 后缀"
    values, warnings = parse_schema_output(SCHEMA, raw)
    assert values["conclusion"] == "结论内容"
    assert values["verdict"] == "pass", "label 应映射为 value"
    assert values["score"] == 3
    assert values["tags"] == ["p0", "p2"], "label 与 value 混合都应映射"
    assert values["report"] == "# 报告正文", "upload 字段保留正文，由调用方转文档"
    assert warnings == []


def test_parse_schema_output_rejects_invalid_option():
    from flowhub_api.services.expert_runtime import parse_schema_output

    values, warnings = parse_schema_output(SCHEMA, "{\"verdict\": \"不知道\", \"report\": \"x\"}")
    assert "verdict" not in values
    assert any("结论" in w for w in warnings)
    assert values["report"] == "x"


def test_parse_schema_output_unparsable():
    from flowhub_api.services.expert_runtime import parse_schema_output

    values, warnings = parse_schema_output(SCHEMA, "模型胡言乱语，没有 JSON")
    assert values == {}
    assert warnings


# ---------- Fake 模型（替代不可达 Provider） ----------

class _FakeCompletion:
    def __init__(self, content):
        self.content = content


class FakeChatOpenAI:
    """替换 expert_runtime.ChatOpenAI：ainvoke 返回预置 JSON 产出。"""
    payload = "{}"
    should_fail = False

    def __init__(self, *args, **kwargs):
        pass

    async def ainvoke(self, messages):
        if FakeChatOpenAI.should_fail:
            raise ConnectionError("provider unreachable")
        return _FakeCompletion(FakeChatOpenAI.payload)


@pytest.fixture
def fake_model(monkeypatch):
    from flowhub_api.services import expert_runtime as er

    monkeypatch.setattr(er, "ChatOpenAI", FakeChatOpenAI)


def _publish_expert_with_deployment(client, headers, deployment_name):
    """创建 Provider/Expert → 测试 → 发布 → Deployment，返回 deployment_id。"""
    model_id = client.post("/api/v1/providers", headers=headers, json={
        "name": f"P-{uuid.uuid4().hex[:6]}", "base_url": "https://example.test/v1",
        "api_key": "k", "models": ["fake-model"],
    }).json()["data"]["provider"]["models"][0]["id"]
    expert = client.post("/api/v1/experts", headers=headers, json={
        "name": "Fill Expert", "slug": f"fill-{uuid.uuid4().hex[:6]}", "description": "d",
        "system_prompt": "sp", "provider_model_id": model_id,
    }).json()["data"]
    eid, version_id = expert["expert"]["id"], expert["version"]["id"]
    assert client.post(f"/api/v1/experts/{eid}/versions/{version_id}/test", headers=headers,
                       json={"prompt": "t", "write_intent": False}).status_code == 200
    assert client.post(f"/api/v1/experts/{eid}/versions/{version_id}/publish", headers=headers).status_code == 200
    dep = client.post(f"/api/v1/experts/{eid}/deployments", headers=headers,
                      json={"name": deployment_name, "environment": "test", "alias": "fill"}).json()["data"]["deployment"]
    return model_id, dep["id"]


def _publish_flow_template(client, headers, deployment_id):
    """自定义流程：需求提交 → AI 任务节点（绑定 Deployment + schema + 验收）→ 结束。"""
    tpl = client.post("/api/v1/templates", headers=headers, json={"name": f"AI填充-{uuid.uuid4().hex[:6]}", "type": "requirement"}).json()["data"]["template"]
    nodes = [
        {"id": "n1", "label": "需求提交", "type": "start", "x": 24, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "START", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "",
                 "schema": [{"key": "title", "label": "标题", "type": "input", "required": True}]}},
        {"id": "n2", "label": "AI 产出", "type": "task", "x": 200, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "TASK", "purpose": "生成分析产出", "handler": "人工 + Expert 可协助", "fallback": "", "sla": "24 小时",
                 "output": "x",
                 "schema": [
                     {"key": "conclusion", "label": "分析结论", "type": "textarea", "required": True},
                     {"key": "verdict", "label": "结论", "type": "select", "required": True,
                      "options": [{"label": "通过", "value": "pass"}, {"label": "不通过", "value": "reject"}]},
                     {"key": "report", "label": "分析报告", "type": "upload", "required": True},
                 ],
                 "deliverable": {"instruction": "生成分析", "acceptance": [{"key": "a1", "text": "结论完整"}], "aiGuidance": "", "example": ""},
                 "split": {"mode": "off"},
                 "expert": {"expertDeploymentId": deployment_id}}},
        {"id": "n3", "label": "完成", "type": "end", "x": 376, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "END", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "", "schema": []}},
    ]
    edges = [["n1", "n2"], ["n2", "n3"]]
    r = client.post(f"/api/v1/templates/{tpl['id']}/versions/save-and-publish", headers=headers,
                    json={"nodes": nodes, "edges": edges, "fallbacks": []})
    assert r.status_code == 200, r.text
    return tpl["id"]


# ---------- Expert 协助填充（人工确认 → 回填表单） ----------

def _wait_wi_task_status(client, headers, wi_id, node_id, status, timeout=8.0):
    """轮询等待工作项某节点任务进入指定状态（Expert Run 已改为后台异步执行）。"""
    deadline = time.time() + timeout
    detail = None
    while time.time() < deadline:
        detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
        t = next((x for x in detail["tasks"] if x["nodeId"] == node_id), None)
        if t is not None and t["status"] == status:
            return detail
        time.sleep(0.05)
    return detail


def _wait_run_succeeded(client, headers, task_id, timeout=8.0):
    """轮询等待任务关联的 Expert Run 执行完成。"""
    deadline = time.time() + timeout
    runs = []
    while time.time() < deadline:
        runs = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"]
        if runs and runs[0]["status"] in ("succeeded", "failed"):
            return runs
        time.sleep(0.05)
    return runs


def test_ai_fill_generates_values_and_document(client, org_headers, fake_model):
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-1")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI填充-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    FakeChatOpenAI.payload = json.dumps({
        "conclusion": "范围已明确", "verdict": "通过", "report": "# 分析报告\n正文",
    })
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["values"]["conclusion"] == "范围已明确"
    assert data["values"]["verdict"] == "pass"
    ref = data["values"]["report"][0]
    assert ref["id"] and ref["name"], "upload 字段应回填文档引用"
    assert data["warnings"] == []

    # 文档已挂到工作项（kind=节点表单附件）
    detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
    docs = client.get(f"/api/v1/documents?wi={wi_id}", headers=headers).json()["data"]["items"]
    assert any(d["id"] == ref["id"] for d in docs), "生成的文档应出现在工作项文档列表"


def test_ai_fill_requires_expert_binding(client, org_headers):
    headers = org_headers
    tpl_id = _publish_flow_template(client, headers, deployment_id="dep-none")
    # 重新发布一个无绑定版本：直接用未绑定模板（seed 画布无 expert）走 tpl-req 亦可；这里用自定义模板的草稿节点无法改绑定 → 用未绑定部署 id
    pid = _make_project(client, headers, "tpl-req", "v3", [])
    wi_id = _create_wi(client, headers, pid, "tpl-req", "AI填充-无绑定")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 422, r.text
    assert "Expert Deployment" in r.json()["message"]


def test_ai_fill_model_failure_returns_clear_error(client, org_headers, fake_model):
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-2")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI填充-失败")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    FakeChatOpenAI.should_fail = True
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 422
    assert "暂不可用" in r.json()["message"]
    FakeChatOpenAI.should_fail = False


def test_adopt_run_reuses_spawned_run(client, org_headers, fake_model):
    """协助节点标准路径：任务到达时引擎自动创建 Run → 采纳接口复用其产出回填表单，无需重复触发模型。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-3")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "到达即生成的结论", "verdict": "通过", "report": "# 到达报告"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI采纳-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded", "任务到达时引擎应已自动创建 Run（后台执行）"

    r = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": runs[0]["id"]})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["values"]["conclusion"] == "到达即生成的结论"
    assert data["values"]["verdict"] == "pass"
    assert data["values"]["report"][0]["id"], "upload 字段应回填文档引用"

    # 采纳不产生新 Run：仍是到达时那一条
    td2 = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]
    assert len(td2["expertRuns"]) == len(runs)


# ---------- Expert 自动：run 成功后免审批自动采纳并流转 ----------

def _publish_auto_template(client, headers, deployment_id):
    tpl = client.post("/api/v1/templates", headers=headers, json={"name": f"AI自动-{uuid.uuid4().hex[:6]}", "type": "requirement"}).json()["data"]["template"]
    n2 = {"id": "n2", "label": "AI 自动产出", "type": "task", "x": 200, "y": 24, "width": 118, "height": 56,
          "cfg": {"typeLine": "TASK", "purpose": "自动生成", "handler": "Expert 自动", "fallback": "", "sla": "24 小时",
                  "output": "x",
                  "schema": [{"key": "conclusion", "label": "分析结论", "type": "textarea", "required": True},
                             {"key": "report", "label": "分析报告", "type": "upload", "required": True}],
                  "deliverable": {"instruction": "自动生成", "acceptance": [{"key": "a1", "text": "结论完整"}], "aiGuidance": "", "example": ""},
                  "expert": {"expertDeploymentId": deployment_id}}}
    nodes = [
        {"id": "n1", "label": "需求提交", "type": "start", "x": 24, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "START", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "",
                 "schema": [{"key": "title", "label": "标题", "type": "input", "required": True}]}},
        n2,
        {"id": "n3", "label": "完成", "type": "end", "x": 376, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "END", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "", "schema": []}},
    ]
    r = client.post(f"/api/v1/templates/{tpl['id']}/versions/save-and-publish", headers=headers,
                    json={"nodes": nodes, "edges": [["n1", "n2"], ["n2", "n3"]], "fallbacks": []})
    assert r.status_code == 200, r.text
    return tpl["id"]


def test_expert_auto_fills_and_advances_without_approval(client, org_headers, fake_model):
    """自动节点免人工介入：任务到达即触发 Run → 成功后自动采纳填充并流转，无需审批。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "auto-dep-1")
    tpl_id = _publish_auto_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "自动生成的结论", "report": "# 自动报告"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI自动-E2E")

    detail = _wait_wi_task_status(client, headers, wi_id, "n2", "completed")
    auto_task = next(t for t in detail["tasks"] if t["nodeId"] == "n2")
    assert auto_task["status"] == "completed", "后台 Run 成功后应自动采纳并完成节点"
    next_task = next(t for t in detail["tasks"] if t["node"] == "完成")
    assert next_task["status"] == "assigned", "自动流转到结束节点任务"
    assert detail["item"]["status"] == "in_progress", "结束节点未提交前 WI 不关闭"

    td = client.get(f"/api/v1/tasks/{auto_task['id']}", headers=headers).json()["data"]
    runs = td["expertRuns"]
    assert runs and runs[0]["status"] == "succeeded"
    # 无需审批：不应产生 ExpertApproval
    approvals = client.get("/api/v1/expert-approvals", headers=headers).json()["data"]["items"]
    assert not [a for a in approvals if a.get("runId") == runs[0]["id"]], "自动采纳不应触发审批"


def test_expert_auto_model_failure_falls_back_to_human(client, org_headers, fake_model):
    """自动节点 Run 失败 → 任务保持 assigned 由人工兜底，成功重试后仍可走自动采纳。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "auto-dep-2")
    tpl_id = _publish_auto_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.should_fail = True
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI自动-失败兜底")

    detail = _wait_wi_task_status(client, headers, wi_id, "n2", "assigned")
    auto_task = next(t for t in detail["tasks"] if t["nodeId"] == "n2")
    assert auto_task["status"] == "assigned", "后台 Run 失败 → 任务回退人工兜底"
    FakeChatOpenAI.should_fail = False

    # 人工兜底路径可用：协助填充（重新生成）→ 提交
    r = client.post(f"/api/v1/tasks/{auto_task['id']}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, r.text
    values = r.json()["data"]["values"]
    r2 = client.post(f"/api/v1/tasks/{auto_task['id']}/actions", headers=headers,
                     json={"action": "submit", "form_values": values, "acceptance_checks": {"a1": {"text": "结论完整", "checked": True}}})
    assert r2.status_code == 200, r2.text
