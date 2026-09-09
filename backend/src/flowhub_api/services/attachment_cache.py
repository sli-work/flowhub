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
