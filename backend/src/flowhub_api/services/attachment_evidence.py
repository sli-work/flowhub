"""附件证据服务：按任务意图选择附件 → 解析/缓存 → 检索证据片段 → 注入上下文。

权限与 agent_context 一致（403 语义不变）；只读取当前工作项已授权附件，
跳过 deleted / 含毒 / 无对象 的文档。解析预算、ZIP 安全校验见 spec「安全约束」。
"""
import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, replace

from sqlalchemy import select

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models.support import DocItem
from flowhub_api.models.workflow import TaskItem
from flowhub_api.services.agent_context import _file_ids, can_read_task
from flowhub_api.services.attachment_cache import ProcessLRUAttachmentCache, cache_key
from flowhub_api.services.attachment_parsers import PARSER_VERSION, EvidenceChunk, ParsedAttachment, parse_attachment

logger = logging.getLogger(__name__)

MAX_RETRIEVAL_DEPTH = 3
_MAX_DOC_BYTES = 50 * 1024 * 1024
# 模块级默认缓存：同一进程内多次 build/retrieve 共享解析结果（进程内缓存仅提速，不承担一致性）
_DEFAULT_CACHE = ProcessLRUAttachmentCache()


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


def _sync_read(minio, bucket: str, object_name: str) -> bytes:
    resp = minio.get_object(bucket, object_name)
    try:
        data = resp.read(_MAX_DOC_BYTES + 1)
    finally:
        resp.close()
        resp.release_conn()
    if len(data) > _MAX_DOC_BYTES:
        raise ValueError("文件过大（上限 50MB），跳过解析")
    return data


async def _load_bytes(doc: DocItem) -> bytes:
    """从 MinIO 读取文档对象（网络读取 offload 到线程，避免阻塞事件循环）；
    返回空 bytes 表示不可读（调用方按解析失败处理）。"""
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings

    minio = get_minio()
    if minio is None or not doc.object_name:
        return b""
    return await asyncio.to_thread(_sync_read, minio, get_settings().minio_bucket, doc.object_name)


async def _collect_attachments(session, task: TaskItem) -> list[DocItem]:
    """收集当前工作项可见附件（起始表单 + 已完成前序节点 + 追加信息中的引用）。

    无表单引用时兜底为该工作项全部可见文档（未删除 / 未含毒 / 有对象名）。
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    session: AsyncSession
    from flowhub_api.models.workflow import TaskAppend, WorkItem
    from flowhub_api.services.workflow import main_task_clause

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
    query = select(DocItem).where(DocItem.wi == task.wi_id,
                                  DocItem.deleted == False,  # noqa: E712
                                  DocItem.scan != "含毒", DocItem.object_name.is_not(None))
    if ids:
        query = query.where(DocItem.id.in_(ids))
    return list((await session.execute(query)).scalars().all())


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
        return replace(cached, cache_hit=True), True
    try:
        data = _load_bytes(doc)
        if inspect.isawaitable(data):
            data = await data
    except ValueError as exc:
        return ParsedAttachment(doc_id=doc.id, status="failed", parser="unknown",
                                error=str(exc)[:200]), False
    except Exception as exc:
        logger.warning("读取附件 %s 失败：%s", doc.id, exc)
        return ParsedAttachment(doc_id=doc.id, status="failed", parser="unknown",
                                error=f"读取失败：{str(exc)[:200]}"), False
    if not data:
        parsed = ParsedAttachment(doc_id=doc.id, status="failed", parser="unknown",
                                  error="文档内容不可读")
        return parsed, False
    parsed = await parse_attachment(doc, data, ocr)
    # 只缓存成功索引结果：failed/needs_ocr/skipped 不写缓存，保证修复原因后重试会真实重新解析
    if parsed.status == "indexed":
        await cache.set(key, parsed)
    return parsed, False


async def build_attachment_evidence(
    session, task: TaskItem, user, question: str, output_requirements: str = "",
    *, limit_docs: int = 5, limit_chunks: int = 20, limit_chars: int = 8000,
    timeout_ms: int = 15_000, cache=None, ocr=None,
) -> EvidenceResult:
    """意图选择 → 解析/缓存 → 检索 → 注入。超时降级为当前已完成片段，不抛错。"""
    started = time.monotonic()
    if not await can_read_task(session, user, task):  # 403 语义不变
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务附件证据（仅任务处理人或管理员可见）", http_status=403)
    cache = cache or _DEFAULT_CACHE
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
        parsed, _ = await _parse_or_load(candidate.doc, cache, ocr)
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
    if depth < 1 or depth > MAX_RETRIEVAL_DEPTH:
        raise ValueError(f"检索深度超限（最大 {MAX_RETRIEVAL_DEPTH}）")
    if not await can_read_task(session, user, task):
        raise BizError(BizCode.PERM_DENIED, "无权限读取该任务附件证据（仅任务处理人或管理员可见）", http_status=403)
    cache = cache or _DEFAULT_CACHE
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
            logger.debug("二次检索：缓存未命中，跳过 %s（不重新下载）", doc.id)
            continue
        parsed_list.append(replace(parsed, cache_hit=True))
        if parsed.status == "indexed":
            chunks_by_doc[parsed.doc_id] = [
                c for c in parsed.chunks if c.seq not in exclude
            ]
    injected = _rank_chunks(chunks_by_doc, keywords, limit_chunks, limit_chars)
    return EvidenceResult(candidates=candidates, parsed=parsed_list, injected=injected,
                          total_chars=sum(len(c.text) for c in injected), duration_ms=0)
