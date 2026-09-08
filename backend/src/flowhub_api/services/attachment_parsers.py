"""附件格式解析器注册表：把附件正文切成带来源定位的证据块。

安全约束见 spec「安全约束」：普通 ZIP 只索引白名单文本条目，不递归嵌套压缩包，
不把脚本/二进制正文交给模型；ZIP 校验复用 routes/documents.py 的常量。
"""
import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from flowhub_api.core.response import BizCode, BizError

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

_OCR_MAX_PAGES = 5
_OCR_TIMEOUT_SEC = 8.0


async def parse_attachment(doc, data: bytes, ocr=None) -> ParsedAttachment:
    """按扩展名派发解析；返回带来源定位的证据块。pdf 走异步 OCR 降级解析，zip 走安全白名单/Axure 页面树解析。"""
    ext = _ext(doc)
    if ext == "pdf":
        try:
            return await _parse_pdf(doc, data, ocr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 解析 %s 失败: %s", doc.name, exc)
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="pdf", error=str(exc)[:200])
    if ext == "zip":
        try:
            return await _parse_zip(doc, data)
        except BizError as exc:
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="zip", error=str(exc.detail)[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("ZIP 解析 %s 失败: %s", doc.name, exc)
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="zip", error=str(exc)[:200])
    fn = _SYNC_PARSERS.get(ext)
    if fn is None:
        return ParsedAttachment(doc_id=doc.id, status="skipped", parser="unknown")
    try:
        parser = globals()[fn]
        return parser(doc, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("解析 %s 失败: %s", doc.name, exc)
        return ParsedAttachment(doc_id=doc.id, status="failed", parser=ext, error=str(exc)[:200])


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


async def _parse_pdf(doc, data: bytes, ocr=None) -> ParsedAttachment:
    import asyncio
    import fitz  # PyMuPDF

    pdf = fitz.open(stream=data, filetype="pdf")
    chunks = []
    ocr_pages = 0
    has_text = False
    try:
        for page_no, page in enumerate(pdf, start=1):
            text = page.get_text("text") or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                has_text = True
                title = max(lines, key=len)[:200]
                chunks.append(_chunk(doc, f"p{page_no}", len(chunks) + 1,
                                     "title" if len(lines) > 1 else "text", title))
                chunks.append(_chunk(doc, f"p{page_no}", len(chunks) + 1, "text",
                                     "\n".join(lines)[:4000]))
                continue
            marker = _chunk(doc, f"p{page_no}", len(chunks) + 1, "ocr_marker", "[无文本层，需 OCR 识别]")
            if ocr is not None and getattr(ocr, "available", False) and ocr_pages < _OCR_MAX_PAGES:
                try:
                    pix = page.get_pixmap(dpi=200)
                    png = pix.tobytes("png")
                    ocr_text = await asyncio.wait_for(
                        ocr.extract_text(png, page_no), timeout=_OCR_TIMEOUT_SEC,
                    )
                except Exception:  # noqa: BLE001 — OCR 失败降级为标记
                    ocr_text = ""
                if ocr_text.strip():
                    ocr_pages += 1
                    has_text = True
                    chunks.append(_chunk(doc, f"p{page_no}", len(chunks) + 1, "text",
                                         f"[OCR] {ocr_text.strip()[:2000]}"))
                    continue
            chunks.append(marker)
    finally:
        pdf.close()
    status = "indexed" if has_text else "needs_ocr"
    return ParsedAttachment(doc_id=doc.id, status=status, parser="pdf",
                            chunks=chunks, entries_or_pages=len(chunks))


_ZIP_MAX_TOTAL = 200 * 1024 * 1024
_ZIP_MAX_ENTRIES = 2000
_ZIP_TEXT_EXT = TEXT_EXT
_ZIP_NESTED_EXT = {"zip", "gz", "tar", "7z", "rar"}


def _zip_name_decode(info) -> str:
    """zip 条目名编码修复，与 routes/documents.py 一致：UTF-8 标志 → 原名；否则 CP437 还原后按 UTF-8/GBK 解码。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def _zip_safe_entries(data: bytes) -> list[tuple[str, zipfile.ZipInfo]]:
    """只读安全校验：总量 ≤200MB、条目 ≤2000、防路径穿越、忽略 __MACOSX/.DS_Store；返回 [(name, info)]。

    name 为解码后的展示名；读取内容一律用 info（zf.open(info)），避免解码名 ≠ info.filename 时 KeyError。
    """
    total = 0
    entry_count = 0
    entries = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = _zip_name_decode(info).replace("\\", "/")
            parts = Path(name).parts
            if "__MACOSX" in parts or parts[-1:] == (".DS_Store",):
                continue
            entry_count += 1
            if entry_count > _ZIP_MAX_ENTRIES:
                raise BizError(BizCode.VALIDATION, "压缩包条目过多，拒绝浏览")
            if not name or name.startswith("/") or any(part in {"", ".", ".."} for part in parts):
                raise BizError(BizCode.VALIDATION, "压缩包包含非法路径，拒绝浏览")
            total += info.file_size
            if total > _ZIP_MAX_TOTAL:
                raise BizError(BizCode.VALIDATION, "解压后体积超限（上限 200MB），疑似压缩炸弹")
            if not info.is_dir():
                entries.append((name, info))
    return entries


def _zip_wrapper_prefix(names: list[str]) -> str:
    """计算单层包裹目录前缀：所有条目都在同一根目录下且含 {root}/index.html 时返回 'root/'，否则空串。

    Axure 包常见「macOS 压缩文件夹」产物：root/index.html + root/data/*.js，需剥掉 root/ 再判断/展示。
    """
    if not names:
        return ""
    seg = names[0].split("/", 1)[0]
    if seg and f"{seg}/index.html" in names and all(n == seg or n.startswith(seg + "/") for n in names):
        return seg + "/"
    return ""


def _is_axure(entries: list[tuple[str, zipfile.ZipInfo]]) -> bool:
    paths = {name.lower() for name, _ in entries}
    if "index.html" in paths:
        return True
    if paths:
        root = next(iter(paths)).split("/", 1)[0]
        return f"{root}/index.html" in paths and all(name.startswith(f"{root}/") for name in paths)


async def _parse_zip(doc, data: bytes) -> ParsedAttachment:
    entries = _zip_safe_entries(data)
    prefix = _zip_wrapper_prefix([name for name, _ in entries])
    chunks = []
    indexed = 0
    if _is_axure(entries):
        return _parse_axure(doc, data, entries, prefix)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name, info in entries:
            rel = name[len(prefix):] if prefix else name
            ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
            if ext in _ZIP_NESTED_EXT:
                chunks.append(_chunk(doc, rel, len(chunks) + 1, "text",
                                     "[嵌套压缩包，不递归解压，请在任务页下载后查看]"))
                continue
            if ext not in _ZIP_TEXT_EXT:
                continue
            try:
                content = zf.open(info).read()
            except Exception:  # noqa: BLE001
                continue
            text = content.decode("utf-8", errors="replace")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                indexed += 1
                chunks.append(_chunk(doc, rel, len(chunks) + 1, "text", "\n".join(lines)[:4000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed" if chunks else "needs_ocr",
                            parser="zip", chunks=chunks, entries_or_pages=indexed)


def _parse_axure(doc, data: bytes, entries: list[tuple[str, zipfile.ZipInfo]], prefix: str) -> ParsedAttachment:
    import re as _re

    chunks = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {(name[len(prefix):] if prefix else name).lower(): name for name, _ in entries}
        if "index.html" in names:
            name = names["index.html"]
            rel = name[len(prefix):] if prefix else name
            try:
                html = zf.open(next(info for n, info in entries if n == name)).read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                html = ""
            m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.S | _re.I)
            if m and m.group(1).strip():
                chunks.append(_chunk(doc, rel, len(chunks) + 1, "title",
                                     f"[Axure 原型] {m.group(1).strip()[:200]}"))
        for name, info in entries:
            rel = name[len(prefix):] if prefix else name
            ext = rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
            if ext not in _ZIP_TEXT_EXT and not (ext == "js" and rel.startswith("data/")):
                continue
            try:
                content = zf.open(info).read()
            except Exception:  # noqa: BLE001
                continue
            text = content.decode("utf-8", errors="replace")
            if ext == "js":
                # 仅静态提取页面名/页面树文本，绝不执行脚本，也不把脚本正文交给模型。
                # 标记兼容两种形态：真实 Axure 产物是 "pages":（带引号 key），简报示例为 pages:[
                if '"pages"' in text or "pages:" in text:
                    page_names = _re.findall(r'"name"\s*:\s*"([^"]{1,80})"', text)
                    if page_names:
                        chunks.append(_chunk(doc, rel, len(chunks) + 1, "text",
                                             f"[Axure 页面树] {' / '.join(page_names[:50])}"))
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                chunks.append(_chunk(doc, rel, len(chunks) + 1, "text", "\n".join(lines)[:4000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed" if chunks else "needs_ocr",
                            parser="axure", chunks=chunks, entries_or_pages=len(entries))
