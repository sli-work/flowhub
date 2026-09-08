"""附件格式解析器注册表：把附件正文切成带来源定位的证据块。

安全约束见 spec「安全约束」：普通 ZIP 只索引白名单文本条目，不递归嵌套压缩包，
不把脚本/二进制正文交给模型；ZIP 校验复用 routes/documents.py 的常量。
"""
import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PARSER_VERSION = "1"

TEXT_EXT = {"txt", "md", "yaml", "yml", "json", "csv"}


@dataclass
class EvidenceChunk:
    doc_id: str
    doc_name: str
    location: str            # "p12" / "data/items.json" / "Sheet2!A1" / "slide3"
    seq: int
    kind: str                # "text" | "table" | "title" | "ocr_marker" | ...
    text: str


@dataclass
class ParsedAttachment:
    doc_id: str
    status: str              # "indexed" | "needs_ocr" | "failed" | "skipped"
    parser: str
    cache_hit: bool = False
    duration_ms: int = 0
    chunks: list[EvidenceChunk] = field(default_factory=list)
    entries_or_pages: int = 0
    error: str = ""


def _ext(doc) -> str:
    return doc.name.rsplit(".", 1)[-1].lower() if "." in doc.name else ""


_SYNC_PARSERS = {
    "txt": "_parse_plain", "md": "_parse_plain", "yaml": "_parse_plain", "yml": "_parse_plain",
    "json": "_parse_plain", "csv": "_parse_plain",
    "docx": "_parse_docx", "xlsx": "_parse_xlsx", "pptx": "_parse_pptx",
}


async def parse_attachment(doc, data: bytes, ocr=None) -> ParsedAttachment:
    """按扩展名派发解析；返回带来源定位的证据块。pdf/zip 由 Task 3/4 补齐，暂 skipped。"""
    ext = _ext(doc)
    if ext == "pdf":
        return _skip_pdf(doc)
    if ext == "zip":
        return ParsedAttachment(doc_id=doc.id, status="skipped", parser="zip")
    fn = _SYNC_PARSERS.get(ext)
    if fn is None:
        return ParsedAttachment(doc_id=doc.id, status="skipped", parser="unknown")
    try:
        parser = globals()[fn]
        return parser(doc, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 %s 失败: %s", doc.name, exc)
        return ParsedAttachment(doc_id=doc.id, status="failed", parser=ext, error=str(exc)[:200])


def _skip_pdf(doc) -> ParsedAttachment:
    return ParsedAttachment(doc_id=doc.id, status="skipped", parser="pdf")


def _chunk(doc, location: str, seq: int, kind: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(doc_id=doc.id, doc_name=doc.name, location=location,
                         seq=seq, kind=kind, text=text.strip())


def _parse_plain(doc, data: bytes) -> ParsedAttachment:
    text = data.decode("utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    chunks = [_chunk(doc, f"p{idx}", idx, "text", line) for idx, line in enumerate(lines, start=1)]
    if not chunks and text.strip():
        chunks.append(_chunk(doc, "p1", 1, "text", text[:4000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed", parser="plain",
                            chunks=chunks, entries_or_pages=len(lines))


def _parse_docx(doc, data: bytes) -> ParsedAttachment:
    import docx

    document = docx.Document(io.BytesIO(data))
    chunks = []
    for idx, para in enumerate(document.paragraphs, start=1):
        text = para.text.strip()
        if not text:
            continue
        kind = "title" if (para.style and "heading" in (para.style.name or "").lower()) else "text"
        chunks.append(_chunk(doc, f"p{idx}", len(chunks) + 1, kind, text[:1000]))
    for ti, table in enumerate(document.tables, start=1):
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows[:20]]
        if rows:
            chunks.append(_chunk(doc, f"table{ti}", len(chunks) + 1, "table", "\n".join(rows)))
    return ParsedAttachment(doc_id=doc.id, status="indexed", parser="docx",
                            chunks=chunks, entries_or_pages=len(document.tables))


def _parse_xlsx(doc, data: bytes) -> ParsedAttachment:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    chunks = []
    sheet_count = 0
    for ws in wb.worksheets:
        sheet_count += 1
        rows = []
        first_loc = ""
        for row_idx, row in enumerate(ws.iter_rows(max_row=20, values_only=True), start=1):
            values = ["" if v is None else str(v) for v in row]
            if not any(values):
                continue
            if not first_loc:
                last_col = chr(64 + min(len(values), 26))
                first_loc = f"{ws.title}!A{row_idx}:{last_col}{row_idx}"
            rows.append(" | ".join(values))
        if rows:
            chunks.append(_chunk(doc, first_loc or f"{ws.title}!A1", len(chunks) + 1, "table",
                                 f"{ws.title}（{ws.max_row}行×{ws.max_column}列）\n" + "\n".join(rows)))
    return ParsedAttachment(doc_id=doc.id, status="indexed", parser="xlsx",
                            chunks=chunks, entries_or_pages=sheet_count)


def _parse_pptx(doc, data: bytes) -> ParsedAttachment:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    chunks = []
    for idx, slide in enumerate(prs.slides, start=1):
        texts = [shape.text.strip() for shape in slide.shapes
                 if shape.has_text_frame and shape.text.strip()]
        title = (slide.shapes.title.text.strip()
                 if slide.shapes.title and slide.shapes.title.has_text_frame else "")
        body = "\n".join(t for t in texts if t != title)
        if title or body:
            chunks.append(_chunk(doc, f"slide{idx}", len(chunks) + 1, "title" if title else "text",
                                 (title + "\n" + body).strip()[:2000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed", parser="pptx",
                            chunks=chunks, entries_or_pages=len(prs.slides._sldIdLst))
