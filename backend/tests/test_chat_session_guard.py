"""No database access: advisory lock ownership and cleanup contract."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flowhub_api.core.response import BizError
from flowhub_api.services import chat_session_guard


def fake_engine(monkeypatch, acquired=True):
    connection = AsyncMock()
    connection.scalar.return_value = acquired

    @asynccontextmanager
    async def connect():
        yield connection

    # AsyncEngine methods are descriptor-backed and cannot be patched on an
    # instance; replace the module dependency with the narrow fake instead.
    monkeypatch.setattr(chat_session_guard, 'engine', SimpleNamespace(connect=connect))
    return connection


def test_busy_session_rejected_without_unlocking_other_owner(monkeypatch):
    connection = fake_engine(monkeypatch, False)
    async def run():
        with pytest.raises(BizError) as error:
            await anext(chat_session_guard.guard_chat_session('s1'))
        assert error.value.status_code == 423
    asyncio.run(run())
    connection.execute.assert_not_awaited()


def test_acquired_lock_released_on_request_error(monkeypatch):
    connection = fake_engine(monkeypatch)
    async def run():
        guard = chat_session_guard.guard_chat_session('s1')
        await anext(guard)
        with pytest.raises(ValueError):
            await guard.athrow(ValueError('failed request'))
    asyncio.run(run())
    assert 'pg_advisory_unlock' in str(connection.execute.call_args.args[0])
    assert connection.commit.await_count == 2


def test_broken_unlock_invalidates_connection(monkeypatch):
    connection = fake_engine(monkeypatch)
    connection.execute.side_effect = RuntimeError('connection broken')
    async def run():
        guard = chat_session_guard.guard_chat_session('s1')
        await anext(guard)
        with pytest.raises(RuntimeError):
            await guard.aclose()
    asyncio.run(run())
    connection.invalidate.assert_awaited_once()
