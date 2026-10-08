"""Regression coverage for evidence-grounded Expert answers and task outputs."""

from io import BytesIO
from types import SimpleNamespace
import zipfile
from types import SimpleNamespace

import pytest

from flowhub_api.services.expert_runtime import (
    build_repair_instruction,
    compact_schema_retry_messages,
    code_evidence_issues,
    extract_entity_ids,
    _skill_markdown_from_archive,
    parse_quality_review,
    schema_validation_issues,
    should_retry_judge,
)


def test_repair_instruction_keeps_the_previous_answer_and_judge_findings():
    instruction = build_repair_instruction(
        "REQ-42 已完成。",
        ["REQ-42 的状态与证据冲突", "缺少风险说明"],
    )

    assert "REQ-42 已完成。" in instruction
    assert "REQ-42 的状态与证据冲突" in instruction
    assert "缺少风险说明" in instruction


def test_invalid_judge_payload_is_observable_and_requires_repair():
    passed, issues, status = parse_quality_review("not-json")

    assert not passed
    assert status == "unavailable"
    assert issues


def test_judge_protocol_failures_retry_the_judge_not_the_answer_model():
    assert should_retry_judge("unavailable", 1)
    assert not should_retry_judge("unavailable", 2)
    assert not should_retry_judge("needs_revision", 1)


def test_code_answer_requires_a_pinned_evidence_citation():
    evidence = "【代码证据｜orders/api｜commit abcdef123456】\n命中符号：create_order（function｜src/orders.py:L12）"
    assert code_evidence_issues("create_order 的调用关系", evidence, "它会发送邮件")
    assert not code_evidence_issues("create_order 的调用关系", evidence, "它会发送邮件 [orders/api@abcdef123456:src/orders.py:L12 create_order]")


def test_code_answer_explicitly_handles_projects_without_a_repository():
    evidence = "【关联代码仓库】未绑定代码仓库；不能据此分析实现、调用关系或修改影响。"
    assert code_evidence_issues("这个项目代码怎么改", evidence, "建议修改 service")
    assert not code_evidence_issues("这个项目代码怎么改", evidence, "该项目未绑定代码仓库；以下基于任务上下文分析，未经过代码实现验证。")


def test_repo_backed_delivery_plan_cannot_pass_with_a_generic_template():
    evidence = "【代码证据｜orders/api｜commit abcdef123456】\n命中符号：create_order（function｜src/orders.py:L12）"

    issues = code_evidence_issues(
        "根据仓库给出本次开发范围、实施方案和冒烟用例",
        evidence,
        "## 项目范围\n- 功能1：用户登录\n- 功能2：商品列表\n\n## 项目用例\n采购后生成文档",
    )

    assert issues == ["代码结论缺少 [repo@commit:file:L行号 symbol] 证据引用。"]


def test_repo_forensics_gate_only_targets_code_analysis_tasks():
    from flowhub_api.services.expert_runtime import is_code_analysis_question

    # 代码型任务：必须做仓库取证
    assert is_code_analysis_question("分析 create_order 函数调用链，给出改造方案")
    assert is_code_analysis_question("源码里这个 bug 怎么修")
    # 非代码任务：附件/文档/方案类，即便项目绑定仓库也不得强制仓库取证
    assert not is_code_analysis_question("读取附件《重庆数据局OSM适配新指标和接口计划.xlsx》并回填计划字段")
    assert not is_code_analysis_question("根据附件给出本次采购的验收方案文档")
    assert not is_code_analysis_question("为这个需求生成项目计划与里程碑")


def test_task_brief_boilerplate_repository_section_does_not_trigger_code_forensics():
    """任务书对任何绑定仓库的项目都会自动附加「## 关联代码仓库」段（含「仓库」二字），
    该样板文字不得把非代码任务误判为代码任务而触发仓库取证门禁。"""
    from flowhub_api.services.expert_runtime import is_code_analysis_question

    brief = (
        "# 任务书：AI 产出\n\n## 节点目的\n生成分析产出\n\n"
        "## 需要填写的产出字段\n- 分析结论（conclusion，必填）\n\n"
        "## 关联代码仓库\n【关联代码仓库】\n  - orders/api（核心）：模块 src/orders\n"
    )
    assert not is_code_analysis_question(brief)
    # 真实的代码任务：需求本身写在正文段落，样板仓库段仍在末尾，必须取证。
    code_brief = (
        "# 任务书：AI 产出\n\n## 产出要求\n分析 create_order 的调用链并给出重构方案\n\n"
        "## 关联代码仓库\n【关联代码仓库】\n  - orders/api（核心）：模块 src/orders\n"
    )
    assert is_code_analysis_question(code_brief)


def test_failure_kind_classifies_recoverable_and_provider_failures():
    from flowhub_api.services.expert_runtime import _failure_kind_for_exception
    from flowhub_api.services.context_budget import ContextBudgetExceeded

    class LengthFinishReasonError(RuntimeError):
        pass

    assert _failure_kind_for_exception(ContextBudgetExceeded("必要任务上下文超过 Provider 上下文窗口")) == "context_budget"
    assert _failure_kind_for_exception(LengthFinishReasonError("finish_reason=length")) == "length_limited"
    assert _failure_kind_for_exception(ValueError("旧运行缺少可靠配置快照")) == "configuration"
    assert _failure_kind_for_exception(RuntimeError("502 bad gateway")) == "provider_error"


def test_compact_schema_retry_keeps_required_field_contract():
    messages = [("system", "原始系统提示"), ("human", "为当前任务生成产出")]
    compact = compact_schema_retry_messages([
        {"key": "plan", "label": "任务计划", "type": "textarea", "required": True},
        {"key": "priority", "label": "优先级", "type": "select", "required": True,
         "options": [{"label": "高", "value": "high"}]},
    ], messages)

    assert "plan（任务计划，必填" in compact[0][1]
    assert "priority（优先级，必填" in compact[0][1]
    assert "当前任务" in compact[1][1]


@pytest.mark.asyncio
async def test_task_snapshot_uses_only_the_inherited_work_item_context():
    from flowhub_api.services.expert_runtime import flowhub_read_snapshot

    class Session:
        async def execute(self, statement):
            raise AssertionError("节点 Expert 不应查询当前用户全部任务")

    result = await flowhub_read_snapshot(
        Session(), SimpleNamespace(name="李松", roles=[]), "生成开发方案",
        task_context="【工作项信息】\n当前工作项及其已完成前序节点",
    )

    assert result["context"] == "【工作项信息】\n当前工作项及其已完成前序节点"
    assert result["pre_answer"] == ""
    assert result["trace"][0]["tool"] == "flowhub.task.context"


def test_quality_review_accepts_only_an_explicit_clean_pass():
    assert parse_quality_review('{"pass": true, "issues": []}') == (True, [], "passed")
    passed, issues, status = parse_quality_review('{"pass": true, "issues": ["证据不足"]}')
    assert not passed
    assert status == "needs_revision"
    assert issues == ["证据不足"]


def test_exact_entity_id_extraction_includes_issue_ids():
    assert extract_entity_ids("请核查 ISSUE-2026-0520 及 req-42 的关联任务") == {
        "ISSUE-2026-0520", "REQ-42",
    }


def test_skill_archive_is_read_in_memory_without_extracting_files():
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("review/SKILL.md", "# Review\n只依据证据输出结论")

    assert _skill_markdown_from_archive(archive.getvalue(), "zip") == "# Review\n只依据证据输出结论"


def test_budgeted_model_can_cap_tool_selection_output_without_changing_the_model():
    from flowhub_api.services.context_budget import ContextBudget
    from flowhub_api.services.runtime_model import BudgetedModel

    class Inner:
        def __init__(self, options=None):
            self.options = options or {}

        def bind(self, **options):
            return Inner({**self.options, **options})

    constrained = BudgetedModel(Inner(), ContextBudget(32_000, 4_096, "test-model")).with_max_output_tokens(1_024)

    assert constrained.inner.options["max_tokens"] == 1_024
    assert constrained.budget.output_tokens == 1_024
    assert constrained.budget.context_tokens == 32_000


def test_task_model_omits_request_output_cap_when_provider_owns_the_limit():
    from flowhub_api.services.runtime_model import make_model

    class Factory:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    result = make_model(
        Factory,
        SimpleNamespace(model="test-model", max_context_tokens=32_000, max_output_tokens=4_096),
        SimpleNamespace(base_url="https://example.test", max_context_tokens=32_000),
        "test-key",
        send_max_tokens=False,
    )

    assert "max_tokens" not in result.inner.kwargs


def test_empty_optional_schema_output_is_invalid_for_expert_delivery():
    from flowhub_api.services.expert_runtime import expert_schema_output_issues

    issues = expert_schema_output_issues(
        [{"key": "smoke_scope", "label": "冒烟范围", "type": "textarea", "required": False}],
        {},
    )

    assert issues == ["模型未生成任何可回填字段"]


@pytest.mark.asyncio
async def test_repo_tool_loop_returns_tool_evidence_to_the_model_and_records_trace():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            assert args == {"query": "create_order"}
            return '{"repository":"orders/api","commit":"abcdef123456","evidence":"src/orders.py:L12 def create_order"}'

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            assert tools[0].name == "repo_search"
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="", tool_calls=[{"id": "call-1", "name": "repo_search", "args": {"query": "create_order"}}])
            assert any(getattr(message, "tool_call_id", "") == "call-1" for message in messages)
            return SimpleNamespace(content="create_order 位于 orders.py。 [orders/api@abcdef123456:src/orders.py:L12 create_order]", tool_calls=[])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "create_order 在哪里")], SimpleNamespace(tools=[Tool()], traces=[]),
        on_trace=trace.append,
    )

    assert "[orders/api@abcdef123456:src/orders.py:L12 create_order]" in output
    assert used == 1
    assert trace[0]["tool"] == "repo_search"
    assert trace[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_repo_tool_loop_continues_beyond_legacy_call_budget_until_model_finishes():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "evidence"

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return SimpleNamespace(
                content="",
                tool_calls=[{"id": f"call-{self.calls}", "name": "repo_search", "args": {"query": "x"}}],
            )

    trace = []
    class FinishingModel(Model):
        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls > 10:
                return SimpleNamespace(content="已完成十次取证后的结论", tool_calls=[])
            return SimpleNamespace(
                content="",
                tool_calls=[{"id": f"call-{self.calls}", "name": "repo_search", "args": {"query": "x"}}],
            )

    output, used = await run_repo_tool_loop(
        FinishingModel(), [("human", "查代码")], SimpleNamespace(tools=[Tool()], traces=[]), on_trace=trace.append,
    )

    assert used == 10
    assert output == "已完成十次取证后的结论"
    assert all(item["status"] == "succeeded" for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_semantically_compacts_at_provider_context_window():
    from flowhub_api.services.context_budget import ContextBudget
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "evidence " * 80

    class Model:
        def __init__(self):
            self.calls = 0
            self.budget = ContextBudget(1_200, 100, "")

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            if any("压缩工具取证" in str(message) for message in messages):
                return SimpleNamespace(content="保留：repo_search 已读取与问题相关的代码证据。", tool_calls=[])
            self.calls += 1
            if self.calls > 12:
                return SimpleNamespace(content="基于压缩后的证据完成结论", tool_calls=[])
            return SimpleNamespace(content="", tool_calls=[{
                "id": f"call-{self.calls}", "name": "repo_search", "args": {"query": "x"},
            }])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "查代码")], SimpleNamespace(tools=[Tool()], traces=[]), on_trace=trace.append,
    )

    assert used == 12
    assert output == "基于压缩后的证据完成结论"
    assert any(item["tool"] == "flowhub.repo.context_compaction" for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_recovers_from_model_length_finish_with_a_plain_summary():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "src/orders.py:L12 def create_order"

    class ToolBoundModel:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="", tool_calls=[{
                    "id": "call-1", "name": "repo_search", "args": {"query": "create_order"},
                }])
            raise RuntimeError("LengthFinishReasonError")

    class Model:
        def __init__(self):
            self.tool_model = ToolBoundModel()
            self.summary_messages = []

        def bind_tools(self, tools):
            return self.tool_model

        async def ainvoke(self, messages):
            self.summary_messages = messages
            return SimpleNamespace(content="基于已读取的代码证据完成总结。", tool_calls=[])

    model = Model()
    trace = []
    output, used = await run_repo_tool_loop(
        model, [("human", "查 create_order")], SimpleNamespace(tools=[Tool()], traces=[]), on_trace=trace.append,
    )

    assert used == 1
    assert "Provider 输出长度上限" in output
    assert any("长度上限" in item["summary"] for item in trace)
    assert model.summary_messages == []


@pytest.mark.asyncio
async def test_repo_tool_loop_recovers_when_gateway_returns_length_finish_reason():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class ToolBoundModel:
        async def ainvoke(self, messages):
            return SimpleNamespace(content="截断内容", tool_calls=[], response_metadata={"finish_reason": "length"})

    class Model:
        def bind_tools(self, tools):
            return ToolBoundModel()

        async def ainvoke(self, messages):
            return SimpleNamespace(content="基于已有证据的简短总结", tool_calls=[])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "查代码")], SimpleNamespace(tools=[SimpleNamespace(name="repo_search")], traces=[]),
        on_trace=trace.append,
    )

    assert used == 0
    assert output == "截断内容"
    assert any("长度上限" in item["summary"] for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_requires_one_repository_read_for_structured_delivery():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "src/orders.py:L12 def create_order"

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="通用模板", tool_calls=[])
            if self.calls == 2:
                return SimpleNamespace(content="", tool_calls=[{
                    "id": "call-1", "name": "repo_search", "args": {"query": "create_order"},
                }])
            return SimpleNamespace(content="基于仓库证据的交付结果", tool_calls=[])

    output, used = await run_repo_tool_loop(
        Model(), [("human", "生成开发方案")], SimpleNamespace(tools=[Tool()], traces=[]),
        require_tool_evidence=True,
    )

    assert used == 1
    assert output == "基于仓库证据的交付结果"


@pytest.mark.asyncio
async def test_repo_tool_loop_marks_blocked_but_keeps_draft_when_model_ignores_evidence_reminder():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return SimpleNamespace(content="通用模板", tool_calls=[])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "生成开发方案")], SimpleNamespace(tools=[Tool()], traces=[]),
        require_tool_evidence=True, on_trace=trace.append,
    )

    # 未取证是复核信号：保留模型已产出的草稿，只记 blocked，不再替换为硬失败提示。
    assert used == 0
    assert output == "通用模板"
    assert any(item["status"] == "blocked" and "未完成最小仓库取证" in item["summary"] for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_does_not_treat_attachment_or_failed_read_as_repository_evidence():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class AttachmentTool:
        name = "flowhub_attachment_read"

        async def ainvoke(self, args):
            return "attachment text"

    class FailedRepoTool:
        name = "repo_search"

        async def ainvoke(self, args):
            raise RuntimeError("mirror unavailable")

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="", tool_calls=[
                    {"id": "attachment", "name": "flowhub_attachment_read", "args": {}},
                    {"id": "repo", "name": "repo_search", "args": {"query": "create_order"}},
                ])
            return SimpleNamespace(content="通用模板", tool_calls=[])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "生成开发方案")],
        SimpleNamespace(tools=[AttachmentTool(), FailedRepoTool()], traces=[]), require_tool_evidence=True,
        on_trace=trace.append,
    )

    assert used == 2
    assert any(item["status"] == "blocked" and "未完成最小仓库取证" in item["summary"] for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_rejects_structured_delivery_when_length_stops_before_evidence():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("LengthFinishReasonError")

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "生成开发方案")], SimpleNamespace(tools=[Tool()], traces=[]),
        require_tool_evidence=True, on_trace=trace.append,
    )

    # 长度截断仍未取证：记录 blocked，且不再以硬失败提示掩盖真实原因。
    assert used == 0
    assert any(item["status"] == "blocked" and "未完成最小仓库取证" in item["summary"] for item in trace)


@pytest.mark.asyncio
async def test_repo_tool_loop_keeps_graphify_preflight_evidence_without_spending_a_tool_call():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            assert any("Graphify 预分析" in str(message) for message in messages)
            return SimpleNamespace(content="基于图谱回答", tool_calls=[])

    output, used = await run_repo_tool_loop(
        Model(), [("system", "Graphify 预分析：命中 create_order")],
        SimpleNamespace(tools=[SimpleNamespace(name="repo_search")], traces=[]),
    )

    assert output == "基于图谱回答"
    assert used == 0


@pytest.mark.asyncio
async def test_repo_tool_loop_stops_at_the_tool_round_ceiling():
    """工具封顶后必须基于已取证内容进行一次无工具的最终生成。"""
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "src/orders.py:L12 def create_order"

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if any("工具预算已用尽" in str(message) for message in messages):
                return SimpleNamespace(content="基于 src/orders.py:L12 的测试方案", tool_calls=[])
            return SimpleNamespace(content="", tool_calls=[{
                "id": f"call-{self.calls}", "name": "repo_search", "args": {"query": "x"},
            }])

    model = Model()
    trace = []
    output, used = await run_repo_tool_loop(
        model, [("human", "查代码")], SimpleNamespace(tools=[Tool()], traces=[]),
        on_trace=trace.append, max_rounds=3,
    )

    assert used == 3
    assert model.calls == 4, "3 轮工具调用后，直接在禁用工具的第 4 次调用中生成最终结论"
    assert output == "基于 src/orders.py:L12 的测试方案"
    assert trace[-1]["tool"] == "flowhub.repo.tools"
    assert trace[-1]["status"] == "blocked"
    assert "最终生成" in trace[-1]["summary"]


@pytest.mark.asyncio
async def test_repo_tool_loop_never_executes_more_than_the_tool_call_ceiling():
    """单个模型响应包含大量调用时，任务分析仍必须限制实际工具执行数。"""
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        def __init__(self):
            self.calls = 0

        async def ainvoke(self, args):
            self.calls += 1
            return f"evidence-{self.calls}"

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if any("工具预算已用尽" in str(message) for message in messages):
                return SimpleNamespace(content="基于已读取证据完成交付", tool_calls=[])
            return SimpleNamespace(content="", tool_calls=[
                {"id": f"call-{index}", "name": "repo_search", "args": {"query": str(index)}}
                for index in range(5)
            ])

    tool = Tool()
    output, used = await run_repo_tool_loop(
        Model(), [("human", "查代码")], SimpleNamespace(tools=[tool], traces=[]),
        max_calls=2,
    )

    assert used == 2
    assert tool.calls == 2
    assert output == "基于已读取证据完成交付"


@pytest.mark.asyncio
async def test_repo_tool_loop_tool_round_ceiling_does_not_bound_default_runs():
    """未传 max_rounds（对话式/AiChat）时保持原有的无上限行为。"""
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "repo_search"

        async def ainvoke(self, args):
            return "evidence"

    class Model:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls > 15:
                return SimpleNamespace(content="十五轮后的结论", tool_calls=[])
            return SimpleNamespace(content="", tool_calls=[{
                "id": f"call-{self.calls}", "name": "repo_search", "args": {"query": "x"},
            }])

    output, used = await run_repo_tool_loop(
        Model(), [("human", "查代码")], SimpleNamespace(tools=[Tool()], traces=[]),
    )

    assert used == 15
    assert output == "十五轮后的结论"


@pytest.mark.asyncio
async def test_quality_failure_blocks_the_shared_auto_fill_path():
    """ai_autosubmit and manual adoption share this gate; normalize must not erase it."""
    from flowhub_api.services.workflow import WorkflowService

    run = SimpleNamespace(output='{"conclusion":"字段格式正确"}', parsed={
        "values": {"conclusion": "字段格式正确"},
        "warnings": [],
        "valid": True,
        "validationIssues": [],
        "qualityStatus": "needs_human_review",
        "qualityIssues": ["无证据支撑结论"],
    })
    task = SimpleNamespace()

    values, warnings = await WorkflowService(None).fill_task_from_run(
        task, run, {"schema": [{"key": "conclusion", "required": True}]}, SimpleNamespace(),
    )

    assert values == {}
    assert "无证据支撑结论" in warnings


@pytest.mark.asyncio
async def test_legacy_full_text_snapshot_is_revalidated_before_adoption():
    """Old parsed snapshots may contain the removed full-text fallback and must be rejected."""
    from flowhub_api.services.workflow import WorkflowService

    run = SimpleNamespace(
        output="## 原始 Markdown\n不是 JSON",
        parsed={"values": {"conclusion": "## 原始 Markdown\n不是 JSON"}, "warnings": [], "valid": True},
    )
    values, warnings = await WorkflowService(None).fill_task_from_run(
        SimpleNamespace(), run,
        {"schema": [{"key": "conclusion", "label": "结论", "type": "textarea", "required": True}]},
        SimpleNamespace(),
    )

    assert values == {}
    assert any("不是有效 JSON" in warning for warning in warnings)


def test_schema_validation_rejects_missing_required_values_and_invalid_date():
    schema = [
        {"key": "conclusion", "label": "结论", "type": "textarea", "required": True},
        {"key": "verdict", "label": "结论状态", "type": "select", "required": True,
         "options": [{"label": "通过", "value": "pass"}]},
        {"key": "tags", "label": "标签", "type": "multiselect", "required": True,
         "options": [{"label": "高优", "value": "p0"}]},
        {"key": "due", "label": "截止日期", "type": "date", "required": True},
    ]

    issues = schema_validation_issues(
        schema,
        {"conclusion": " ", "verdict": "unknown", "tags": [], "due": "2026/09/04"},
    )

    assert any("结论」缺少" in issue for issue in issues)
    assert any("结论状态」不在可选项内" in issue for issue in issues)
    assert any("标签」缺少" in issue for issue in issues)
    assert any("截止日期」不是 YYYY-MM-DD" in issue for issue in issues)


def test_schema_validation_accepts_complete_typed_output():
    schema = [
        {"key": "conclusion", "label": "结论", "type": "textarea", "required": True},
        {"key": "verdict", "label": "结论状态", "type": "select", "required": True,
         "options": [{"label": "通过", "value": "pass"}]},
        {"key": "tags", "label": "标签", "type": "multiselect", "required": True,
         "options": [{"label": "高优", "value": "p0"}]},
        {"key": "due", "label": "截止日期", "type": "date", "required": True},
    ]

    assert schema_validation_issues(
        schema,
        {"conclusion": "证据充分", "verdict": "pass", "tags": ["p0"], "due": "2026-09-04"},
    ) == []

@pytest.mark.asyncio
async def test_repo_tool_loop_classifies_model_type_errors_as_unsupported_tool_protocol():
    """OpenAI-compatible gateway may accept bind_tools but reject the tool request at invoke time."""
    from flowhub_api.services.expert_runtime import RepoToolLoopUnavailable, run_repo_tool_loop

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            raise TypeError("unsupported tools payload")

    with pytest.raises(RepoToolLoopUnavailable, match="工具调用协议"):
        await run_repo_tool_loop(
            Model(), [("human", "分析代码")], SimpleNamespace(tools=[SimpleNamespace(name="repo_search")]),
        )


def test_ai_output_never_turns_image_field_text_into_a_document_candidate():
    from flowhub_api.services.expert_runtime import parse_schema_output

    values, warnings = parse_schema_output(
        [{"key": "photos", "label": "现场图片", "type": "image", "required": True}],
        '{"photos": "模型不能生成的图片说明"}',
    )

    assert values == {}
    assert warnings == ["「现场图片」需由人工上传图片"]


def test_quality_review_accepts_json_wrapped_by_gateway_text():
    from flowhub_api.services.expert_runtime import parse_quality_review

    assert parse_quality_review('核验结果如下：\n{"pass": true, "issues": []}\n以上。') == (True, [], 'passed')


def test_graph_state_keeps_attachment_evidence_for_citation_checks():
    """GraphState 必须声明 attachmentEvidence；否则 LangGraph 丢弃该键，
    check_attachment_citations 永远拿不到证据，只要产出提到附件就误报「未检索到附件证据」。"""
    from flowhub_api.services.expert_runtime import GraphState

    assert "attachmentEvidence" in GraphState.__annotations__, "GraphState 必须声明 attachmentEvidence"


@pytest.mark.asyncio
async def test_graph_propagates_attachment_evidence_to_the_result(monkeypatch):
    """端到端：model_node 返回的 attachmentEvidence 必须能到达 graph 输出。"""
    from flowhub_api.services import expert_runtime
    from langchain_core.tools import StructuredTool

    class Model:
        def __init__(self, **kw): pass

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            return SimpleNamespace(content='{"conclusion": "根据附件 [附件@备件.pdf:p1:1]，缺货 120 单"}', tool_calls=[])

    async def fake_read(doc_id: str = "evd1", location: str = "p1") -> str:
        return "[附件@备件.pdf:p1:1]\n备件缺货 120 单"

    tool = StructuredTool.from_function(coroutine=fake_read, name="flowhub_attachment_read",
                                        description="read a location from an attachment")
    injected = [SimpleNamespace(doc_id="evd1", doc_name="备件.pdf", location="p1", seq=1, kind="text", text="备件缺货 120 单")]

    async def attachment_factory(prompt):
        return SimpleNamespace(
            tools=[tool], traces=[], evidence_context=[],
            candidates=[], parsed=[], injected=list(injected), operations=[], duration_ms=0,
        )

    monkeypatch.setattr(expert_runtime, "ChatOpenAI", Model)
    monkeypatch.setattr(expert_runtime, "decrypt_secret", lambda value: value)
    graph = expert_runtime.build_graph(
        SimpleNamespace(api_key="encrypted", base_url="", max_context_tokens=None),
        SimpleNamespace(model="test-model", max_context_tokens=None, max_output_tokens=None),
        "填写表单", quality_mode="fast",
        output_schema=[{"key": "conclusion", "label": "结论", "type": "textarea", "required": True}],
        attachment_tools_factory=attachment_factory,
    ).compile()

    result = await graph.ainvoke({"prompt": "根据附件生成结论"})

    assert result.get("attachmentEvidence") is not None, "attachmentEvidence 必须随 graph 状态保留"
