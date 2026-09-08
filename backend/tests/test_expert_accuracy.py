"""Regression coverage for evidence-grounded Expert answers and task outputs."""

from io import BytesIO
from types import SimpleNamespace
import zipfile
from types import SimpleNamespace

import pytest

from flowhub_api.services.expert_runtime import (
    build_repair_instruction,
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
async def test_repo_tool_loop_stops_when_the_call_budget_is_exhausted():
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
    output, used = await run_repo_tool_loop(
        Model(), [("human", "查代码")], SimpleNamespace(tools=[Tool()], traces=[]), on_trace=trace.append, max_calls=1,
    )

    assert used == 1
    assert "安全上限" in output
    assert [item["status"] for item in trace] == ["succeeded", "blocked"]


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
