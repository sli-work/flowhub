"""Cross-process chat serialization without holding a business transaction."""
import hashlib
from collections.abc import AsyncIterator
from sqlalchemy import text
from flowhub_api.core.response import BizCode, BizError
from flowhub_api.db.session import engine


async def guard_chat_session(session_id: str) -> AsyncIterator[None]:
    key = int.from_bytes(hashlib.sha256(('flowhub:chat:' + session_id).encode()).digest()[:8], 'big', signed=True)
    async with engine.connect() as connection:
        acquired = await connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': key})
        await connection.commit()
        if not acquired:
            raise BizError(BizCode.LOCKED, '该会话正在生成，请等待本轮完成后重试')
        try:
            yield
        finally:
            try:
                await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                await connection.commit()
            except BaseException:
                await connection.invalidate()
                raise
