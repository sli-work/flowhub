# 任务附件的意图驱动解析与证据上下文 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前「附件名 + 4KB 文本片段」的 Agent 上下文，升级为 Expert 运行时按任务意图选择附件、解析/缓存、检索证据片段并注入模型上下文；解析状态、证据片段与来源定位在任务处理界面可见可追溯。

**Architecture:** 后端新增 `services/attachment_{evidence,parsers,cache,ocr}.py` 四个服务：意图选择（文件名/元数据关键词打分）→ 解析（PyMuPDF/python-docx/openpyxl/python-pptx/RapidOCR，进程内 TTL LRU 按 `doc_id:version:object_name:size:time:PARSER_VERSION` 缓存）→ 检索排序 → 注入。LangGraph 的 `execute_run.snapshot()` 在 `build_task_context` 之后追加 `【任务附件证据】`，trace 每个附件，`run.parsed["attachmentEvidence"]` 供任务页展示；新增 MCP 二次检索工具与 `reparse-attachments` 接口。前端 `node.tsx` 展示附件解析状态徽标、可展开证据片段、失败重试解析。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、LangGraph 0.6、PyMuPDF、python-docx、openpyxl、python-pptx、rapidocr-onnxruntime、pytest；React 19、TypeScript 5.9、Vite。

## Global Constraints

- 附件证据服务只读取 `DocItem.wi == task.wi_id`、`deleted == False`、`scan != "含毒"`、`object_name` 非空的文档；权限复用 `agent_context.can_read_task`（无权限 403，语义不变）。
- ZIP 安全校验复用 `routes/documents.py` 既有常量/逻辑：解压总量 ≤200MB、条目 ≤2000、防路径穿越（`Path.resolve()` 前缀校验）、忽略 `__MACOSX`/`.DS_Store`；本服务不落盘解压、不递归嵌套压缩包、不给模型脚本/二进制正文。
- 解析预算硬上限：`limit_docs=5 / limit_chunks=20 / limit_chars=8000 / timeout_ms=15000`；OCR ≤5 页、单页 ≤8s，超限页标记 `ocr_marker` 不阻塞整体。
- 缓存键 = `sha256(f"{doc.id}:{doc.version}:{doc.object_name}:{doc.size}:{doc.time}:{PARSER_VERSION}")[:32]`；`PARSER_VERSION` 变更即全部失效。
- OCR 开关：`core/config.py` 新增 `attachment_ocr_enabled: bool = False`；`runtime_config.settings()` 返回 `attachment_ocr_enabled=True` 且 `rapidocr_onnxruntime` 可 import 时用 `RapidOcrAdapter`，否则 `NoopOcrAdapter`（需 OCR 降级，不伪造文本）。
- 附件证据以工具形式提供：模型自主调用 `flowhub_attachment_list` / `flowhub_attachment_search`；引用格式 `[附件@文档名:页码或路径:片段号]`（非强制注入，但涉及附件结论应引用）。质量校验：产出提到「附件」但本轮 `tool_trace` 无 `flowhub_attachment.*` 调用 → `needs_human_review`。不把附件解析/注入逻辑写死在 `snapshot()`/`model_node` 的业务分支里。
- 依赖用 `uv` 管理（`uv add` + `uv lock`），不手改 `uv.lock`；PyPI 镜像 `http://192.168.239.230:8887/repository/mc-group-pypi/simple/`。
- 不改变文档上传、预览（`document-viewer-drawer.tsx`）、下载、权限链路本身。
- 测试库 `postgresql://flowhub:flowhub123@192.168.21.4:5432/flowhub_test`；测试命令统一 `cd backend && uv run pytest <file> -v`。

---

## File Structure

- Modify `backend/pyproject.toml`：新增 5 个解析依赖。
- Create `backend/src/flowhub_api/services/ocr.py`：OCR 适配器协议 + Noop + RapidOCR + `get_ocr_adapter()`。
- Create `backend/src/flowhub_api/services/attachment_parsers.py`：`EvidenceChunk`/`ParsedAttachment` 数据结构 + 各格式解析器 + `parse_attachment` 注册表 + `PARSER_VERSION`。
- Create `backend/src/flowhub_api/services/attachment_cache.py`：`AttachmentCache` 抽象 + `ProcessLRUAttachmentCache`（TTL LRU）+ `cache_key()`。
- Create `backend/src/flowhub_api/services/attachment_evidence.py`：`AttachmentCandidate`/`EvidenceResult` + `build_attachment_evidence()` + 意图选择/检索排序/注入渲染 + `retrieve_more_evidence()`。
- Modify `backend/src/flowhub_api/core/config.py`：新增 `attachment_ocr_enabled`。
- Modify `backend/src/flowhub_api/services/expert_runtime.py`：泛化 `run_repo_tool_loop`（`max_calls/max_chars` 参数）、`build_graph` 增 `attachment_tools_factory`、`model_node` 合并 附件+repo 工具为一个循环、`execute_run` 构建附件工具 factory 与结束汇总 `run.parsed["attachmentEvidence"]`、`check_attachment_citations`（基于 `tool_trace`）。
- Create `backend/src/flowhub_api/services/attachment_tools.py`：`AttachmentToolBundle` + `create_attachment_tool_bundle`（`flowhub_attachment_list`/`flowhub_attachment_search` 两个 StructuredTool）。
- Modify `backend/src/flowhub_api/services/mcp_server.py`：新增 `retrieve_attachment_evidence` 工具。
- Modify `backend/src/flowhub_api/routes/tasks.py`：新增 `POST /{task_id}/reparse-attachments`。
- Modify `frontend/src/pages/node.tsx`：Expert 产出卡内附件证据面板（状态徽标/证据展开/重试解析）。
- Modify `frontend/src/types/index.ts`：`ExpertRunBrief.parsed` 增加 `attachmentEvidence` 类型。
- Create `backend/tests/test_attachment_ocr.py`、`test_attachment_parsers.py`、`test_attachment_cache.py`、`test_attachment_evidence.py`、`test_attachment_run.py`、`test_attachment_reparse.py`。
- Modify `backend/tests/test_documents.py` 或独立：ZIP 安全复用断言在 `test_attachment_parsers.py` 覆盖。

### Task 1: 依赖与 OCR 适配器

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/flowhub_api/core/config.py:7-30`
- Create: `backend/src/flowhub_api/services/ocr.py`
- Test: `backend/tests/test_attachment_ocr.py`

**Interfaces:**
- Produces: `OcrAdapter`（`name`/`available`/`async extract_text(image_bytes, page_no) -> str`）、`NoopOcrAdapter`、`RapidOcrAdapter`、`get_ocr_adapter() -> OcrAdapter`；`Settings.attachment_ocr_enabled: bool = False`。
- Consumes: `flowhub_api.services.runtime_config.settings()` 的内存快照。

- [ ] **Step 1: 添加解析依赖**

```bash
cd backend && uv add pymupdf python-docx openpyxl python-pptx rapidocr-onnxruntime
```

Expected: `pyproject.toml` `[project].dependencies` 出现 5 个包，`uv.lock` 更新，无报错。

- [ ] **Step 2: config.py 增加 OCR 开关**

在 `backend/src/flowhub_api/core/config.py` 的 `class Settings` 中 `bootstrap_admin_name` 之后加：

```python
    # 附件 OCR：本地 RapidOCR（CPU）；false 时扫描件标记「需 OCR」降级
    attachment_ocr_enabled: bool = False
```

- [ ] **Step 3: 写失败的测试**

Create `backend/tests/test_attachment_ocr.py`:

```python
"""OCR 适配器：Noop 降级 / RapidOCR 可用性 / get_ocr_adapter 开关。"""
import pytest

from flowhub_api.services import runtime_config
from flowhub_api.services.ocr import NoopOcrAdapter, RapidOcrAdapter, get_ocr_adapter


@pytest.mark.asyncio
async def test_noop_extract_text_returns_empty():
    adapter = NoopOcrAdapter()
    assert adapter.available is False
    assert await adapter.extract_text(b"fake-image", 1) == ""


@pytest.mark.asyncio
async def test_get_ocr_adapter_disabled_returns_noop(monkeypatch):
    monkeypatch.setattr(runtime_config, "_values", {"attachment_ocr_enabled": "false"})
    adapter = get_ocr_adapter()
    assert isinstance(adapter, NoopOcrAdapter)


@pytest.mark.asyncio
async def test_get_ocr_adapter_enabled_returns_rapidocr(monkeypatch):
    monkeypatch.setattr(runtime_config, "_values", {"attachment_ocr_enabled": "true"})
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        pytest.skip("rapidocr-onnxruntime 未安装")
    adapter = get_ocr_adapter()
    assert isinstance(adapter, RapidOcrAdapter)
    assert adapter.available is True
```

- [ ] **Step 4: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_ocr.py -v`
Expected: FAIL（`ModuleNotFoundError: flowhub_api.services.ocr`）。

- [ ] **Step 5: 实现 ocr.py**

Create `backend/src/flowhub_api/services/ocr.py`:

```python
"""附件 OCR 适配器：本地 RapidOCR（CPU，中英文）或 Noop 降级。

开关：RuntimeConfig 内存快照 `attachment_ocr_enabled == "true"` 且依赖可 import 时启用，
否则回退 Noop（扫描件标记「需 OCR」，不伪造文本）。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class OcrAdapter:
    name: str = "unknown"
    available: bool = False

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        raise NotImplementedError


class NoopOcrAdapter(OcrAdapter):
    name = "noop"
    available = False

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        return ""


class RapidOcrAdapter(OcrAdapter):
    name = "rapidocr"
    available = True

    def __init__(self) -> None:
        self._engine = None
        self._lock = asyncio.Lock()

    async def _engine_or_none(self):
        if self._engine is not None:
            return self._engine
        async with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                import rapidocr_onnxruntime
            except ImportError:
                logger.warning("rapidocr-onnxruntime 未安装，OCR 不可用")
                return None
            self._engine = rapidocr_onnxruntime.RapidOCR()
            return self._engine

    async def extract_text(self, image_bytes: bytes, page_no: int) -> str:
        engine = await self._engine_or_none()
        if engine is None:
            return ""
        try:
            result, _ = await asyncio.to_thread(engine, image_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR 第 %s 页失败：%s", page_no, exc)
            return ""
        if not result:
            return ""
        return "\n".join(item[1] for item in result if len(item) >= 2 and item[1])


def get_ocr_adapter() -> OcrAdapter:
    """按运行时配置 + 依赖可用性返回 OCR 适配器；不可用时永远返回 Noop，不抛错。"""
    from flowhub_api.services import runtime_config

    try:
        enabled = bool(runtime_config.settings().attachment_ocr_enabled)
    except Exception:  # noqa: BLE001
        enabled = False
    if not enabled:
        return NoopOcrAdapter()
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return NoopOcrAdapter()
    return RapidOcrAdapter()
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_ocr.py -v`
Expected: PASS（若 rapidocr 未装则启用分支 skip）。

- [ ] **Step 7: 提交**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/flowhub_api/core/config.py backend/src/flowhub_api/services/ocr.py backend/tests/test_attachment_ocr.py
git commit -m "feat: 附件 OCR 适配器（RapidOCR + Noop 降级）"
```

### Task 2: 附件解析器——数据结构与纯文本/Office 解析

**Files:**
- Create: `backend/src/flowhub_api/services/attachment_parsers.py`（数据结构 + `_parse_plain`/`_parse_docx`/`_parse_xlsx`/`_parse_pptx` + `parse_attachment` 注册表骨架）
- Test: `backend/tests/test_attachment_parsers.py`

**Interfaces:**
- Produces: `EvidenceChunk(doc_id, doc_name, location, seq, kind, text)`、`ParsedAttachment(doc_id, status, parser, cache_hit=False, duration_ms=0, chunks=[], entries_or_pages=0, error="")`、`PARSER_VERSION="1"`、`async parse_attachment(doc, data: bytes, ocr=None) -> ParsedAttachment`。
- Consumes: `flowhub_api.models.support.DocItem`（只用 `doc.id`/`doc.name`）。
- Note: Task 3 补 `_parse_pdf`（async，内部 `await ocr`），Task 4 补 `_parse_zip`；本 Task 对 `.pdf`/`.zip`/图片先返回 `skipped` 占位。

- [ ] **Step 1: 写失败的测试**

Create `backend/tests/test_attachment_parsers.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py -v`
Expected: FAIL（`ModuleNotFoundError: flowhub_api.services.attachment_parsers`）。

- [ ] **Step 3: 实现 attachment_parsers.py（数据结构 + 文本/Office）**

Create `backend/src/flowhub_api/services/attachment_parsers.py`:

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py -v`
Expected: PASS（5 个用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_parsers.py backend/tests/test_attachment_parsers.py
git commit -m "feat: 附件解析器数据结构与纯文本/Office 解析"
```

### Task 3: PDF 解析（文本层 + 无文本页 OCR）

**Files:**
- Modify: `backend/src/flowhub_api/services/attachment_parsers.py`（`parse_attachment` 接入 `_parse_pdf`）
- Test: `backend/tests/test_attachment_parsers.py`（追加 PDF 用例）

**Interfaces:**
- Consumes: Task 1 的 `ocr: OcrAdapter | None`（`await ocr.extract_text(png_bytes, page_no)`）。
- Produces: `async _parse_pdf(doc, data, ocr)`（文本层分页 + 标题/表格摘要；无文本层页渲染 PNG 走 OCR；全部无文本且 OCR 不可用 → `status="needs_ocr"`）。OCR 预算：≤5 页、单页 ≤8s。

- [ ] **Step 1: 追加 PDF 测试**

Append to `backend/tests/test_attachment_parsers.py`:

```python
async def test_pdf_text_page_parsed_with_location():
    import fitz

    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "备件库存看板")
    data = pdf.tobytes()
    pdf.close()
    parsed = await parse_attachment(_doc("doc.pdf"), data)
    assert parsed.status == "indexed"
    assert parsed.parser == "pdf"
    assert parsed.chunks and parsed.chunks[0].location.startswith("p")
    assert "备件库存看板" in parsed.chunks[0].text


async def test_pdf_no_text_without_ocr_needs_ocr():
    import fitz

    from flowhub_api.services.ocr import NoopOcrAdapter

    pdf = fitz.open()
    pdf.new_page()  # 空白页，无文本层
    data = pdf.tobytes()
    pdf.close()
    parsed = await parse_attachment(_doc("scan.pdf"), data, ocr=NoopOcrAdapter())
    assert parsed.status == "needs_ocr"
    assert any(c.kind == "ocr_marker" for c in parsed.chunks)


async def test_pdf_no_text_with_ocr_extracts_text():
    import fitz

    pdf = fitz.open()
    pdf.new_page()
    data = pdf.tobytes()
    pdf.close()

    class FakeOcr:
        available = True

        async def extract_text(self, image_bytes, page_no):
            return "OCR 识别内容"

    parsed = await parse_attachment(_doc("scan.pdf"), data, ocr=FakeOcr())
    assert parsed.status == "indexed"
    assert any("OCR 识别内容" in c.text for c in parsed.chunks)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py::test_pdf_text_page_parsed_with_location -v`
Expected: FAIL（pdf 仍走 `_skip_pdf` → `parsed.parser == "pdf"` 但 `status == "skipped"`，断言 `status == "indexed"` 失败）。

- [ ] **Step 3: 实现 _parse_pdf**

Modify `attachment_parsers.py`：

在 `_SYNC_PARSERS` 定义之后、`parse_attachment` 之前插入：

```python
_OCR_MAX_PAGES = 5
_OCR_TIMEOUT_SEC = 8.0
```

将 `parse_attachment` 中两行：

```python
    if ext == "pdf":
        return _skip_pdf(doc)
```

替换为：

```python
    if ext == "pdf":
        try:
            return await _parse_pdf(doc, data, ocr)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 解析 %s 失败: %s", doc.name, exc)
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="pdf", error=str(exc)[:200])
```

删除 `_skip_pdf` 函数，文件末尾追加：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py -v`
Expected: PASS（8 个用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_parsers.py backend/tests/test_attachment_parsers.py
git commit -m "feat: PDF 解析（文本层 + 无文本页 OCR 降级）"
```

### Task 4: ZIP 解析（普通 ZIP + Axure ZIP）

**Files:**
- Modify: `backend/src/flowhub_api/services/attachment_parsers.py`（`parse_attachment` 接入 `_parse_zip`）
- Test: `backend/tests/test_attachment_parsers.py`（追加 ZIP 用例）

**Interfaces:**
- Produces: `async _parse_zip(doc, data)`（安全校验 → 识别 Axure/普通 → 静态读取白名单条目）。
- 安全常量与 `routes/documents.py` 一致：解压总量 ≤200MB、条目 ≤2000、防路径穿越、忽略 `__MACOSX`/`.DS_Store`、剥离单层包裹目录；本服务不落盘解压、不递归嵌套压缩包、不给模型脚本/二进制正文。

- [ ] **Step 1: 追加 ZIP 测试**

Append to `backend/tests/test_attachment_parsers.py`:

```python
import zipfile


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def test_zip_indexes_whitelist_text_only():
    data = _zip_bytes({
        "readme.txt": "说明：Q2 备件盘点",
        "data/items.json": '{"sku": "A-1", "qty": 42}',
        "app.js": "var x = 1;",            # 脚本正文不进模型
        "bin.dat": b"\x00\x01\x02",         # 二进制不进模型
    })
    parsed = await parse_attachment(_doc("pkg.zip"), data)
    assert parsed.status == "indexed"
    assert parsed.parser == "zip"
    texts = "\n".join(c.text for c in parsed.chunks)
    assert "Q2 备件盘点" in texts
    assert "A-1" in texts
    assert "var x = 1" not in texts
    locations = {c.location for c in parsed.chunks}
    assert "readme.txt" in locations and "data/items.json" in locations


async def test_zip_nested_archive_skipped():
    inner = _zip_bytes({"inner.txt": "内部内容"})
    data = _zip_bytes({"outer.zip": inner, "top.txt": "顶层内容"})
    parsed = await parse_attachment(_doc("nested.zip"), data)
    assert parsed.status == "indexed"
    texts = "\n".join(c.text for c in parsed.chunks)
    assert "顶层内容" in texts
    assert "内部内容" not in texts


async def test_zip_axure_extracts_pages_and_title():
    data = _zip_bytes({
        "index.html": "<html><head><title>订单系统原型</title></head><body></body></html>",
        "data/document.js": 'var document = {"pages": [{"name": "首页", "id": "p1"}, {"name": "订单详情", "id": "p2"}]};',
        "data/document.css": "body { color: red; }",
        "js/script.js": "console.log('not for model');",
    })
    parsed = await parse_attachment(_doc("axure.zip"), data)
    assert parsed.status == "indexed"
    assert parsed.parser == "axure"
    texts = "\n".join(c.text for c in parsed.chunks)
    assert "订单系统原型" in texts
    assert "首页" in texts and "订单详情" in texts
    assert "console.log" not in texts


async def test_zip_path_traversal_rejected():
    data = _zip_bytes({"../evil.txt": "越权内容", "ok.txt": "正常"})
    parsed = await parse_attachment(_doc("bad.zip"), data)
    assert parsed.status == "failed"
    assert "路径" in parsed.error or "非法" in parsed.error


async def test_zip_compression_bomb_rejected():
    # 2000 个条目正好触顶，2001 个应拒绝
    entries = {f"f{i}.txt": b"x" * 100 for i in range(2001)}
    data = _zip_bytes(entries)
    parsed = await parse_attachment(_doc("bomb.zip"), data)
    assert parsed.status == "failed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py -v`
Expected: FAIL（zip 仍走 `status="skipped"`）。

- [ ] **Step 3: 实现 _parse_zip**

Modify `attachment_parsers.py`：

将 `parse_attachment` 中两行：

```python
    if ext == "zip":
        return ParsedAttachment(doc_id=doc.id, status="skipped", parser="zip")
```

替换为：

```python
    if ext == "zip":
        try:
            return await _parse_zip(doc, data)
        except BizError as exc:
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="zip", error=str(exc.detail)[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("ZIP 解析 %s 失败: %s", doc.name, exc)
            return ParsedAttachment(doc_id=doc.id, status="failed", parser="zip", error=str(exc)[:200])
```

文件顶部 import 区加：

```python
import re
import zipfile
from pathlib import Path

from flowhub_api.core.response import BizError
```

文件末尾追加（含与 `routes/documents.py` 一致的编码修复与安全校验，服务层不依赖 routes 避免循环导入）：

```python
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


def _zip_safe_entries(data: bytes) -> list[tuple[str, int]]:
    """只读安全校验：总量 ≤200MB、条目 ≤2000、防路径穿越、忽略 __MACOSX/.DS_Store；返回 [(name, size)]。"""
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
                entries.append((name, info.file_size))
    return entries


def _is_axure(entries: list[tuple[str, int]]) -> bool:
    paths = {name.lower() for name, _ in entries}
    if "index.html" in paths:
        return True
    if paths:
        root = next(iter(paths)).split("/", 1)[0]
        return f"{root}/index.html" in paths and all(name.startswith(f"{root}/") for name in paths)


async def _parse_zip(doc, data: bytes) -> ParsedAttachment:
    entries = _zip_safe_entries(data)
    chunks = []
    indexed = 0
    if _is_axure(entries):
        return _parse_axure(doc, data, entries)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name, _size in entries:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in _ZIP_NESTED_EXT:
                chunks.append(_chunk(doc, name, len(chunks) + 1, "text",
                                     "[嵌套压缩包，不递归解压，请在任务页下载后查看]"))
                continue
            if ext not in _ZIP_TEXT_EXT:
                continue
            try:
                content = zf.read(name)
            except Exception:  # noqa: BLE001
                continue
            text = content.decode("utf-8", errors="replace")
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                indexed += 1
                chunks.append(_chunk(doc, name, len(chunks) + 1, "text", "\n".join(lines)[:4000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed" if chunks else "needs_ocr",
                            parser="zip", chunks=chunks, entries_or_pages=indexed)


def _parse_axure(doc, data: bytes, entries: list[tuple[str, int]]) -> ParsedAttachment:
    import re as _re

    chunks = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = {name.lower() for name, _ in entries}
        for target in ("index.html",):
            if target not in names:
                continue
            name = next(n for n, _ in entries if n.lower() == target)
            html = zf.read(name).decode("utf-8", errors="replace")
            m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.S | _re.I)
            if m and m.group(1).strip():
                chunks.append(_chunk(doc, name, len(chunks) + 1, "title",
                                     f"[Axure 原型] {m.group(1).strip()[:200]}"))
        for name, _ in entries:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext not in _ZIP_TEXT_EXT and not (ext == "js" and name.startswith("data/")):
                continue
            try:
                content = zf.read(name)
            except Exception:  # noqa: BLE001
                continue
            text = content.decode("utf-8", errors="replace")
            if ext == "js" and "pages:" in text:
                # 仅静态提取页面名/页面树文本，绝不执行脚本
                page_names = _re.findall(r'"name"\s*:\s*"([^"]{1,80})"', text)
                if page_names:
                    chunks.append(_chunk(doc, name, len(chunks) + 1, "text",
                                         f"[Axure 页面树] {' / '.join(page_names[:50])}"))
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if lines:
                chunks.append(_chunk(doc, name, len(chunks) + 1, "text", "\n".join(lines)[:4000]))
    return ParsedAttachment(doc_id=doc.id, status="indexed" if chunks else "needs_ocr",
                            parser="axure", chunks=chunks, entries_or_pages=len(entries))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_parsers.py -v`
Expected: PASS（13 个用例）。若 `BizCode` 未在 `core.response` 导出，改为直接抛 `ValueError`（见下方注）。

Note: 若 `flowhub_api.core.response.BizCode` 无法按上述方式导入，把 `_zip_safe_entries` 内的 `BizError(BizCode.VALIDATION, ...)` 全部换成 `ValueError(...)`，并把 `parse_attachment` 中 `except BizError` 分支改为 `except ValueError`，测试断言不变。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_parsers.py backend/tests/test_attachment_parsers.py
git commit -m "feat: ZIP 解析（普通白名单条目 + Axure 页面树，安全校验）"
```

### Task 5: 附件解析临时缓存

**Files:**
- Create: `backend/src/flowhub_api/services/attachment_cache.py`
- Test: `backend/tests/test_attachment_cache.py`

**Interfaces:**
- Produces: `AttachmentCache`（`async get(key) -> ParsedAttachment | None`、`async set(key, parsed)`、`async clear()`）、`ProcessLRUAttachmentCache(maxsize=256, ttl=3600)`、`cache_key(doc, parser_version) -> str`。
- Consumes: `attachment_parsers.ParsedAttachment`、`models.support.DocItem` 字段（`id/version/object_name/size/time`）。

- [ ] **Step 1: 写失败的测试**

Create `backend/tests/test_attachment_cache.py`:

```python
"""附件解析缓存：命中/失效/TTL/LRU 淘汰。"""
import asyncio

from flowhub_api.models.support import DocItem
from flowhub_api.services.attachment_cache import ProcessLRUAttachmentCache, cache_key
from flowhub_api.services.attachment_parsers import ParsedAttachment

TEST_OBJ = {
    "id": "d1", "name": "a.pdf", "project": "p", "version": "v1", "level": "L2",
    "scan": "已扫描", "uploader": "t", "size": "1KB", "time": "t1",
    "object_name": "d1/a.pdf",
}


def _doc(**overrides) -> DocItem:
    values = {**TEST_OBJ, **overrides}
    return DocItem(**values)


async def test_cache_hit_after_set():
    cache = ProcessLRUAttachmentCache()
    doc = _doc()
    key = cache_key(doc, "1")
    parsed = ParsedAttachment(doc_id=doc.id, status="indexed", parser="pdf")
    await cache.set(key, parsed)
    got = await cache.get(key)
    assert got is parsed


async def test_cache_key_changes_with_object_version():
    assert cache_key(_doc(version="v1"), "1") != cache_key(_doc(version="v2"), "1")


async def test_cache_key_changes_with_parser_version():
    assert cache_key(_doc(), "1") != cache_key(_doc(), "2")


async def test_cache_ttl_expires():
    cache = ProcessLRUAttachmentCache(ttl=0)
    doc = _doc()
    key = cache_key(doc, "1")
    await cache.set(key, ParsedAttachment(doc_id=doc.id, status="indexed", parser="pdf"))
    assert await cache.get(key) is None


async def test_cache_lru_eviction():
    cache = ProcessLRUAttachmentCache(maxsize=2, ttl=3600)
    parsed = ParsedAttachment(doc_id="d", status="indexed", parser="pdf")
    for version in ("v1", "v2", "v3"):
        await cache.set(cache_key(_doc(version=version), "1"), parsed)
    assert await cache.get(cache_key(_doc(version="v1"), "1")) is None
    assert await cache.get(cache_key(_doc(version="v3"), "1")) is parsed


async def test_cache_clear():
    cache = ProcessLRUAttachmentCache()
    doc = _doc()
    key = cache_key(doc, "1")
    await cache.set(key, ParsedAttachment(doc_id=doc.id, status="indexed", parser="pdf"))
    await cache.clear()
    assert await cache.get(key) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_cache.py -v`
Expected: FAIL（`ModuleNotFoundError: flowhub_api.services.attachment_cache`）。

- [ ] **Step 3: 实现 attachment_cache.py**

Create `backend/src/flowhub_api/services/attachment_cache.py`:

```python
"""附件解析结果的进程内 TTL LRU 缓存（临时索引，不建长期库）。

多 worker 部署时各进程独立缓存：缓存仅是提速，不承担一致性。
对象版本变化（version/object_name/size/time 任一改变）即 key 变化 → 重新解析。
"""
import asyncio
import hashlib
import time
from collections import OrderedDict


class AttachmentCache:
    async def get(self, key: str):
        raise NotImplementedError

    async def set(self, key: str, parsed) -> None:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


def cache_key(doc, parser_version: str) -> str:
    fingerprint = f"{doc.id}:{doc.version}:{doc.object_name}:{doc.size}:{doc.time}:{parser_version}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]


class ProcessLRUAttachmentCache(AttachmentCache):
    def __init__(self, maxsize: int = 256, ttl: float = 3600.0) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._store: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str):
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            created, parsed = item
            if time.monotonic() - created > self._ttl:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return parsed

    async def set(self, key: str, parsed) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic(), parsed)
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_cache.py -v`
Expected: PASS（6 个用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_cache.py backend/tests/test_attachment_cache.py
git commit -m "feat: 附件解析进程内 TTL LRU 缓存"
```

### Task 6: 附件证据服务主入口（意图选择 → 解析/缓存 → 检索 → 注入）

**Files:**
- Create: `backend/src/flowhub_api/services/attachment_evidence.py`
- Test: `backend/tests/test_attachment_evidence.py`

**Interfaces:**
- Consumes: Task 1 `ocr.get_ocr_adapter`、Task 2 `attachment_parsers`（`PARSER_VERSION`/`parse_attachment`/`ParsedAttachment`）、Task 5 `attachment_cache`（`cache_key`/`ProcessLRUAttachmentCache`）、`agent_context.can_read_task` 与 `_file_ids`（同包，无循环导入）。
- Produces: `AttachmentCandidate`、`EvidenceResult`、`build_attachment_evidence(...) -> EvidenceResult`、`retrieve_more_evidence(...)`、`render_evidence_section(result) -> str`、`MAX_RETRIEVAL_DEPTH = 3`。
- 权限：无权限 403（复用 `can_read_task`）；只读 `wi == task.wi_id`、`deleted == False`、`scan != "含毒"`、`object_name` 非空；正文经 `_load_bytes` 从 MinIO 读取（≤50MB，超出 → `failed("文件过大")`）。

- [ ] **Step 1: 写失败的测试**

Create `backend/tests/test_attachment_evidence.py`:

```python
"""附件证据服务：意图选择 / 权限与安全过滤 / 解析缓存 / 检索注入 / 渲染。"""
import pytest

from flowhub_api.db.session import SessionFactory
from flowhub_api.models.support import DocItem
from flowhub_api.models.workflow import TaskItem, WorkItem
from flowhub_api.models import User
from flowhub_api.services import attachment_evidence as svc


@pytest.fixture
async def seeded():
    async with SessionFactory() as session:
        wi = WorkItem(id="WI-EVID-001", type="issue", title="备件库存看板", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-EVID-001", wi_id=wi.id, title="分析备件库存", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        docs = [
            DocItem(id="d1", name="Q2服务复盘.pdf", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t1", wi=wi.id, object_name="d1/Q2.pdf"),
            DocItem(id="d2", name="备件清单.xlsx", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t2", wi=wi.id, object_name="d2/list.xlsx"),
            DocItem(id="d3", name="无关文档.txt", project="售后", scan="已扫描", uploader="李四",
                    size="1KB", time="t3", wi=wi.id, object_name="d3/x.txt"),
            DocItem(id="d4", name="病毒文件.zip", project="售后", scan="含毒", uploader="王五",
                    size="1KB", time="t4", wi=wi.id, object_name="d4/v.zip"),
            DocItem(id="d5", name="已删除.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t5", wi=wi.id, object_name="d5/d.txt", deleted=True),
            DocItem(id="d6", name="无对象.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t6", wi=wi.id, object_name=None),
        ]
        session.add(wi); session.add(task); session.add_all(docs)
        await session.commit()
        admin = (await session.execute(
            __import__("sqlalchemy").select(User).where(User.account == "admin")
        )).scalars().first()
        yield session, task, admin, docs


@pytest.mark.asyncio
async def test_intent_selects_relevant_attachments(seeded, monkeypatch):
    session, task, admin, docs = seeded
    contents = {
        "d1": "Q2 服务复盘：备件缺货 120 单".encode(),
        "d2": "月份,sku,qty\n4月,A-1,42".encode(),
        "d3": "无关内容".encode(),
    }
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: contents.get(doc.id, b""))

    result = await svc.build_attachment_evidence(
        session, task, admin, question="分析备件库存看板，参考 Q2 复盘与备件清单")
    selected = {c.doc.id for c in result.candidates if c.selected}
    assert "d1" in selected and "d2" in selected
    assert "d3" not in selected, "无关附件不应被选中"
    assert "d4" not in selected and "d5" not in selected and "d6" not in selected
    # 含毒/删除/无对象被跳过且不在候选里
    ids = {c.doc.id for c in result.candidates}
    assert ids == {"d1", "d2", "d3"}
    assert result.injected and all(c.doc_id in {"d1", "d2"} for c in result.injected)


@pytest.mark.asyncio
async def test_permission_denied_403(seeded):
    session, task, admin, _ = seeded
    outsider = User(id="u-none", name="局外人", account="outsider", roles=[])
    with pytest.raises(Exception) as exc:
        await svc.build_attachment_evidence(session, task, outsider, "问题")
    assert exc.value.http_status == 403


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
```

Note: `TaskItem.status` 默认 `assigned`、`WorkItem.status` 默认 `draft`，满足模型约束；`User.roles` 为空时 `can_read_task` 对非处理人应 403——测试用 `outsider` 无绑定关系，命中 403 分支。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_evidence.py -v`
Expected: FAIL（`ModuleNotFoundError: flowhub_api.services.attachment_evidence`）。

- [ ] **Step 3: 实现 attachment_evidence.py**

Create `backend/src/flowhub_api/services/attachment_evidence.py`:

```python
"""附件证据服务：按任务意图选择附件 → 解析/缓存 → 检索证据片段 → 注入上下文。

权限与 agent_context 一致（403 语义不变）；只读取当前工作项已授权附件，
跳过 deleted / 含毒 / 无对象 的文档。解析预算、ZIP 安全校验见 spec「安全约束」。
"""
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from flowhub_api.models.support import DocItem
from flowhub_api.models.workflow import TaskItem
from flowhub_api.services.agent_context import _file_ids, can_read_task
from flowhub_api.services.attachment_cache import ProcessLRUAttachmentCache, cache_key
from flowhub_api.services.attachment_parsers import PARSER_VERSION, EvidenceChunk, ParsedAttachment, parse_attachment

logger = logging.getLogger(__name__)

MAX_RETRIEVAL_DEPTH = 3
_MAX_DOC_BYTES = 50 * 1024 * 1024


@dataclass
class AttachmentCandidate:
    doc: DocItem
    selected: bool
    reason: str
    score: float


@dataclass
class EvidenceResult:
    candidates: list[AttachmentCandidate]
    parsed: list[ParsedAttachment]
    injected: list[EvidenceChunk]
    total_chars: int
    duration_ms: int


def _extract_keywords(text: str) -> list[str]:
    """与 expert_runtime._extract_keywords 同源：英文词 + 中文 2-gram。"""
    import re

    words = re.findall(r"[a-zA-Z0-9]+", text)
    cjk = "".join(c for c in text if "\u4e00" <= c <= "\u9fff")
    bigrams = [cjk[i : i + 2] for i in range(len(cjk) - 1)] if len(cjk) >= 2 else ([cjk] if cjk else [])
    stop_words = {"吗", "呢", "吧", "的", "了", "是", "在", "我", "你", "他", "她", "它", "啊",
                  "什么", "怎么", "如何", "这个", "那个"}
    keywords = [w for w in words if len(w) >= 2 and w.lower() not in stop_words] \
        + [b for b in bigrams if b not in stop_words]
    seen, out = set(), []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:10]


async def _load_bytes(doc: DocItem) -> bytes:
    """从 MinIO 读取文档对象；返回空 bytes 表示不可读（调用方按解析失败处理）。"""
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings

    minio = get_minio()
    if minio is None or not doc.object_name:
        return b""
    resp = minio.get_object(get_settings().minio_bucket, doc.object_name)
    try:
        data = resp.read(_MAX_DOC_BYTES + 1)
    finally:
        resp.close()
        resp.release_conn()
    if len(data) > _MAX_DOC_BYTES:
        raise ValueError("附件过大（上限 50MB），跳过解析")
    return data


async def _collect_attachments(session, task: TaskItem) -> list[DocItem]:
    """收集当前工作项可见附件（起始表单 + 已完成前序节点 + 追加信息中的引用）。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    session: AsyncSession
    from flowhub_api.models.workflow import TaskAppend, WorkItem, main_task_clause

    wi = await session.get(WorkItem, task.wi_id)
    ids = set(_file_ids(wi.start_values if wi else None))
    prev_tasks = (await session.execute(
        select(TaskItem).where(TaskItem.wi_id == task.wi_id, TaskItem.id < task.id,
                               TaskItem.status == "completed", main_task_clause())
    )).scalars().all() if wi else []
    for t in prev_tasks:
        ids.update(_file_ids(t.form_values))
    if prev_tasks:
        appends = (await session.execute(
            select(TaskAppend).where(TaskAppend.wi_id == task.wi_id,
                                     TaskAppend.task_id.in_([t.id for t in prev_tasks]))
        )).scalars().all()
        for ap in appends:
            ids.update(_file_ids(ap.values))
    if not ids:
        return []
    return list((await session.execute(
        select(DocItem).where(DocItem.id.in_(ids), DocItem.wi == task.wi_id,
                              DocItem.deleted == False,  # noqa: E712
                              DocItem.scan != "含毒", DocItem.object_name.is_not(None))
    )).scalars().all())


def _score_attachment(doc: DocItem, keywords: list[str]) -> float:
    haystack = f"{doc.name} {doc.kind or ''}".lower()
    return sum(1.0 for kw in keywords if kw.lower() in haystack)


def _rank_chunks(chunks_by_doc: dict[str, list[EvidenceChunk]], keywords: list[str],
                 limit_chunks: int, limit_chars: int) -> list[EvidenceChunk]:
    scored = []
    for chunks in chunks_by_doc.values():
        for chunk in chunks:
            weight = sum(1 for kw in keywords if kw.lower() in chunk.text.lower())
            if chunk.kind in ("title", "table"):
                weight += 0.5
            scored.append((weight, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)
    out, total = [], 0
    for weight, chunk in scored:
        if len(out) >= limit_chunks or total + len(chunk.text) > limit_chars:
            break
        out.append(chunk)
        total += len(chunk.text)
    return out


def render_evidence_section(result: EvidenceResult) -> str:
    if not result.injected:
        return ("【任务附件证据】无可用证据片段（附件未解析或与问题无关）。"
                "涉及附件结论必须明确说明「附件未解析/需人工核对」，不得编造。")
    lines = ["【任务附件证据】（引用格式 [附件@文档名:页码或路径:片段号]）："]
    for chunk in result.injected:
        lines.append(f"  [{chunk.seq}] [附件@{chunk.doc_name}:{chunk.location}:{chunk.seq}] {chunk.text[:200]}")
    return "\n".join(lines)


async def _parse_or_load(doc: DocItem, cache, ocr) -> tuple[ParsedAttachment, bool]:
    key = cache_key(doc, PARSER_VERSION)
    cached = await cache.get(key)
    if cached is not None:
        return cached, True
    data = await _load_bytes(doc)
    if not data:
        parsed = ParsedAttachment(doc_id=doc.id, status="failed", parser="unknown",
                                  error="文档内容不可读")
    else:
        parsed = await parse_attachment(doc, data, ocr)
    parsed.cache_hit = False
    await cache.set(key, parsed)
    return parsed, False


async def build_attachment_evidence(
    session, task: TaskItem, user, question: str, output_requirements: str = "",
    *, limit_docs: int = 5, limit_chunks: int = 20, limit_chars: int = 8000,
    timeout_ms: int = 15_000, cache=None, ocr=None,
) -> EvidenceResult:
    """意图选择 → 解析/缓存 → 检索 → 注入。超时降级为当前已完成片段，不抛错。"""
    started = time.monotonic()
    await can_read_task(session, user, task)  # 403 语义不变
    cache = cache or ProcessLRUAttachmentCache()
    from flowhub_api.services.ocr import get_ocr_adapter

    ocr = ocr if ocr is not None else get_ocr_adapter()
    docs = await _collect_attachments(session, task)
    keywords = _extract_keywords(f"{question} {output_requirements}")
    candidates = []
    for doc in docs:
        score = _score_attachment(doc, keywords)
        candidates.append(AttachmentCandidate(doc=doc, selected=score > 0,
                                              reason=f"文件名/元数据命中 {score} 个关键词" if score else "无关键词命中",
                                              score=score))
    selected = [c for c in candidates if c.selected]
    if not selected and candidates:
        selected = candidates[:limit_docs]
        for c in selected:
            c.selected = True
            c.reason = "无关键词命中，按工作项附件顺序兜底"
    parsed_list: list[ParsedAttachment] = []
    chunks_by_doc: dict[str, list[EvidenceChunk]] = {}
    deadline = time.monotonic() + timeout_ms / 1000
    for candidate in selected[:limit_docs]:
        if time.monotonic() > deadline:
            break
        parsed, cache_hit = await _parse_or_load(candidate.doc, cache, ocr)
        parsed.cache_hit = cache_hit
        parsed_list.append(parsed)
        if parsed.status == "indexed" and parsed.chunks:
            chunks_by_doc[parsed.doc_id] = parsed.chunks
    injected = _rank_chunks(chunks_by_doc, keywords, limit_chunks, limit_chars)
    return EvidenceResult(
        candidates=candidates, parsed=parsed_list, injected=injected,
        total_chars=sum(len(c.text) for c in injected),
        duration_ms=round((time.monotonic() - started) * 1000),
    )


async def retrieve_more_evidence(
    session, task: TaskItem, user, query: str, exclude_chunk_seq: set[int] | None = None,
    depth: int = 1, *, limit_docs: int = 3, limit_chunks: int = 10, limit_chars: int = 4000,
    cache=None, ocr=None,
) -> EvidenceResult:
    """模型二次检索：只重检索已缓存解析结果，不重新下载对象；深度超限拒绝。"""
    if depth > MAX_RETRIEVAL_DEPTH:
        raise ValueError(f"检索深度超限（最大 {MAX_RETRIEVAL_DEPTH}）")
    await can_read_task(session, user, task)
    cache = cache or ProcessLRUAttachmentCache()
    from flowhub_api.services.ocr import get_ocr_adapter

    ocr = ocr if ocr is not None else get_ocr_adapter()
    exclude = set(exclude_chunk_seq or [])
    docs = await _collect_attachments(session, task)
    keywords = _extract_keywords(query)
    candidates = [AttachmentCandidate(doc=d, selected=False, reason="", score=0.0) for d in docs]
    parsed_list: list[ParsedAttachment] = []
    chunks_by_doc: dict[str, list[EvidenceChunk]] = {}
    for doc in docs[:limit_docs]:
        key = cache_key(doc, PARSER_VERSION)
        parsed = await cache.get(key)
        if parsed is None:
            parsed, _ = await _parse_or_load(doc, cache, ocr)
        parsed_list.append(parsed)
        if parsed.status == "indexed":
            chunks_by_doc[parsed.doc_id] = [
                c for c in parsed.chunks if c.seq not in exclude
            ]
    injected = _rank_chunks(chunks_by_doc, keywords, limit_chunks, limit_chars)
    return EvidenceResult(candidates=candidates, parsed=parsed_list, injected=injected,
                          total_chars=sum(len(c.text) for c in injected), duration_ms=0)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_evidence.py -v`
Expected: PASS（4 个用例）。若 `models.workflow` 无 `main_task_clause` 导出，改为 `from flowhub_api.services.workflow import main_task_clause`（Task 6 内两处引用同一符号即可）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_evidence.py backend/tests/test_attachment_evidence.py
git commit -m "feat: 附件证据服务（意图选择/解析缓存/检索注入）"
```

### Task 7: LangGraph 集成——附件证据工具 Bundle（模型按需调用）

**Files:**
- Create: `backend/src/flowhub_api/services/attachment_tools.py`（`AttachmentToolBundle` + `create_attachment_tool_bundle`）
- Modify: `backend/src/flowhub_api/services/expert_runtime.py`（泛化 `run_repo_tool_loop` 加 `max_calls/max_chars` 参数；`build_graph` 增 `attachment_tools_factory` 参数；`model_node` 合并 附件+repo 工具为一个循环；`execute_run` 构建 factory 与结束汇总 `run.parsed["attachmentEvidence"]`；`check_attachment_citations` 改为基于 `tool_trace`）
- Modify: `backend/tests/conftest.py`（把 `seeded` fixture 移到 conftest 供多文件共用）
- Modify: `backend/tests/test_attachment_evidence.py`（删除本地 `seeded` fixture 定义，保留其余）
- Test: `backend/tests/test_attachment_run.py`

**Interfaces:**
- Consumes: Task 6 `_collect_attachments`/`_rank_chunks`/`_extract_keywords`/`_load_bytes`、Task 5 `cache_key`/`ProcessLRUAttachmentCache`、Task 2 `PARSER_VERSION`/`parse_attachment`/`ParsedAttachment`、Task 1 `get_ocr_adapter`。
- Produces: `AttachmentToolBundle(tools, traces, evidence_context, parsed, injected)`；`create_attachment_tool_bundle(session, task, user) -> AttachmentToolBundle`（工具名 `flowhub_attachment_list` / `flowhub_attachment_search`，OpenAI 兼容、不带点号）；`MAX_ATTACHMENT_TOOL_CALLS=6` / `MAX_ATTACHMENT_TOOL_CONTEXT_CHARS=8000` / `MAX_SEARCH_DEPTH=3`；`check_attachment_citations(output: str, tool_trace: list[dict]) -> list[str]`。
- Note: 工具与 repo 工具同构（`StructuredTool.from_function(coroutine=...)`）；`model_node` 合并附件与 repo 工具到同一个 tool loop，模型一次对话可自主选择两类工具，逻辑不写死在业务流程里。

- [ ] **Step 1: 把 seeded fixture 移到 conftest**

Append to `backend/tests/conftest.py`（文件末尾）：

```python
@pytest.fixture
async def seeded():
    """任务 + 工作项 + 6 个附件（含含毒/已删除/无对象）的 DB 场景，供附件证据测试共用。"""
    from sqlalchemy import select

    from flowhub_api.db.session import SessionFactory
    from flowhub_api.models import User
    from flowhub_api.models.support import DocItem
    from flowhub_api.models.workflow import TaskItem, WorkItem

    async with SessionFactory() as session:
        wi = WorkItem(id="WI-EVID-001", type="issue", title="备件库存看板", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-EVID-001", wi_id=wi.id, title="分析备件库存", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        docs = [
            DocItem(id="d1", name="Q2服务复盘.pdf", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t1", wi=wi.id, object_name="d1/Q2.pdf"),
            DocItem(id="d2", name="备件清单.xlsx", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t2", wi=wi.id, object_name="d2/list.xlsx"),
            DocItem(id="d3", name="无关文档.txt", project="售后", scan="已扫描", uploader="李四",
                    size="1KB", time="t3", wi=wi.id, object_name="d3/x.txt"),
            DocItem(id="d4", name="病毒文件.zip", project="售后", scan="含毒", uploader="王五",
                    size="1KB", time="t4", wi=wi.id, object_name="d4/v.zip"),
            DocItem(id="d5", name="已删除.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t5", wi=wi.id, object_name="d5/d.txt", deleted=True),
            DocItem(id="d6", name="无对象.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t6", wi=wi.id, object_name=None),
        ]
        session.add(wi); session.add(task); session.add_all(docs)
        await session.commit()
        admin = (await session.execute(select(User).where(User.account == "admin"))).scalars().first()
        yield session, task, admin, docs
```

Modify `backend/tests/test_attachment_evidence.py`：删除其顶部 `@pytest.fixture async def seeded():` 定义（保留其余测试），使其使用 conftest 版本。

- [ ] **Step 2: 写失败的测试**

Create `backend/tests/test_attachment_run.py`:

```python
"""LangGraph 集成：附件证据工具 Bundle + 质量校验。"""
import pytest

from flowhub_api.services import attachment_tools
from flowhub_api.services.expert_runtime import check_attachment_citations


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
    assert "d1" in listed and "d4" not in listed, "含毒附件不应出现在目录"

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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_run.py -v`
Expected: FAIL（`ModuleNotFoundError: flowhub_api.services.attachment_tools`；`check_attachment_citations` 未定义）。

- [ ] **Step 4: 实现 attachment_tools.py 与 check_attachment_citations**

Create `backend/src/flowhub_api/services/attachment_tools.py`:

```python
"""附件证据工具：flowhub_attachment_list / flowhub_attachment_search。

与 repo_mirror.RepoToolBundle 同构：模型按需选择调用，服务器控制预算与审计。
不把附件解析/注入逻辑写死在 LangGraph 业务流程里。
"""
import json
import logging
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool

from flowhub_api.services.agent_context import can_read_task
from flowhub_api.services.attachment_cache import ProcessLRUAttachmentCache, cache_key
from flowhub_api.services.attachment_evidence import (
    _collect_attachments, _extract_keywords, _load_bytes, _rank_chunks,
)
from flowhub_api.services.attachment_parsers import PARSER_VERSION, ParsedAttachment, parse_attachment

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_TOOL_CALLS = 6
MAX_ATTACHMENT_TOOL_CONTEXT_CHARS = 8000
MAX_SEARCH_DEPTH = 3


@dataclass
class AttachmentToolBundle:
    tools: list
    traces: list = field(default_factory=list)
    evidence_context: list = field(default_factory=list)
    candidates: list = field(default_factory=list)   # 可见附件摘要（id/name/ext/kind）
    parsed: list = field(default_factory=list)       # ParsedAttachment（本轮解析结果）
    injected: list = field(default_factory=list)     # EvidenceChunk（本轮检索片段）


async def create_attachment_tool_bundle(session, task, user) -> AttachmentToolBundle:
    await can_read_task(session, user, task)
    cache = ProcessLRUAttachmentCache()
    from flowhub_api.services.ocr import get_ocr_adapter

    ocr = get_ocr_adapter()
    bundle = AttachmentToolBundle(tools=[])
    docs = await _collect_attachments(session, task)
    bundle.candidates = [{
        "id": d.id, "name": d.name,
        "ext": d.name.rsplit(".", 1)[-1].lower() if "." in d.name else "",
        "kind": d.kind or "",
    } for d in docs]

    async def list_attachments() -> str:
        bundle.traces.append({"tool": "flowhub_attachment_list", "status": "succeeded",
                              "summary": f"当前任务可见 {len(bundle.candidates)} 个附件"})
        return json.dumps({"attachments": bundle.candidates}, ensure_ascii=False)

    async def search(query: str, doc_ids: str = "", exclude_seq: str = "", depth: int = 1) -> str:
        if depth > MAX_SEARCH_DEPTH:
            return json.dumps({"error": "检索深度超限"}, ensure_ascii=False)
        docs = await _collect_attachments(session, task)
        wanted = {part.strip() for part in doc_ids.split(",") if part.strip()}
        target = {d.id for d in docs} if not wanted else {d.id for d in docs if d.id in wanted}
        exclude = {int(part) for part in exclude_seq.split(",") if part.strip().isdigit()}
        keywords = _extract_keywords(query)
        chunks_by_doc: dict = {}
        parsed_list: list = []
        for doc in docs:
            if doc.id not in target:
                continue
            key = cache_key(doc, PARSER_VERSION)
            parsed = await cache.get(key)
            if parsed is None:
                data = await _load_bytes(doc)
                parsed = (await parse_attachment(doc, data, ocr) if data
                          else ParsedAttachment(doc_id=doc.id, status="failed", parser="unknown",
                                                error="文档内容不可读"))
                parsed.cache_hit = False
                await cache.set(key, parsed)
            parsed_list.append(parsed)
            if parsed.status == "indexed":
                chunks_by_doc[parsed.doc_id] = [c for c in parsed.chunks if c.seq not in exclude]
        injected = _rank_chunks(chunks_by_doc, keywords, 10, 4000)
        for p in parsed_list:
            bundle.traces.append({"tool": "flowhub_attachment_search", "status": p.status,
                                  "summary": f"{p.parser}；缓存{'命中' if p.cache_hit else '未命中'}；"
                                             f"{p.entries_or_pages} 页/条目；{p.error or '无错误'}"})
        for c in injected:
            bundle.evidence_context.append(f"【附件证据｜{c.doc_name}｜{c.location}｜{c.seq}】\n{c.text}")
        bundle.parsed = parsed_list
        bundle.injected = injected
        if not injected:
            return json.dumps({"error": "未检索到附件证据片段（附件未解析或未命中关键词）"}, ensure_ascii=False)
        return "\n".join(f"[{c.seq}] [附件@{c.doc_name}:{c.location}:{c.seq}] {c.text[:300]}" for c in injected)

    bundle.tools = [
        StructuredTool.from_function(
            coroutine=list_attachments, name="flowhub_attachment_list",
            description="列出当前任务可见附件（名称/类型/扩展名，只读元数据，不读正文）。涉及附件结论前先调用本工具确认可选附件。"),
        StructuredTool.from_function(
            coroutine=search, name="flowhub_attachment_search",
            description="按查询检索任务附件证据片段，返回带 [附件@文档名:页码或路径:片段号] 标注的片段；doc_ids 可限定附件 ID（逗号分隔，留空=全部）；exclude_seq 排除已引用片段序号；depth 为递归深度(1-3)。只读。"),
    ]
    return bundle
```

Modify `backend/src/flowhub_api/services/expert_runtime.py`：在模块级（`build_graph` 之前）追加：

```python
def check_attachment_citations(output: str, tool_trace: list | None) -> list[str]:
    """产出提到「附件」但本轮未调用任何附件证据工具时提示人工核对。"""
    if "附件" not in output:
        return []
    for item in tool_trace or []:
        if str(item.get("tool") or "").startswith("flowhub_attachment"):
            return []
    return ["产出提到附件但本轮未调用 flowhub_attachment.* 工具检索证据，需人工核对"]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_run.py -v`
Expected: PASS（5 个用例）。

- [ ] **Step 6: model_node 合并附件+repo 工具循环，execute_run 构建 factory 与汇总**

Modify `backend/src/flowhub_api/services/expert_runtime.py`：

**(a) 泛化 `run_repo_tool_loop`**：把签名改为

```python
async def run_repo_tool_loop(llm, messages, bundle, *, on_trace=None,
                             max_calls: int = MAX_REPO_TOOL_CALLS,
                             max_chars: int = MAX_REPO_TOOL_CONTEXT_CHARS) -> tuple[str, int]:
```

并把函数体内的 `MAX_REPO_TOOL_CONTEXT_CHARS` 全部替换为 `max_chars`（现有调用默认值不变）。

**(b) `build_graph` 签名**增加 `attachment_tools_factory=None`（放在 `repo_tools_factory` 之后）：

```python
def build_graph(provider, model, system_prompt, flowhub_snapshot=None, emitter=None,
                quality_mode="accurate", repo_tools_factory=None, output_schema=None,
                cached_code_evidence=None, force_code_reanalysis=False,
                attachment_tools_factory=None):
```

**(c) `model_node` 合并工具循环**：把现有 repo 工具分支（`expert_runtime.py:552` 起）的条件与 bundle 构建替换为：

```python
        if (repo_tools_factory is not None or attachment_tools_factory is not None) and int(state.get("attempt", 0)) == 0:
            try:
                from flowhub_api.services.repo_mirror import RepoToolBundle
                bundle = RepoToolBundle(tools=[], traces=[], evidence_context=[])
                attachment_state: dict = {}
                if attachment_tools_factory is not None:
                    try:
                        ab = await attachment_tools_factory(state["prompt"])
                        bundle.tools += ab.tools
                        bundle.traces += ab.traces
                        bundle.evidence_context += ab.evidence_context
                        attachment_state = {
                            "candidates": ab.candidates,
                            "parsed": [{"id": p.doc_id, "status": p.status, "parser": p.parser,
                                        "cacheHit": p.cache_hit, "durationMs": p.duration_ms,
                                        "entriesOrPages": p.entries_or_pages, "error": p.error} for p in ab.parsed],
                            "injected": [{"docId": c.doc_id, "docName": c.doc_name, "location": c.location,
                                          "seq": c.seq, "kind": c.kind, "text": c.text[:200]} for c in ab.injected],
                            "totalChars": sum(len(c.text) for c in ab.injected),
                            "durationMs": 0,
                        }
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("附件工具构建失败: %s", exc)
                if repo_tools_factory is not None:
                    rb = await repo_tools_factory(state["prompt"], include_preflight=not bool(code_evidence) or force_code_reanalysis)
                    bundle.tools += rb.tools
                    bundle.traces += rb.traces
                    bundle.evidence_context += rb.evidence_context
                for item in bundle.traces:
                    await _emit("trace", {"kind": "tool", **item})
                if bundle.tools:
```

并把该分支内 `run_repo_tool_loop(...)` 调用保持（附件与 repo 共享同一循环预算），`return {...}` 增加 `"attachmentEvidence": attachment_state`：

```python
                    return {"output": output, "tool_trace": trace,
                            "flowhub_context": f"{state.get('flowhub_context', '')}\n\n{tool_evidence}".strip(),
                            "attempt": int(state.get("attempt", 0)) + 1, "code_evidence": code_evidence,
                            "attachmentEvidence": attachment_state}
```

在 `model_node` 的 system prompt（`expert_runtime.py:530` 事实约束字符串）之后追加工具提示（非强制）：

```python
        if attachment_tools_factory is not None:
            messages[0] = (messages[0][0],
                messages[0][1] + "\n当前任务有附件证据工具 flowhub_attachment_list / flowhub_attachment_search；涉及附件结论时自行调用检索，引用格式 [附件@文档名:页码或路径:片段号]（序号与检索结果的 [seq] 对应）；无可用证据时明确说明「附件未解析/需人工核对」，不得编造。")
```

**(d) `execute_run`**：在 `repo_tools` 定义之后新增附件工具 factory：

```python
        async def attachment_tools(prompt):
            from flowhub_api.services.attachment_tools import create_attachment_tool_bundle
            return await create_attachment_tool_bundle(session, task, user)
```

并把 `build_graph(...)` 调用（`expert_runtime.py:828-831`）增加：

```python
                    attachment_tools_factory=attachment_tools if run.task_id and task else None,
```

**(e) `execute_run` 成功分支**：把 `expert_runtime.py:854-861` 的 quality/parsed 三行改为合并附件校验：

```python
        tool_trace = list(result.get('tool_trace') or [])
        quality_issues = list(run.quality_result['issues'])
        quality_issues.extend(check_attachment_citations(run.output or '', tool_trace))
        quality = run.quality_result['status']
        if quality_issues:
            quality = 'needs_human_review'
        run.quality_result = {'status': quality, 'issues': quality_issues[:3],
                              'contentHash': content_hash(run.output)}
        run.parsed = {**(run.parsed or {}), 'qualityStatus': quality,
            'qualityIssues': run.quality_result['issues'],
            'contentHash': run.quality_result['contentHash'],
            'attachmentEvidence': result.get('attachmentEvidence') or {}}
```

Note: 附件证据只在模型主动调用工具时进入上下文（`bundle.evidence_context` 注入），不再在 `snapshot()` 强制注入；`build_task_context` 的「【关联文档】」仍提供附件目录元数据，保证模型知道有哪些附件可选。

- [ ] **Step 7: 运行既有测试回归**

Run: `cd backend && uv run pytest tests/test_attachment_run.py tests/test_ai_fill.py tests/test_attachment_evidence.py -v`
Expected: PASS（`test_ai_fill.py` 应不受影响；`test_attachment_evidence.py` 用 conftest 版 `seeded` 后仍通过；`test_attachment_run.py` 5 个用例通过）。

- [ ] **Step 8: 提交**

```bash
git add backend/src/flowhub_api/services/attachment_tools.py backend/src/flowhub_api/services/expert_runtime.py backend/tests/conftest.py backend/tests/test_attachment_evidence.py backend/tests/test_attachment_run.py
git commit -m "feat: 附件证据工具 Bundle（flowhub_attachment_list/search），model_node 合并工具循环"
```

### Task 8: MCP 二次检索工具

**Files:**
- Modify: `backend/src/flowhub_api/services/mcp_server.py`（在 `get_task_context` 工具之后新增 `retrieve_attachment_evidence`）
- Test: `backend/tests/test_attachment_run.py`（追加用例）

**Interfaces:**
- Consumes: Task 6 `retrieve_more_evidence`。
- Produces: `@mcp.tool() async def retrieve_attachment_evidence(task_id: str, query: str, exclude_chunk_seq: str = "", depth: int = 1) -> str`（只读；权限走 `can_read_task`；上限：文档 ≤3 / 片段 ≤10 / 字符 ≤4000 / 深度 ≤3）。

- [ ] **Step 1: 追加 MCP 测试**

Append to `backend/tests/test_attachment_run.py`:

```python
@pytest.mark.asyncio
async def test_mcp_retrieve_attachment_evidence(seeded, monkeypatch):
    from flowhub_api.services import mcp_server
    from flowhub_api.services import attachment_evidence as svc

    session, task, admin, docs = seeded
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "备件缺货 120 单".encode())

    # 先构建一次，填充缓存
    await svc.build_attachment_evidence(session, task, admin, "备件")

    async def call(query, depth=1):
        token = mcp_server._current_user.set(admin)
        try:
            return await mcp_server.retrieve_attachment_evidence(task.id, query, "", depth)
        finally:
            mcp_server._current_user.reset(token)

    out = await call("备件")
    assert "[附件@" in out
    out_deep = await call("备件", depth=99)
    assert "深度超限" in out_deep
```

Note: `seeded` fixture 已在 Task 7 Step 1 移入 `backend/tests/conftest.py`，本 Task 直接使用；MCP 工具的 `retrieve_more_evidence` 在 `attachment_evidence` 模块内读取正文，因此测试 `monkeypatch.setattr(svc, "_load_bytes", ...)`（patch 到 `attachment_evidence` 模块）对其生效。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_run.py -v`
Expected: FAIL（`retrieve_attachment_evidence` 未定义）。

- [ ] **Step 3: 实现 MCP 工具**

Modify `backend/src/flowhub_api/services/mcp_server.py`，在 `get_task_context` 工具定义之后追加：

```python
@mcp.tool()
async def retrieve_attachment_evidence(task_id: str, query: str, exclude_chunk_seq: str = "", depth: int = 1) -> str:
    """按需检索任务附件证据片段（只读二次检索）。task_id: 任务 ID；query: 检索问题；exclude_chunk_seq: 已引用片段序号，逗号分隔；depth: 递归深度 1-3。返回带 [附件@文档:位置:片段号] 标注的证据片段。"""
    user = _current_user.get()
    from flowhub_api.services.attachment_evidence import retrieve_more_evidence

    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return "任务不存在"
        try:
            exclude = {int(part) for part in exclude_chunk_seq.split(",") if part.strip().isdigit()}
            result = await retrieve_more_evidence(session, task, user, query, exclude, depth)
        except Exception as exc:  # noqa: BLE001
            return f"检索失败：{exc}"
        if not result.injected:
            return "未检索到更多附件证据片段。"
        return "\n".join(
            f"[{c.seq}] [附件@{c.doc_name}:{c.location}:{c.seq}] {c.text[:300]}"
            for c in result.injected
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_run.py -v`
Expected: PASS（6 个用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/services/mcp_server.py backend/tests/conftest.py backend/tests/test_attachment_run.py backend/tests/test_attachment_evidence.py
git commit -m "feat: MCP 附件证据二次检索工具"
```

### Task 9: reparse-attachments 接口

**Files:**
- Modify: `backend/src/flowhub_api/routes/tasks.py`（文件末尾新增 `POST /{task_id}/reparse-attachments`）
- Test: `backend/tests/test_attachment_reparse.py`

**Interfaces:**
- Consumes: Task 6 `build_attachment_evidence`、`agent_context.can_read_task`。
- Produces: `POST /api/v1/tasks/{task_id}/reparse-attachments` → `{attachments: [{id,status,parser,cacheHit,durationMs,entriesOrPages,error}], injected: [{docName,location,seq,kind,text}], durationMs}`；只解析不调模型、不写 Run；审计动作 `attachment:reparse`。

- [ ] **Step 1: 写失败的测试**

Create `backend/tests/test_attachment_reparse.py`:

```python
"""reparse-attachments 接口：失败重试解析入口。"""
import pytest

from flowhub_api.db.session import SessionFactory
from flowhub_api.models.support import DocItem
from flowhub_api.models.workflow import TaskItem, WorkItem
from flowhub_api.services import attachment_evidence as svc


@pytest.fixture
async def seeded_task_with_doc():
    async with SessionFactory() as session:
        wi = WorkItem(id="WI-REP-001", type="issue", title="备件盘点", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-REP-001", wi_id=wi.id, title="分析备件盘点", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        doc = DocItem(id="d-rep", name="备件清单.txt", project="售后", scan="已扫描",
                      uploader="张三", size="1KB", time="t1", wi=wi.id, object_name="d-rep/x.txt")
        session.add(wi); session.add(task); session.add(doc)
        wi.start_values = {"attach": [{"id": doc.id, "name": doc.name}]}
        await session.commit()
        yield task.id, doc.id


def test_reparse_attachments_indexes(client, admin_headers, seeded_task_with_doc, monkeypatch):
    task_id, _doc_id = seeded_task_with_doc
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "备件缺货 120 单\n补货周期 7 天".encode())

    resp = client.post(f"/api/v1/tasks/{task_id}/reparse-attachments", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["attachments"], "应返回附件解析摘要"
    assert data["attachments"][0]["status"] == "indexed"
    assert data["attachments"][0]["parser"] == "plain"
    assert any("备件缺货" in c["text"] for c in data["injected"])
    assert any(c["location"].startswith("p") for c in data["injected"])


def test_reparse_attachments_denied_for_outsider(client, leader_headers, seeded_task_with_doc, monkeypatch):
    task_id, _doc_id = seeded_task_with_doc
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "内容".encode())
    resp = client.post(f"/api/v1/tasks/{task_id}/reparse-attachments", headers=leader_headers)
    assert resp.status_code == 403, resp.text
```

Note: `leader_headers`（张伟）不是该任务处理人/管理员 → `can_read_task` 403。

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/test_attachment_reparse.py -v`
Expected: FAIL（404：路由不存在）。

- [ ] **Step 3: 实现接口**

Append to `backend/src/flowhub_api/routes/tasks.py`（文件末尾）：

```python
@router.post("/{task_id}/reparse-attachments")
async def reparse_attachments(
    task_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """失败重试：仅重跑附件解析与证据注入，不调用模型、不写 Run。"""
    task = await session.get(TaskItem, task_id)
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务", http_status=403)
    from flowhub_api.services.attachment_evidence import build_attachment_evidence

    evidence = await build_attachment_evidence(session, task, user, task.brief or task.title)
    await AuditService(session).record(
        actor=user.name, action="attachment:reparse", target=f"{task.id} · 附件重试解析", result="success",
    )
    await session.commit()
    return ok({
        "attachments": [{
            "id": p.doc_id, "status": p.status, "parser": p.parser, "cacheHit": p.cache_hit,
            "durationMs": p.duration_ms, "entriesOrPages": p.entries_or_pages, "error": p.error,
        } for p in evidence.parsed],
        "injected": [{
            "docName": c.doc_name, "location": c.location, "seq": c.seq,
            "kind": c.kind, "text": c.text,
        } for c in evidence.injected],
        "durationMs": evidence.duration_ms,
    }, "附件解析完成")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/test_attachment_reparse.py -v`
Expected: PASS（2 个用例）。

- [ ] **Step 5: 提交**

```bash
git add backend/src/flowhub_api/routes/tasks.py backend/tests/test_attachment_reparse.py
git commit -m "feat: 任务附件失败重试解析接口"
```

### Task 10: 前端任务页附件证据面板

**Files:**
- Modify: `frontend/src/types/index.ts:520-539`（`ExpertRunBrief.parsed` 增加 `attachmentEvidence`）
- Create: `frontend/src/components/attachment-evidence-panel.tsx`
- Modify: `frontend/src/pages/node.tsx`（Expert 产出卡内渲染面板 + 重试解析刷新）

**Interfaces:**
- Consumes: 后端 `GET /tasks/{id}` 的 `expertRuns[].parsed.attachmentEvidence` 与 `POST /tasks/{id}/reparse-attachments`。
- Produces: `AttachmentEvidence` 类型；`AttachmentEvidencePanel({ taskId, evidence })` 组件。

- [ ] **Step 1: 定义 AttachmentEvidence 类型**

Modify `frontend/src/types/index.ts`，在 `ExpertRunBrief.parsed` 类型内（`formatRepair` 之后）追加字段：

```ts
    /** 附件证据：本轮送入模型的证据片段与来源定位（任务附件证据服务产出）。 */
    attachmentEvidence?: {
      candidates: Array<{ id: string; name: string; selected?: boolean; reason?: string; score?: number }>
      parsed: Array<{ id: string; status: string; parser: string; cacheHit: boolean; durationMs: number; entriesOrPages: number; error: string }>
      injected: Array<{ docId: string; docName: string; location: string; seq: number; kind: string; text: string }>
      totalChars: number
      durationMs: number
    }
```

- [ ] **Step 2: 创建附件证据面板组件**

Create `frontend/src/components/attachment-evidence-panel.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { FileText, RotateCw } from 'lucide-react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'
import type { AttachmentEvidence } from '../types'
import { toast } from './toast'

const STATUS_TONE: Record<string, string> = {
  indexed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  needs_ocr: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  failed: 'bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-300',
  skipped: 'bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400',
}

function statusLabel(status: string, cacheHit: boolean, selected: boolean): string {
  if (!selected) return '未使用'
  if (status === 'indexed') return cacheHit ? '缓存命中' : '已索引'
  if (status === 'needs_ocr') return '需 OCR'
  if (status === 'failed') return '失败'
  return status
}

export function AttachmentEvidencePanel({ taskId, evidence }: { taskId: string; evidence?: AttachmentEvidence }) {
  const [state, setState] = useState<AttachmentEvidence | undefined>(evidence)
  const [busy, setBusy] = useState(false)
  useEffect(() => setState(evidence), [evidence])

  const failed = (state?.parsed ?? []).some((p) => p.status === 'failed')
  const reparse = async () => {
    if (!taskId || busy) return
    setBusy(true)
    try {
      const d = await api.post<{ attachments: AttachmentEvidence['parsed']; injected: AttachmentEvidence['injected']; durationMs: number }>(
        `/api/v1/tasks/${taskId}/reparse-attachments`,
      )
      setState((prev) => ({ ...(prev ?? { candidates: [], totalChars: 0, durationMs: 0 }), parsed: d.attachments, injected: d.injected, durationMs: d.durationMs }))
      toast.success('附件解析完成')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '重试解析失败')
    } finally {
      setBusy(false)
    }
  }

  if (!state || (!state.parsed?.length && !state.injected?.length)) return null
  const selectedIds = new Set((state.candidates ?? []).filter((c) => c.selected).map((c) => c.id))

  return (
    <div className="mt-2.5 rounded-lg border border-slate-200 bg-white/60 px-2.5 py-2 dark:border-slate-700 dark:bg-slate-900/40">
      <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
        <span className="font-semibold text-slate-600 dark:text-slate-300">附件证据</span>
        <span className="text-slate-400">本轮送入 {state.injected?.length ?? 0} 片段 · {state.totalChars ?? 0} 字 · {(state.durationMs ?? 0) / 1000}s</span>
        {failed && (
          <button className="ml-auto inline-flex items-center gap-1 rounded-md border border-violet-300 px-2 py-0.5 font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:opacity-50 dark:border-violet-500/40 dark:text-violet-300"
            disabled={busy} onClick={() => void reparse()}>
            <RotateCw className={cn('h-3 w-3', busy && 'animate-spin')} />{busy ? '解析中…' : '重试解析'}
          </button>
        )}
      </div>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[11px] font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-200">展开证据片段与来源定位</summary>
        <div className="mt-2 space-y-1.5">
          {(state.candidates ?? []).map((c) => {
            const parsedEntry = state.parsed?.find((p) => p.id === c.id)
            const used = !!parsedEntry || c.selected === true
            return (
              <div key={c.id} className="flex items-center gap-2 text-[11px]">
                <FileText className="h-3 w-3 flex-none text-blue-500" />
                <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300" title={c.reason}>{c.name}</span>
                <span className={cn('flex-none rounded px-1.5 py-px font-medium', STATUS_TONE[parsedEntry?.status ?? 'skipped'] ?? STATUS_TONE.skipped)}>
                  {statusLabel(parsedEntry?.status ?? 'skipped', parsedEntry?.cacheHit ?? false, used)}
                </span>
              </div>
            )
          })}
          {(state.injected ?? []).map((chunk) => (
            <div key={`${chunk.docId}:${chunk.seq}`} className="rounded-md bg-slate-50 px-2 py-1.5 text-[11px] leading-relaxed text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              <span className="mr-1.5 rounded bg-violet-100 px-1 py-px font-mono text-[10px] text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                {chunk.docName}:{chunk.location}:{chunk.seq}
              </span>
              {chunk.text}
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
```

Note: `toast` 从 `frontend/src/components/dialogs.tsx` 导出（搜索确认导出名；若为 `toast` 对象含 `success/error` 则直接用）。若不存在独立 `toast` 模块，改为从 `../components/dialogs` 导入。`AttachmentEvidence` 类型需在 `frontend/src/types/index.ts` 顶层导出（命名导出），供组件 import——在 `ExpertRunBrief` 同文件内定义为 `export interface AttachmentEvidence`，再被 `ExpertRunBrief.parsed.attachmentEvidence` 引用。

- [ ] **Step 3: 在 node.tsx 集成面板**

Modify `frontend/src/pages/node.tsx`：

顶部 import 追加：

```tsx
import { AttachmentEvidencePanel } from '../components/attachment-evidence-panel'
```

在 Expert 产出卡 `latestRun.parsed?.warnings` 渲染块（`node.tsx:1078-1082`）之后插入：

```tsx
                  {latestRun.parsed?.attachmentEvidence && (
                    <AttachmentEvidencePanel taskId={activeTaskId ?? ''} evidence={latestRun.parsed.attachmentEvidence} />
                  )}
```

- [ ] **Step 4: 类型检查与构建**

Run: `cd frontend && npm run build`
Expected: `tsc -b && vite build` 通过，无类型错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types/index.ts frontend/src/components/attachment-evidence-panel.tsx frontend/src/pages/node.tsx
git commit -m "feat: 任务页附件证据面板（状态徽标/证据片段/重试解析）"
```

---

## Self-Review

- **Spec 覆盖**：附件证据服务（意图选择/解析/缓存/检索）→ Task 6；PDF 与扫描降级 → Task 3；DOCX/XLSX/PPTX → Task 2；Axure/普通 ZIP 与安全边界 → Task 4；版本缓存 → Task 5；OCR 协议 + RapidOCR → Task 1；附件证据工具 Bundle（`flowhub_attachment_list/search`）+ model_node 合并循环 + 质量校验 → Task 7；MCP 二次检索 → Task 8；reparse 接口 → Task 9；任务页状态徽标/证据/重试 → Task 10；保留预览（未改 `document-viewer-drawer.tsx`）。无缺口。
- **占位符扫描**：全部步骤含完整代码与命令，无 TBD/TODO。
- **类型一致性**：`async parse_attachment(doc, data, ocr=None)`（Task 2 定义，Task 3/4/6/7 调用一致）；`EvidenceChunk`/`ParsedAttachment`/`EvidenceResult`/`AttachmentCandidate` 字段跨 Task 一致；`cache_key`/`ProcessLRUAttachmentCache`/`get_ocr_adapter`/`retrieve_more_evidence`/`check_attachment_citations`/`create_attachment_tool_bundle` 命名在定义与使用处一致；`AttachmentToolBundle` 的 `candidates/parsed/injected` 在 Task 7 产生、Task 10 前端消费；`seeded` fixture 在 Task 7 Step 1 移入 `conftest.py` 供多文件共用。

---

## 执行交接（Execution Handoff）

Plan 已保存到 `docs/superpowers/plans/2026-09-08-attachment-intent-parsing.md`（10 个 Task，后端 9 个 + 前端 1 个，每个 Task 独立可测、独立提交）。

两个执行选项：

1. **Subagent-Driven（推荐）**：每个 Task 派发独立 subagent，任务间做两段式评审，迭代快、隔离好。
2. **Inline Execution**：本会话内用 executing-plans 顺序执行，带检查点批量推进。

选哪个？













