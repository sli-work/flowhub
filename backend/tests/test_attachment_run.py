"""LangGraph 集成：附件证据工具 Bundle + 质量校验。"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from flowhub_api.models import User
from flowhub_api.services import attachment_tools
from flowhub_api.services import attachment_evidence
from flowhub_api.services.attachment_parsers import EvidenceChunk, ParsedAttachment
from flowhub_api.services.expert_runtime import _attachment_state_from_bundle, check_attachment_citations


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema_and_seed(client):
    """触发 lifespan（建表 + seed demo 用户），供直接操作 SessionFactory 的用例使用。"""
    return client


def test_check_citations_no_attachment_word_ok():
    assert check_attachment_citations("这是普通分析结论。", []) == []


def test_check_citations_mentions_attachment_without_tool_flagged():
    issues = check_attachment_citations("根据附件 Q2 复盘，缺货 120 单。", [])
    assert issues and "附件" in issues[0]


def test_check_citations_mentions_attachment_with_valid_evidence_ok():
    trace = [{"tool": "flowhub_attachment_read", "status": "succeeded", "summary": "1 条结果"}]
    evidence = {"injected": [{"docName": "Q2复盘.pdf", "location": "p2", "seq": 1}]}
    assert check_attachment_citations("根据附件 Q2 复盘 [附件@Q2复盘.pdf:p2:1]，缺货 120 单。", trace, evidence) == []


def test_check_citations_rejects_list_only_or_forged_reference():
    evidence = {"injected": [{"docName": "Q2复盘.pdf", "location": "p2", "seq": 1}]}
    list_only = [{"tool": "flowhub_attachment_list", "status": "succeeded", "summary": "1 个附件"}]
    assert check_attachment_citations("根据附件得出结论。", list_only, evidence)
    searched = [{"tool": "flowhub_attachment_read", "status": "succeeded", "summary": "1 条结果"}]
    assert check_attachment_citations("根据附件 [附件@伪造.pdf:p9:9] 得出结论。", searched, evidence)


@pytest.mark.asyncio
async def test_attachment_tool_bundle_exposes_basic_read_tools(seeded, monkeypatch):
    session, task, admin, docs = seeded
    monkeypatch.setattr(attachment_evidence, "_load_bytes", lambda doc: "备件缺货 120 单\n补货周期 7 天".encode())
    bundle = await attachment_tools.create_attachment_tool_bundle(session, task, admin)
    names = {tool.name for tool in bundle.tools}
    assert {"flowhub_attachment_list", "flowhub_attachment_inspect", "flowhub_attachment_read", "flowhub_attachment_find", "flowhub_attachment_render"} == names

    listed = await next(t for t in bundle.tools if t.name == "flowhub_attachment_list").ainvoke({})
    assert "evd1" in listed and "evd4" not in listed, "含毒附件不应出现在目录"

    inspected = await next(t for t in bundle.tools if t.name == "flowhub_attachment_inspect").ainvoke({"doc_id": "evd3"})
    assert "locations" in inspected
    found = await next(t for t in bundle.tools if t.name == "flowhub_attachment_read").ainvoke({"doc_id": "evd3", "location": "p1"})
    assert "[附件@" in found and "备件缺货 120 单" in found
    assert bundle.injected, "检索片段应累计到 bundle"
    assert any(trace["tool"] == "flowhub_attachment_read" for trace in bundle.traces)


@pytest.mark.asyncio
async def test_attachment_tool_loop_drives_read_and_backfills_state(seeded, monkeypatch):
    """合并工具循环接缝：stub llm 驱动 run_repo_tool_loop 调用附件搜索 → trace 出现 → 回填状态非空。"""
    from sqlalchemy import select

    from flowhub_api.db.session import SessionFactory
    from flowhub_api.models.workflow import TaskItem, WorkItem
    from flowhub_api.models.support import DocItem
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    async with SessionFactory() as session:
        wi = WorkItem(id="WI-LOOP-001", type="issue", title="备件盘点", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-LOOP-001", wi_id=wi.id, title="分析备件盘点", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        doc = DocItem(id="d-loop", name="备件清单.txt", project="售后", scan="已扫描",
                      uploader="张三", size="1KB", time="t-loop", wi=wi.id, object_name="d-loop/x.txt")
        session.add(wi); session.add(task); session.add(doc)
        wi.start_values = {"attach": [{"id": doc.id, "name": doc.name}]}
        await session.commit()
        admin = (await session.execute(select(User).where(User.account == "liting"))).scalars().first()
        try:
            monkeypatch.setattr(attachment_evidence, "_load_bytes",
                                lambda d: "备件缺货 120 单\n补货周期 7 天".encode())
            bundle = await attachment_tools.create_attachment_tool_bundle(session, task, admin)

            class Model:
                def __init__(self):
                    self.calls = 0

                def bind_tools(self, tools):
                    assert any(t.name == "flowhub_attachment_read" for t in tools)
                    return self

                async def ainvoke(self, messages):
                    self.calls += 1
                    if self.calls == 1:
                        return SimpleNamespace(content="", tool_calls=[
                            {"id": "c1", "name": "flowhub_attachment_read", "args": {"doc_id": doc.id, "location": "p1"}}])
                    return SimpleNamespace(content="基于附件得出结论：备件缺货 120 单。", tool_calls=[])

            trace = []
            output, used = await run_repo_tool_loop(
                Model(), [("human", "根据附件分析备件情况")], bundle, on_trace=trace.append)
            assert used == 1
            assert "备件缺货 120 单" in output
            assert any(item.get("tool") == "flowhub_attachment_read" for item in trace)
            assert bundle.injected, "search 调用后应回填 injected"
            state = _attachment_state_from_bundle(bundle)
            assert state["injected"] and state["totalChars"] > 0, "loop 后回填 attachmentEvidence 非空"
        finally:
            await session.delete(doc)
            await session.delete(task)
            await session.delete(wi)
            await session.commit()


def test_attachment_state_from_bundle_after_search():
    """search 调用后（parsed/injected 已由工具回填）→ attachmentEvidence 摘要非空。"""
    ab = attachment_tools.AttachmentToolBundle(
        tools=[],
        duration_ms=37,
        candidates=[{"id": "evd1", "name": "Q2复盘.pdf", "ext": "pdf", "kind": ""}],
        parsed=[ParsedAttachment(doc_id="evd1", status="indexed", parser="pdf_ocr", cache_hit=True,
                                 duration_ms=120, entries_or_pages=3, error="")],
        injected=[
            EvidenceChunk(doc_id="evd1", doc_name="Q2复盘.pdf", location="p2", seq=1,
                          kind="text", text="备件缺货 120 单"),
            EvidenceChunk(doc_id="evd2", doc_name="备件清单.xlsx", location="Sheet1!A1", seq=2,
                          kind="table", text="补货周期 7 天"),
        ],
    )
    state = _attachment_state_from_bundle(ab)
    assert state["parsed"] and state["injected"] and state["totalChars"] > 0
    first = state["injected"][0]
    assert first["docId"] == "evd1" and first["docName"] == "Q2复盘.pdf"
    assert first["location"] == "p2" and first["seq"] == 1
    assert state["durationMs"] == 37


@pytest.mark.asyncio
async def test_attachment_tool_bundle_permission_denied_403(seeded):
    session, task, admin, _ = seeded
    outsider = User(id="u-none", name="局外人", account="outsider", roles=[])
    with pytest.raises(Exception) as exc:
        await attachment_tools.create_attachment_tool_bundle(session, task, outsider)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_mcp_attachment_tools_are_stepwise(seeded, monkeypatch):
    from flowhub_api.services import attachment_evidence as svc
    from flowhub_api.services import mcp_server

    session, task, admin, _ = seeded
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "备件缺货 120 单".encode())
    token = mcp_server._current_user.set(admin)
    try:
        listed = await mcp_server.attachment_list(task.id)
        inspected = await mcp_server.attachment_inspect(task.id, "evd3")
        read = await mcp_server.attachment_read(task.id, "evd3", "p1")
        downloaded = await mcp_server.attachment_download(task.id, "evd1")
        rejected = await mcp_server.attachment_download(task.id, "evd4")
    finally:
        mcp_server._current_user.reset(token)
    assert "evd1" in listed
    assert "locations" in inspected
    assert "[附件@" in read
    assert downloaded["id"] == "evd1"
    assert downloaded["downloadPath"].startswith("/api/v1/documents/evd1/content?token=")
    assert downloaded["expiresIn"] == "5 分钟"
    from flowhub_api.services.document_access import valid_document_content_token
    token_value = parse_qs(urlsplit(downloaded["downloadPath"]).query)["token"][0]
    assert valid_document_content_token(token_value, "evd1")
    assert not valid_document_content_token(token_value, "evd2")
    assert "error" in rejected

@pytest.mark.asyncio
async def test_attachment_inspect_is_paginated_metadata_only(seeded, monkeypatch):
    session, task, admin, _ = seeded
    monkeypatch.setattr(attachment_evidence, "_load_bytes", lambda doc: b"one\ntwo\nthree")
    bundle = await attachment_tools.create_attachment_tool_bundle(session, task, admin)
    inspect = next(tool for tool in bundle.tools if tool.name == "flowhub_attachment_inspect")
    page = await inspect.ainvoke({"doc_id": "evd3", "offset": 0, "limit": 1})
    assert '"preview"' not in page
    assert '"offset": 0' in page and '"hasMore": true' in page


@pytest.mark.asyncio
async def test_tool_loop_reports_the_budget_that_is_exhausted():
    from flowhub_api.services.expert_runtime import run_repo_tool_loop

    class Tool:
        name = "large_result"

        async def ainvoke(self, args):
            return "x" * 100

    class Model:
        calls = 0

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="", tool_calls=[{"id": "large", "name": "large_result", "args": {}}])
            return SimpleNamespace(content="总结", tool_calls=[])

    trace = []
    output, used = await run_repo_tool_loop(
        Model(), [("human", "test")], SimpleNamespace(tools=[Tool()], traces=[]),
        on_trace=trace.append, max_tool_tokens=10,
    )
    assert used == 1 and output == "总结"
    assert "token 预算已达 10/10" in trace[-1]["summary"]
