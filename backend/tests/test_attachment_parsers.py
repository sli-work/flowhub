"""附件解析器：数据结构 + 纯文本 / DOCX / XLSX / PPTX 的解析与证据定位。"""
from io import BytesIO

from flowhub_api.models.support import DocItem
from flowhub_api.services.attachment_parsers import parse_attachment

TXT = "需求: 备件库存看板\n数据来自 Q2 复盘。\n"


def _doc(name: str, doc_id: str = "dtest") -> DocItem:
    return DocItem(id=doc_id, name=name, project="p", version="v1", level="L2",
                   scan="已扫描", uploader="t", size="1KB", time="now")


async def test_plain_text_parsed_with_location():
    parsed = await parse_attachment(_doc("notes.txt"), TXT.encode("utf-8"))
    assert parsed.status == "indexed"
    assert parsed.parser == "plain"
    assert parsed.chunks and parsed.chunks[0].location.startswith("p1")
    assert "备件库存看板" in parsed.chunks[0].text


async def test_docx_parsed_with_paragraph_and_table():
    import docx

    document = docx.Document()
    document.add_heading("标题", level=1)
    document.add_paragraph("第一段正文")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "sku"
    table.cell(0, 1).text = "qty"
    table.cell(1, 0).text = "A-1"
    table.cell(1, 1).text = "42"
    buf = BytesIO()
    document.save(buf)
    parsed = await parse_attachment(_doc("doc.docx"), buf.getvalue())
    assert parsed.status == "indexed"
    assert parsed.parser == "docx"
    assert any(c.kind == "title" and "标题" in c.text for c in parsed.chunks)
    assert any(c.kind == "table" and "A-1" in c.text for c in parsed.chunks)


async def test_xlsx_parsed_with_sheet_location():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Q2"
    ws.append(["月份", "销售额"])
    ws.append(["4月", "100"])
    buf = BytesIO()
    wb.save(buf)
    parsed = await parse_attachment(_doc("book.xlsx"), buf.getvalue())
    assert parsed.status == "indexed"
    assert parsed.parser == "xlsx"
    assert any(c.location.startswith("Q2!") for c in parsed.chunks)
    assert any("销售额" in c.text for c in parsed.chunks)


async def test_pptx_parsed_with_slide_location():
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "盘点报告"
    buf = BytesIO()
    prs.save(buf)
    parsed = await parse_attachment(_doc("deck.pptx"), buf.getvalue())
    assert parsed.status == "indexed"
    assert parsed.parser == "pptx"
    assert any(c.location.startswith("slide") for c in parsed.chunks)
    assert any("盘点报告" in c.text for c in parsed.chunks)


async def test_unsupported_extension_skipped():
    parsed = await parse_attachment(_doc("a.exe"), b"MZ....")
    assert parsed.status == "skipped"
