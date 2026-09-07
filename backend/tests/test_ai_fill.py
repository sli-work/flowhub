"""节点表单 AI 填充：schema 解析矫正 / 协助填充 / Expert 自动闭环 / 失败兜底。"""
import asyncio
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


def test_parse_schema_output_rejects_plain_text_without_field_fallback():
    """非 JSON 产出必须保留原文供查看，但不能猜测并回填任何字段。"""
    from flowhub_api.services.expert_runtime import parse_schema_output

    raw = "### 一、需求合理性结论\n**结论：有条件可行。**\n\n### 二、影响范围分析\n| 后端模块 | drcc-backend |\n"
    values, warnings = parse_schema_output(SCHEMA, raw)
    assert values == {}
    assert warnings == ["模型输出不是 JSON，无法自动回填字段"]


def test_parse_schema_output_unwraps_markdown_fences_for_textarea():
    """模型偶发把 Markdown 正文包进代码围栏时，采纳内容应仍可直接渲染。"""
    from flowhub_api.services.expert_runtime import parse_schema_output

    values, warnings = parse_schema_output(
        [{"key": "analysis", "label": "分析", "type": "textarea", "required": True}],
        '{"analysis":"```markdown\\n## 结论\\n- 可执行  \\n```"}',
    )

    assert values == {"analysis": "## 结论\n- 可执行  "}
    assert warnings == []


def test_parse_schema_output_unparsable_no_target():
    """连可回填的文本字段都没有时才放弃：返回空 + warnings。"""
    from flowhub_api.services.expert_runtime import parse_schema_output

    values, warnings = parse_schema_output(
        [{"key": "verdict", "label": "结论", "type": "select", "required": True,
          "options": [{"label": "通过", "value": "pass"}]}],
        "模型胡言乱语，没有 JSON",
    )
    assert values == {}
    assert warnings


def test_schema_output_instruction_distinguishes_markdown_from_single_line_text():
    """Expert 应知道 textarea/file 是 Markdown 正文，而 input 不应携带 Markdown 包装。"""
    from flowhub_api.services.expert_runtime import build_schema_output_instruction

    instruction = build_schema_output_instruction([
        {"key": "summary", "label": "摘要", "type": "input", "required": True},
        {"key": "analysis", "label": "分析", "type": "textarea", "required": True},
        {"key": "report", "label": "报告", "type": "upload", "required": True},
    ])

    assert "summary" in instruction and "单行纯文本" in instruction
    assert "analysis" in instruction and "纯 Markdown 正文" in instruction
    assert "report" in instruction and "纯 Markdown 正文" in instruction
    assert "不要在字段值中重复字段名" in instruction


# ---------- Fake 模型（替代不可达 Provider） ----------

class _FakeCompletion:
    def __init__(self, content):
        self.content = content


class FakeChatOpenAI:
    """替换 expert_runtime.ChatOpenAI：ainvoke 返回预置 JSON 产出。"""
    payload = "{}"
    should_fail = False
    delay = 0.0
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def ainvoke(self, messages):
        FakeChatOpenAI.calls += 1
        if FakeChatOpenAI.delay:
            await asyncio.sleep(FakeChatOpenAI.delay)
        if FakeChatOpenAI.should_fail:
            raise ConnectionError("provider unreachable")
        if any("你是事实核验器" in str(message) for message in messages):
            return _FakeCompletion('{"pass": true, "issues": []}')
        return _FakeCompletion(FakeChatOpenAI.payload)


@pytest.fixture
def fake_model(monkeypatch):
    from flowhub_api.services import expert_runtime as er

    monkeypatch.setattr(er, "ChatOpenAI", FakeChatOpenAI)
    # 用例内 should_fail/delay 改动即使断言失败也不外泄（避免污染后续用例）
    yield
    FakeChatOpenAI.should_fail = False
    FakeChatOpenAI.delay = 0.0
    FakeChatOpenAI.calls = 0


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


def test_task_expert_run_exposes_observable_stage_events(client, org_headers, fake_model):
    """任务页应能看到 Run 从排队到生成、校验和完成的阶段，定位慢点无需查日志。"""
    headers = org_headers
    _model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-observability")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "可观测", "verdict": "通过", "report": "# 报告"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "Expert 可观测性")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    runs = _wait_run_succeeded(client, headers, task_id)
    events = runs[0]["events"]

    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert any(event["kind"] == "queue" and event["status"] == "queued" for event in events)
    assert any(event["kind"] == "model" and event["status"] == "running" for event in events)
    assert any(event["kind"] == "quality" for event in events)
    assert all("createdAt" in event and "title" in event for event in events)


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
    # 覆盖式重跑要求无 running Run：先等到达时的自动 Run 终态，再 ai-fill 覆盖它
    initial = _wait_run_succeeded(client, headers, task_id)
    assert initial and initial[0]["status"] == "succeeded", "前置：任务到达应已自动创建并完成 Run"
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # 持久调度契约：提交事务只登记 queued Run；worker 领取后才变为 running。
    # 结果经任务详情轮询获取，不能把尚未领取误报为正在运行。
    assert data["run"]["status"] == "queued"
    assert data["run"]["id"] == initial[0]["id"], "重跑应复用原 Run 记录"
    run_id = data["run"]["id"]
    runs = _wait_run_succeeded(client, headers, task_id)
    assert any(x["id"] == run_id for x in runs), "重跑后的 Run 应出现在任务详情"
    new_run = next(x for x in runs if x["id"] == run_id)
    assert new_run["status"] == "succeeded"
    td = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]
    assert td["expertRuns"][0]["id"] == run_id, "最新 Run 应置顶"
    # 采纳新 Run 产出 → 回填表单
    r2 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": run_id})
    assert r2.status_code == 200, r2.text
    values = r2.json()["data"]["values"]
    assert values["conclusion"] == "范围已明确"
    assert values["verdict"] == "pass"
    ref = values["report"][0]
    assert ref["id"] and ref["name"], "upload 字段应回填文档引用"

    # 文档已挂到工作项（kind=节点表单附件）
    detail = client.get(f"/api/v1/work-items/{wi_id}", headers=headers).json()["data"]
    docs = client.get(f"/api/v1/documents?wi={wi_id}", headers=headers).json()["data"]["items"]
    assert any(d["id"] == ref["id"] for d in docs), "生成的文档应出现在工作项文档列表"


def test_non_json_run_is_retained_but_cannot_be_adopted(client, org_headers, fake_model):
    """格式修复一次后仍非 JSON：保留原文供复核，但不得回填、生成文档或采纳。"""
    headers = org_headers
    _model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-invalid-json")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = "### 分析结论\n原始 Markdown 正文，不是 JSON"
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI非JSON-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded"
    run = runs[0]
    assert run["output"] == FakeChatOpenAI.payload
    assert run["parsed"]["formatStatus"] == "invalid"
    assert run["parsed"]["values"] == {}
    assert run["parsed"]["formatRepair"]["attempted"] is True

    adopted = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": run["id"]})
    assert adopted.status_code == 422
    assert "JSON" in adopted.json()["message"]
    docs = client.get(f"/api/v1/documents?wi={wi_id}", headers=headers).json()["data"]["items"]
    assert docs == []


def test_ai_fill_rerun_with_context(client, org_headers, fake_model):
    """覆盖式重跑：复用原 Run 记录（总数不变、同 id、输出被替换），context 落库供追溯。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-ctx")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI重跑-上下文")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    # 任务到达时已有一次自动 Run；记录覆盖前的 Run 集合
    initial_runs = _wait_run_succeeded(client, headers, task_id)
    assert initial_runs, "前置：任务到达应已自动创建 Run"
    original_id = initial_runs[0]["id"]
    FakeChatOpenAI.payload = json.dumps({"conclusion": "带上下文的结论", "verdict": "通过", "report": "# 报告"})
    ctx = "上一轮结论遗漏了备件库存因素；请重点参考附件《Q2 服务复盘》"
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={"context": ctx})
    assert r.status_code == 200, r.text
    rerun = r.json()["data"]["run"]
    assert rerun["id"] == original_id, "重跑应复用原 Run 记录（覆盖而非新建）"
    assert rerun["status"] == "queued"
    assert rerun["context"] == ctx
    runs = _wait_run_succeeded(client, headers, task_id)
    assert len(runs) == len(initial_runs), "覆盖式重跑不应新增 Run 记录"
    rerun_in_detail = next(x for x in runs if x["id"] == original_id)
    assert rerun_in_detail["status"] == "succeeded"
    assert rerun_in_detail["context"] == ctx
    rerun_output = json.loads(rerun_in_detail["output"])
    assert rerun_output["conclusion"] == "带上下文的结论", "输出应被新一轮结果覆盖"
    # 采纳覆盖后的 Run → 表单回填为新产出
    adopted = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": original_id})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["data"]["values"]["conclusion"] == "带上下文的结论"


def test_ai_fill_running_run_blocks_rerun(client, org_headers, fake_model):
    """Run 执行中（running）→ 重跑被 423 拒绝，防止并发覆盖同一记录。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-runlock")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI重跑-执行中")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    FakeChatOpenAI.payload = json.dumps({"conclusion": "慢生成", "verdict": "通过", "report": "# 报告"})
    FakeChatOpenAI.delay = 1.5
    try:
        # 等自动 Run 进入 running（可达态再发第二请求），确认 423 拦截
        deadline = time.time() + 8.0
        while time.time() < deadline:
            runs = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"]
            if runs and runs[0]["status"] in {"queued", "running", "interrupted"}:
                break
            time.sleep(0.02)
        r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
        assert r.status_code == 423, f"执行中重跑应被拒绝: {r.status_code} {r.text}"
        assert "执行中" in r.json()["message"]
    finally:
        FakeChatOpenAI.delay = 0.0
    _wait_run_succeeded(client, headers, task_id)


def test_ai_fill_context_too_long_rejected(client, org_headers, fake_model):
    """补充上下文超长（>2000 字）→ 422 校验拒绝。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-long")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI重跑-超长")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={"context": "字" * 2001})
    assert r.status_code == 422


def test_ai_fill_requires_expert_binding(client, org_headers):
    headers = org_headers
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
    # 先等到达时的自动 Run 走完（终态 failed）：覆盖式重跑不允许覆盖 running 中的 Run
    initial = _wait_run_succeeded(client, headers, task_id)
    assert initial and initial[0]["status"] == "failed", "前置：should_fail 下自动 Run 应失败落终态"
    # ai-fill 覆盖该失败 Run 重跑，应放行并登记 queued Run。
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, "改为后台执行后，发起本身不应失败"
    run_id = r.json()["data"]["run"]["id"]
    assert run_id == initial[0]["id"], "重跑应复用原 Run 记录"
    # 轮询到终态：失败 Run 保留在详情里，可再次重新执行
    deadline = time.time() + 8.0
    runs = []
    while time.time() < deadline:
        runs = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"]
        run = next((x for x in runs if x["id"] == run_id), None)
        if run and run["status"] not in {"queued", "running", "interrupted"}:
            break
        time.sleep(0.05)
    assert run and run["status"] == "failed"
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


def test_adopt_run_parsed_snapshot_and_idempotent(client, org_headers, fake_model):
    """解析快照：Run 成功后按 schema 预解析存 parsed（详情可见，供前端预览）；
    重复采纳幂等——upload 字段不重复生成文档。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-snap")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "快照结论", "verdict": "通过", "report": "# 快照报告正文"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI快照-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded"
    # 详情携带 parsed 快照（前端字段预览的数据源）
    snap = runs[0].get("parsed")
    assert snap and snap["values"]["conclusion"] == "快照结论", "Run 详情应携带解析快照"

    # 第一次采纳：生成文档并回填
    r1 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": runs[0]["id"]})
    assert r1.status_code == 200, r1.text
    ref1 = r1.json()["data"]["values"]["report"][0]
    # 第二次采纳：幂等，文档引用不变（不重复生成）
    r2 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": runs[0]["id"]})
    assert r2.status_code == 200, r2.text
    ref2 = r2.json()["data"]["values"]["report"][0]
    assert ref2["id"] == ref1["id"], "重复采纳应复用同一文档（快照幂等）"
    # 快照中 upload 值已被替换为文档引用
    snap2 = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"][0]["parsed"]
    assert isinstance(snap2["values"]["report"], list) and snap2["values"]["report"][0]["id"] == ref1["id"]


def test_adopt_replaces_stale_doc_on_rerun(client, org_headers, fake_model):
    """覆盖式重跑后再采纳：新产出生成新文档，上一轮采纳生成的旧 AI 文档被删除（不堆积）；
    且「节点表单附件」不出现在文档中心全量列表（仅在所属工作项文档里可见）。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-replace")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI重跑-文档替换")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]

    # 第一轮：采纳生成文档 v1
    FakeChatOpenAI.payload = json.dumps({"conclusion": "第一轮结论", "verdict": "通过", "report": "# 第一轮报告"})
    runs1 = _wait_run_succeeded(client, headers, task_id)
    assert runs1 and runs1[0]["status"] == "succeeded"
    r1 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": runs1[0]["id"]})
    assert r1.status_code == 200, r1.text
    ref1 = r1.json()["data"]["values"]["report"][0]

    # 第二轮：覆盖式重跑后采纳 → 旧文档应被替换
    FakeChatOpenAI.payload = json.dumps({"conclusion": "第二轮结论", "verdict": "通过", "report": "# 第二轮报告"})
    r = client.post(f"/api/v1/tasks/{task_id}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, r.text
    run_id = r.json()["data"]["run"]["id"]
    runs2 = _wait_run_succeeded(client, headers, task_id)
    assert next(x for x in runs2 if x["id"] == run_id)["status"] == "succeeded"
    r2 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": run_id})
    assert r2.status_code == 200, r2.text
    ref2 = r2.json()["data"]["values"]["report"][0]
    assert ref2["id"] != ref1["id"], "重跑后采纳应生成新文档"

    # 工作项文档里：新文档在、旧文档已被删除（不堆积）
    wi_docs = client.get(f"/api/v1/documents", headers=headers, params={"wi": wi_id, "page_size": 100}).json()["data"]["items"]
    wi_ids = {d["id"] for d in wi_docs}
    assert ref2["id"] in wi_ids
    assert ref1["id"] not in wi_ids, "上一轮采纳的旧 AI 文档应被清理"

    # 文档中心全量视图（不带 wi）不出现「节点表单附件」
    center_docs = client.get("/api/v1/documents", headers=headers, params={"page_size": 100}).json()["data"]["items"]
    assert all(d["kind"] != "节点表单附件" for d in center_docs), "文档中心不应展示节点表单附件"


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

    # 人工兜底路径可用：协助填充（重新生成）→ 采纳新 Run 产出 → 提交
    r = client.post(f"/api/v1/tasks/{auto_task['id']}/ai-fill", headers=headers, json={})
    assert r.status_code == 200, r.text
    run_id = r.json()["data"]["run"]["id"]
    deadline = time.time() + 8.0
    runs = []
    while time.time() < deadline:
        runs = client.get(f"/api/v1/tasks/{auto_task['id']}", headers=headers).json()["data"]["expertRuns"]
        run = next((x for x in runs if x["id"] == run_id), None)
        if run and run["status"] == "succeeded":
            break
        time.sleep(0.05)
    assert run and run["status"] == "succeeded"
    adopted = client.post(f"/api/v1/tasks/{auto_task['id']}/adopt-run", headers=headers, json={"run_id": run_id})
    assert adopted.status_code == 200, adopted.text
    values = adopted.json()["data"]["values"]
    r2 = client.post(f"/api/v1/tasks/{auto_task['id']}/actions", headers=headers,
                     json={"action": "submit", "form_values": values, "acceptance_checks": {"a1": {"text": "结论完整", "checked": True}}})
    assert r2.status_code == 200, r2.text

# ---------- 采纳 × AI 二次格式修正 ----------

def test_adopt_run_normalize_formats_and_guards(client, org_headers, fake_model):
    """normalize=true：采纳前用同一 Deployment 模型做格式修正——文本字段重排版、
    结构化字段修正前后值不一致时保留原值；run.parsed 打 normalized 标记（幂等不重复调模型）。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-norm")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({
        "conclusion": "原始结论", "verdict": "通过", "report": "# 原始报告\n正文",
    })
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI规范化-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded"
    run_id = runs[0]["id"]

    # 修正轮模型返回重排版后的产出（内容同源，仅排版变化）
    FakeChatOpenAI.payload = json.dumps({
        "conclusion": "## 分析结论\n\n### 一、范围\n范围已明确（重排版）",
        "verdict": "pass", "report": "# 分析报告\n\n| 项 | 值 |\n| --- | --- |\n| 结论 | 有条件可行 |",
    })
    r = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": run_id, "normalize": True})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["normalized"] is True, "格式修正应生效"
    values = data["values"]
    assert values["conclusion"].startswith("## 分析结论"), "文本字段应采用修正后的排版"
    assert values["report"][0]["id"], "upload 字段仍应转文档引用"

    # run.parsed 快照打标：详情可见 normalized + formattedAt
    snap = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"][0]["parsed"]
    assert snap.get("normalized") is True and snap.get("formattedAt")
    # 幂等：重复 normalize 采纳不再调模型（计数不增长）
    calls_after_first = FakeChatOpenAI.calls
    r2 = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": run_id, "normalize": True})
    assert r2.status_code == 200 and r2.json()["data"]["normalized"] is True
    assert FakeChatOpenAI.calls == calls_after_first, "已打标的快照不应重复触发格式修正"


def test_adopt_run_normalize_failure_falls_back(client, org_headers, fake_model):
    """格式修正失败（模型异常）→ 自动降级原解析采纳：请求成功、normalized=false、内容为原产出。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-normfb")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "降级前结论", "verdict": "通过", "report": "# 降级报告"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI规范降级-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded"

    FakeChatOpenAI.should_fail = True
    r = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={"run_id": runs[0]["id"], "normalize": True})
    FakeChatOpenAI.should_fail = False
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["normalized"] is False, "修正失败应降级为原解析"
    assert data["values"]["conclusion"] == "降级前结论", "降级后内容应为原始解析值"


def test_task_detail_next_task_id_after_auto_advance(client, org_headers, fake_model):
    """Expert 自动节点自动流转后，详情响应携带 nextTaskId 指向下一节点任务。"""
    from test_workflow_flows import _get_open_tasks as _tasks_of

    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-next")
    # 复用 _publish_flow_template（n2 为人工+Expert 协助）：这里直接构造自动节点流程
    tpl = client.post("/api/v1/templates", headers=headers, json={"name": f"AI自动跳转-{uuid.uuid4().hex[:6]}", "type": "requirement"}).json()["data"]["template"]
    nodes = [
        {"id": "n1", "label": "需求提交", "type": "start", "x": 24, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "START", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "",
                 "schema": [{"key": "title", "label": "标题", "type": "input", "required": True}]}},
        {"id": "n2", "label": "AI 自动处理", "type": "task", "x": 200, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "TASK", "purpose": "自动产出", "handler": "Expert 自动", "fallback": "", "sla": "24 小时", "output": "x",
                 "schema": [
                     {"key": "conclusion", "label": "分析结论", "type": "textarea", "required": True},
                     {"key": "report", "label": "分析报告", "type": "upload", "required": True},
                 ],
                 "deliverable": {"instruction": "生成分析", "acceptance": [{"key": "a1", "text": "结论完整"}], "aiGuidance": "", "example": ""},
                 "split": {"mode": "off"},
                 "expert": {"expertDeploymentId": dep_id}}},
        {"id": "n3", "label": "人工复核", "type": "task", "x": 376, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "TASK", "purpose": "复核", "handler": "人工 + Expert 可协助", "fallback": "", "sla": "", "output": "x",
                 "schema": [{"key": "remark", "label": "复核意见", "type": "textarea", "required": True}],
                 "deliverable": {}, "split": {"mode": "off"}, "expert": {}}},
        {"id": "n4", "label": "完成", "type": "end", "x": 552, "y": 24, "width": 118, "height": 56,
         "cfg": {"typeLine": "END", "purpose": "", "handler": "系统", "fallback": "", "sla": "", "output": "", "schema": []}},
    ]
    r = client.post(f"/api/v1/templates/{tpl['id']}/versions/save-and-publish", headers=headers,
                    json={"nodes": nodes, "edges": [["n1", "n2"], ["n2", "n3"], ["n3", "n4"]], "fallbacks": []})
    assert r.status_code == 200, r.text
    pid = _make_project(client, headers, tpl["id"], "v1", [])
    wi_id = _create_wi(client, headers, pid, tpl["id"], "AI自动跳转-E2E")

    FakeChatOpenAI.payload = json.dumps({"conclusion": "自动结论", "report": "# 自动报告"})
    auto_task = next(x for x in _tasks_of(client, headers, wi_id) if x["nodeId"] == "n2")
    # 等 run 完成 + 自动采纳流转（复用现有轮询）
    _wait_run_succeeded(client, headers, auto_task["id"])
    deadline = time.time() + 8
    while time.time() < deadline:
        detail = client.get(f"/api/v1/tasks/{auto_task['id']}", headers=headers).json()["data"]
        if detail["task"]["status"] == "completed":
            break
        time.sleep(0.05)
    assert detail["task"]["status"] == "completed", "自动节点应已自动采纳流转"
    assert detail["nextTaskId"], "完成后详情应携带 nextTaskId"
    nt = client.get(f"/api/v1/tasks/{detail['nextTaskId']}", headers=headers).json()["data"]
    assert nt["task"]["nodeId"] == "n3", "nextTaskId 应指向下一节点任务"
    assert nt["task"]["status"] != "completed"

def test_adopt_run_manual_values_override(client, org_headers, fake_model):
    """人工编辑优先：adopt-run 带 values（用户在抽屉中修订）→ 按编辑值覆盖快照直接采纳，
    跳过 AI 格式修正（模型不被调用）；快照打 manualEdited/editedKeys；upload 字段的人工改动被忽略。"""
    headers = org_headers
    model_id, dep_id = _publish_expert_with_deployment(client, headers, "fill-dep-manual")
    tpl_id = _publish_flow_template(client, headers, dep_id)
    pid = _make_project(client, headers, tpl_id, "v1", [])
    FakeChatOpenAI.payload = json.dumps({"conclusion": "原始结论", "verdict": "通过", "report": "# 原始报告"})
    wi_id = _create_wi(client, headers, pid, tpl_id, "AI编辑采纳-E2E")
    task_id = _get_open_tasks(client, headers, wi_id)[0]["id"]
    runs = _wait_run_succeeded(client, headers, task_id)
    assert runs and runs[0]["status"] == "succeeded"
    run_id = runs[0]["id"]

    # 用户修订 conclusion + 篡改一个 upload 字段（应被忽略）
    calls_before = FakeChatOpenAI.calls
    r = client.post(f"/api/v1/tasks/{task_id}/adopt-run", headers=headers, json={
        "run_id": run_id,
        "values": {"conclusion": "人工修订后的结论", "report": "人工改正文"},
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["manualEdited"] is True, "人工编辑采纳应打标"
    assert data["values"]["conclusion"] == "人工修订后的结论"
    assert FakeChatOpenAI.calls == calls_before, "人工编辑采纳不应触发 AI 格式修正模型调用"
    # upload 字段未接受人工值：仍按原正文走文档生成
    assert data["values"]["report"][0]["id"], "upload 字段应按快照正文生成文档引用"
    # 快照留痕：manualEdited + editedKeys（仅 conclusion）
    snap = client.get(f"/api/v1/tasks/{task_id}", headers=headers).json()["data"]["expertRuns"][0]["parsed"]
    assert snap.get("manualEdited") is True
    assert snap.get("editedKeys") == ["conclusion"]
    assert snap["values"]["conclusion"] == "人工修订后的结论"
    assert snap["originalValues"]["conclusion"] == "原始结论"
