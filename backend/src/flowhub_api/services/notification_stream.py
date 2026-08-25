"""进程内站内通知实时分发。

通知始终先持久化；此模块只在提交成功后把新记录推送给当前进程中的 SSE 订阅者。
"""
from __future__ import annotations

import asyncio


class NotificationStreamHub:
    """按可见用户集合管理通知订阅，断连时由路由注销队列。"""

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue[dict], frozenset[str]] = {}

    async def subscribe(self, identities: set[str]) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers[queue] = frozenset(identities)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict]) -> None:
        self._subscribers.pop(queue, None)

    async def publish(self, notification: dict, target_user: str) -> None:
        for queue, identities in tuple(self._subscribers.items()):
            if target_user in ("", "admin") or target_user in identities:
                queue.put_nowait(notification)


notification_stream_hub = NotificationStreamHub()
