"""数据库配置的内存快照：启动加载、页面保存后立即生效。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.config import get_settings
from flowhub_api.models import RuntimeConfig
from flowhub_api.services.crypto import decrypt_secret

_values: dict[str, str] = {}

async def load_runtime_config(session: AsyncSession) -> None:
    global _values
    rows = (await session.execute(select(RuntimeConfig))).scalars().all()
    _values = {row.key: (decrypt_secret(row.value) if row.secret and row.value else row.value) for row in rows}

def settings() -> object:
    """DB 配置覆盖到 Settings 副本；字符串值按字段类型转换（如 smtp_port: int）。"""
    base = get_settings()
    typed = {}
    for key, value in _values.items():
        if not hasattr(base, key):
            continue
        annotation = type(getattr(base, key))
        try:
            typed[key] = annotation(value) if annotation not in (str, type(None)) else value
        except (TypeError, ValueError):
            typed[key] = value
    return base.model_copy(update=typed)

def update_cache(values: dict[str, str]) -> None:
    _values.update(values)
