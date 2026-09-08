"""LangGraph 集成：附件证据工具 Bundle + 质量校验。"""
import pytest

from flowhub_api.services import attachment_tools
from flowhub_api.services.expert_runtime import check_attachment_citations


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema_and_seed(client):
    """触发 lifespan（建表 + seed demo 用户），供直接操作 SessionFactory 的用例使用。"""
    return client


def test_check_citations_no_attachment_word_ok():
    assert check_attachment_citations("这是普通分析结论。", []) == []


def test_check_citations_mentions_attachment_without_tool_flagged():
    issues = check_attachment_citations("根据附件 Q2 复盘，缺货 120 单。", [])
    assert issues and "附件" in issues[0] and "flowhub" in issues[0]


def test_check_citations_mentions_attachment_with_tool_ok():
    trace = [{"tool": "flowhub_attachment_search", "status": "succeeded", "summary": "3 条结果"}]
    assert check_attachment_citations("根据附件 Q2 复盘，缺货 120 单。", trace) == []


@pytest.mark.asyncio
async def test_attachment_tool_bundle_exposes_list_and_search(seeded, monkeypatch):
    session, task, admin, docs = seeded
    monkeypatch.setattr(attachment_tools, "_load_bytes", lambda doc: "备件缺货 120 单\n补货周期 7 天".encode())
    bundle = await attachment_tools.create_attachment_tool_bundle(session, task, admin)
    names = {tool.name for tool in bundle.tools}
    assert names == {"flowhub_attachment_list", "flowhub_attachment_search"}

    listed = await next(t for t in bundle.tools if t.name == "flowhub_attachment_list").ainvoke({})
    assert "evd1" in listed and "evd4" not in listed, "含毒附件不应出现在目录"

    found = await next(t for t in bundle.tools if t.name == "flowhub_attachment_search").ainvoke({"query": "备件"})
    assert "[附件@" in found and "备件缺货 120 单" in found
    assert bundle.injected, "检索片段应累计到 bundle"
    assert any(trace["tool"] == "flowhub_attachment_search" for trace in bundle.traces)


@pytest.mark.asyncio
async def test_attachment_search_depth_limit(seeded):
    session, task, admin, docs = seeded
    bundle = await attachment_tools.create_attachment_tool_bundle(session, task, admin)
    out = await next(t for t in bundle.tools if t.name == "flowhub_attachment_search").ainvoke({"query": "备件", "depth": 99})
    assert "深度超限" in out
