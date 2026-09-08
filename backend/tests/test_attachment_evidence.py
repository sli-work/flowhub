"""附件证据服务：意图选择 / 权限与安全过滤 / 解析缓存 / 检索注入 / 渲染。"""
import pytest

from flowhub_api.models import User
from flowhub_api.services import attachment_evidence as svc


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema_and_seed(client):
    """触发 lifespan（建表 + seed demo 用户），供直接操作 SessionFactory 的用例使用。"""
    return client


def _pdf_bytes(text: str) -> bytes:
    import fitz

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text, fontname="china-s")
    data = pdf.tobytes()
    pdf.close()
    return data


def _xlsx_bytes() -> bytes:
    from io import BytesIO

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["月份", "sku", "qty"])
    ws.append(["4月", "A-1", "42"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_intent_selects_relevant_attachments(seeded, monkeypatch):
    session, task, admin, docs = seeded
    contents = {
        "evd1": _pdf_bytes("Q2 服务复盘：备件缺货 120 单"),
        "evd2": _xlsx_bytes(),
        "evd3": "无关内容".encode(),
    }
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: contents.get(doc.id, b""))

    result = await svc.build_attachment_evidence(
        session, task, admin, question="分析备件库存看板，参考 Q2 复盘与备件清单")
    selected = {c.doc.id for c in result.candidates if c.selected}
    assert "evd1" in selected and "evd2" in selected
    assert "evd3" not in selected, "无关附件不应被选中"
    assert "evd4" not in selected and "evd5" not in selected and "evd6" not in selected
    # 含毒/删除/无对象被跳过且不在候选里
    ids = {c.doc.id for c in result.candidates}
    assert ids == {"evd1", "evd2", "evd3"}
    assert result.injected and all(c.doc_id in {"evd1", "evd2"} for c in result.injected)


@pytest.mark.asyncio
async def test_permission_denied_403(seeded):
    session, task, admin, _ = seeded
    outsider = User(id="u-none", name="局外人", account="outsider", roles=[])
    with pytest.raises(Exception) as exc:
        await svc.build_attachment_evidence(session, task, outsider, "问题")
    assert exc.value.status_code == 403  # BizError 暴露 status_code（HTTPException 属性）


@pytest.mark.asyncio
async def test_cache_hit_skips_reparse(seeded, monkeypatch):
    session, task, admin, docs = seeded
    calls = {"n": 0}
    real = svc.parse_attachment

    async def counting_parse(doc, data, ocr=None):
        calls["n"] += 1
        return await real(doc, data, ocr)

    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "内容".encode())
    monkeypatch.setattr(svc, "parse_attachment", counting_parse)

    await svc.build_attachment_evidence(session, task, admin, "备件")
    n_first = calls["n"]
    await svc.build_attachment_evidence(session, task, admin, "备件")
    assert calls["n"] == n_first, "第二次应命中缓存，不重新解析"


@pytest.mark.asyncio
async def test_render_evidence_section_formats_locations():
    from flowhub_api.services.attachment_parsers import EvidenceChunk

    result = svc.EvidenceResult(
        candidates=[], parsed=[], injected=[
            EvidenceChunk(doc_id="d1", doc_name="Q2.pdf", location="p2", seq=1, kind="text", text="内容"),
        ],
        total_chars=4, duration_ms=10,
    )
    rendered = svc.render_evidence_section(result)
    assert "[附件@Q2.pdf:p2:1]" in rendered
    empty = svc.EvidenceResult(candidates=[], parsed=[], injected=[], total_chars=0, duration_ms=0)
    assert "附件未解析或与问题无关" in svc.render_evidence_section(empty)


@pytest.mark.asyncio
async def test_oversized_attachment_degrades_to_failed(seeded, monkeypatch):
    session, task, admin, docs = seeded
    cache = svc.ProcessLRUAttachmentCache()

    async def oversized(doc):
        raise ValueError("文件过大（上限 50MB），跳过解析")

    monkeypatch.setattr(svc, "_load_bytes", oversized)
    result = await svc.build_attachment_evidence(
        session, task, admin, "分析备件库存，参考 Q2 复盘与备件清单", cache=cache)
    assert result.parsed, "超大附件应降级为 failed 而非抛异常"
    assert all(p.status == "failed" for p in result.parsed)
    assert all("文件过大" in p.error for p in result.parsed)


@pytest.mark.asyncio
async def test_retrieve_more_evidence_no_redownload(seeded, monkeypatch):
    session, task, admin, docs = seeded
    calls = {"n": 0}

    def counting_load(doc):
        calls["n"] += 1
        return "备件内容".encode()

    monkeypatch.setattr(svc, "_load_bytes", counting_load)
    cache = svc.ProcessLRUAttachmentCache()
    await svc.build_attachment_evidence(
        session, task, admin, "分析备件库存，参考 Q2 复盘与备件清单", cache=cache)
    n_build = calls["n"]
    await svc.retrieve_more_evidence(session, task, admin, "备件", cache=cache)
    assert calls["n"] == n_build, "二次检索缓存未命中应跳过，不重新下载"


@pytest.mark.asyncio
async def test_failed_parse_not_cached_so_reparse_recovers(seeded, monkeypatch):
    session, task, admin, docs = seeded
    cache = svc.ProcessLRUAttachmentCache()
    monkeypatch.setattr(svc, "_load_bytes",
                        lambda doc: (_ for _ in ()).throw(ValueError("模拟读取失败")))

    first = await svc.build_attachment_evidence(session, task, admin, "今天天气怎么样", cache=cache)
    assert first.parsed and all(p.status == "failed" for p in first.parsed)

    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "备件缺货 120 单\n补货周期 7 天".encode())
    second = await svc.build_attachment_evidence(session, task, admin, "今天天气怎么样", cache=cache)
    indexed = [p for p in second.parsed if p.status == "indexed"]
    assert indexed, "首次 failed 未写入缓存，修复原因后重试应真实重新解析出 indexed"
    assert any("备件缺货 120 单" in c.text for c in second.injected)


@pytest.mark.asyncio
async def test_retrieve_more_evidence_depth_limit(seeded):
    session, task, admin, _ = seeded
    with pytest.raises(ValueError) as exc:
        await svc.retrieve_more_evidence(session, task, admin, "备件", depth=99)
    assert "深度超限" in str(exc.value)


@pytest.mark.asyncio
async def test_no_keyword_match_falls_back_to_first_docs(seeded, monkeypatch):
    session, task, admin, docs = seeded
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "无关内容".encode())
    cache = svc.ProcessLRUAttachmentCache()
    result = await svc.build_attachment_evidence(session, task, admin, "今天天气怎么样", cache=cache)
    selected = [c for c in result.candidates if c.selected]
    assert selected, "无关键词命中时按前 limit_docs 兜底选中候选"
    assert all(c.reason.startswith("无关键词") for c in selected)
    assert {c.doc.id for c in selected} <= {"evd1", "evd2", "evd3"}
