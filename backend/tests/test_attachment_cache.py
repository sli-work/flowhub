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
