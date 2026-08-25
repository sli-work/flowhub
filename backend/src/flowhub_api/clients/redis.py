"""Redis 客户端：惰性初始化（连接信息未配置时不阻塞启动）。"""
from functools import lru_cache

import redis.asyncio as aioredis

from flowhub_api.core.config import get_settings


@lru_cache
def get_redis() -> aioredis.Redis:
    s = get_settings()
    return aioredis.from_url(s.redis_dsn, decode_responses=True)
