"""MinIO 对象存储客户端：惰性初始化，启动时自动确保 bucket 存在（连接失败不阻塞启动，仅记录）。"""
import logging
from functools import lru_cache

import urllib3

from minio import Minio

from flowhub_api.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_minio() -> Minio | None:
    s = get_settings()
    try:
        client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
            # 连接 5s / 读取 60s 超时：MinIO 未就绪时快速失败，避免阻塞请求线程
            http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=60)),
        )
        if not client.bucket_exists(s.minio_bucket):
            client.make_bucket(s.minio_bucket)
        return client
    except Exception as exc:  # noqa: BLE001 — 连接信息未配置时优雅降级
        logger.warning("MinIO 未就绪（跳过）：%s", exc)
        return None
