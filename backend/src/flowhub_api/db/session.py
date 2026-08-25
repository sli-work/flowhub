"""SQLAlchemy 2.0 async engine / session（PostgreSQL + psycopg）。"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from flowhub_api.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
# 注意：async 引擎 + psycopg 组合下 pool_pre_ping 可能触发 MissingGreenlet，
# 改用 pool_recycle 定期回收空闲连接（30 分钟），避免复用已断开的连接。
engine = create_async_engine(
    _settings.sqlalchemy_url,
    echo=_settings.debug,
    pool_pre_ping=False,
    pool_recycle=1800,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级 session。"""
    async with SessionFactory() as session:
        yield session
