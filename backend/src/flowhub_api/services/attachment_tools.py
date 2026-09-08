"""附件证据工具：flowhub_attachment_list / flowhub_attachment_search。

与 repo_mirror.RepoToolBundle 同构：模型按需选择调用，服务器控制预算与审计。
不把附件解析/注入逻辑写死在 LangGraph 业务流程里。
"""
import inspect
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
                data = _load_bytes(doc)
                if inspect.isawaitable(data):
                    data = await data
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
