"""模型主导的附件基础读取工具，不做语义选档或自动结论。"""
import base64
import json
import time
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.services.agent_context import can_read_task
from flowhub_api.services.attachment_evidence import _DEFAULT_CACHE, _collect_attachments, _load_bytes, _parse_or_load
from flowhub_api.services.attachment_parsers import EvidenceChunk

_IMAGE_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
_MAX_READ_CHARS = 4_000
_MAX_RENDER_BYTES = 4 * 1024 * 1024


@dataclass
class AttachmentToolBundle:
    tools: list
    traces: list = field(default_factory=list)
    evidence_context: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    parsed: list = field(default_factory=list)
    injected: list = field(default_factory=list)
    operations: list = field(default_factory=list)
    duration_ms: int = 0


def _ext(doc) -> str:
    return doc.name.rsplit(".", 1)[-1].lower() if "." in doc.name else ""


def _citation(chunk) -> str:
    return f"[附件@{chunk.doc_name}:{chunk.location}:{chunk.seq}]"


async def create_attachment_tool_bundle(session, task, user, *, supports_vision: bool = False) -> AttachmentToolBundle:
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务", http_status=403)
    from flowhub_api.services.ocr import get_ocr_adapter

    cache, ocr, bundle = _DEFAULT_CACHE, get_ocr_adapter(), AttachmentToolBundle(tools=[])

    async def docs_by_id():
        return {doc.id: doc for doc in await _collect_attachments(session, task)}

    def record(tool: str, doc_id: str = "", location: str = "", status: str = "succeeded", summary: str = "", **more):
        bundle.operations.append({"tool": tool, "status": status, "docId": doc_id, "location": location, **more})
        bundle.traces.append({"tool": tool, "status": status, "summary": summary or location or "已完成"})

    async def parsed_doc(doc_id: str):
        doc = (await docs_by_id()).get(doc_id)
        if doc is None:
            raise ValueError("附件不存在、已删除或当前任务无权读取")
        parsed, _ = await _parse_or_load(doc, cache, ocr)
        bundle.parsed = [p for p in bundle.parsed if p.doc_id != doc_id] + [parsed]
        return doc, parsed

    async def list_attachments() -> str:
        docs = list((await docs_by_id()).values())
        bundle.candidates = [{"id": d.id, "name": d.name, "ext": _ext(d), "kind": d.kind or "", "size": d.size} for d in docs]
        record("flowhub_attachment_list", summary=f"列出 {len(docs)} 个可访问附件")
        return json.dumps({"attachments": bundle.candidates}, ensure_ascii=False)

    async def inspect_attachment(doc_id: str, offset: int = 0, limit: int = 100) -> str:
        """只返回可读取的位置目录；正文必须再通过 read 按需取得。"""
        started = time.monotonic(); doc, parsed = await parsed_doc(doc_id)
        start = max(0, int(offset))
        page_size = max(1, min(int(limit), 100))
        selected = parsed.chunks[start:start + page_size]
        locations = [{"location": c.location, "seq": c.seq, "kind": c.kind} for c in selected]
        elapsed = round((time.monotonic() - started) * 1000); bundle.duration_ms += elapsed
        record("flowhub_attachment_inspect", doc.id, status=parsed.status,
               summary=f"{doc.name}：目录 {start + 1}-{start + len(selected)}/{len(parsed.chunks)}", durationMs=elapsed)
        return json.dumps({"id": doc.id, "name": doc.name, "format": parsed.parser, "status": parsed.status,
                           "entriesOrPages": parsed.entries_or_pages, "locations": locations,
                           "offset": start, "nextOffset": start + len(selected),
                           "hasMore": start + len(selected) < len(parsed.chunks), "error": parsed.error}, ensure_ascii=False)

    def add_evidence(chunk: EvidenceChunk) -> None:
        key = (chunk.doc_id, chunk.location, chunk.seq)
        if any((item.doc_id, item.location, item.seq) == key for item in bundle.injected):
            return
        bundle.injected.append(chunk)
        bundle.evidence_context.append(f"【非可信附件事实｜{_citation(chunk)}】\n{chunk.text}")

    async def read_attachment(doc_id: str, location: str, seq: int | None = None, max_chars: int = _MAX_READ_CHARS) -> str:
        started = time.monotonic(); doc, parsed = await parsed_doc(doc_id)
        chunks = [c for c in parsed.chunks if c.location == location and (seq is None or c.seq == seq)]
        if not chunks:
            record("flowhub_attachment_read", doc.id, location, "failed", "定位不存在")
            return json.dumps({"error": "定位不存在；请先调用 inspect 获取 location 与 seq"}, ensure_ascii=False)
        chunk = chunks[0]; content = chunk.text[:max(1, min(int(max_chars), _MAX_READ_CHARS))]
        add_evidence(EvidenceChunk(doc_id=chunk.doc_id, doc_name=chunk.doc_name, location=chunk.location, seq=chunk.seq, kind=chunk.kind, text=content))
        elapsed = round((time.monotonic() - started) * 1000); bundle.duration_ms += elapsed
        record("flowhub_attachment_read", doc.id, location, summary=f"读取 {_citation(chunk)}", durationMs=elapsed)
        return f"{_citation(chunk)}\n{content}"

    async def find_attachment_text(doc_id: str, text: str, max_results: int = 10) -> str:
        started = time.monotonic(); doc, parsed = await parsed_doc(doc_id); query = text.strip().lower()
        if not query:
            return json.dumps({"error": "text 不能为空"}, ensure_ascii=False)
        matches = [c for c in parsed.chunks if query in c.text.lower()][:max(1, min(int(max_results), 20))]
        elapsed = round((time.monotonic() - started) * 1000); bundle.duration_ms += elapsed
        for chunk in matches:
            add_evidence(chunk)
        record("flowhub_attachment_find", doc.id, summary=f"{doc.name} 命中 {len(matches)} 处", durationMs=elapsed)
        return json.dumps({"matches": [{"location": c.location, "seq": c.seq, "kind": c.kind, "preview": c.text[:300]} for c in matches]}, ensure_ascii=False)

    async def render_attachment(doc_id: str):
        doc = (await docs_by_id()).get(doc_id)
        if doc is None:
            raise ValueError("附件不存在、已删除或当前任务无权读取")
        if not supports_vision:
            record("flowhub_attachment_render", doc.id, status="unavailable", summary="当前模型未启用视觉能力")
            return json.dumps({"error": "当前模型未启用视觉能力；请使用 read 或切换视觉模型"}, ensure_ascii=False)
        ext = _ext(doc)
        if ext not in _IMAGE_MIME:
            return json.dumps({"error": "当前仅支持 PNG/JPEG/WebP 原图渲染；请先使用 inspect/read"}, ensure_ascii=False)
        data = await _load_bytes(doc)
        if not data or len(data) > _MAX_RENDER_BYTES:
            return json.dumps({"error": "图片不可读或超过 4MB 渲染上限"}, ensure_ascii=False)
        image_chunk = EvidenceChunk(doc_id=doc.id, doc_name=doc.name, location="image", seq=1, kind="image", text="[图片已提供给视觉模型；仅可引用可见内容]")
        add_evidence(image_chunk)
        record("flowhub_attachment_render", doc.id, summary=f"渲染图片 {doc.name}")
        return {"content": [{"type": "text", "text": f"{_citation(image_chunk)} 非可信图片，仅提取可见事实。"}, {"type": "image_url", "image_url": {"url": f"data:{_IMAGE_MIME[ext]};base64,{base64.b64encode(data).decode()}"}}]}

    bundle.tools = [
        StructuredTool.from_function(coroutine=list_attachments, name="flowhub_attachment_list", description="列出当前任务可见附件。涉及附件时先调用。"),
        StructuredTool.from_function(coroutine=inspect_attachment, name="flowhub_attachment_inspect", description="分页读取指定附件的结构目录和可用 location/seq，不返回正文；正文请再调用 read。"),
        StructuredTool.from_function(coroutine=read_attachment, name="flowhub_attachment_read", description="按 inspect 返回的 location 和可选 seq 精确读取附件文本或表格。"),
        StructuredTool.from_function(coroutine=find_attachment_text, name="flowhub_attachment_find", description="在指定附件中作字面文本匹配，返回定位；不做语义检索。"),
        StructuredTool.from_function(coroutine=render_attachment, name="flowhub_attachment_render", description="向视觉模型读取图片附件。"),
    ]
    return bundle
