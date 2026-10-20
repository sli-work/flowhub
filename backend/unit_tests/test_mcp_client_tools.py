from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import flowhub_api.services.mcp_client as transport


@pytest.mark.asyncio
async def test_discovery_fetches_actual_remote_contracts_and_paginates(monkeypatch):
    tool = SimpleNamespace(model_dump=lambda **_: {'name': 'lookup', 'inputSchema': {'type': 'object'}})
    client = SimpleNamespace(list_tools=AsyncMock(side_effect=[SimpleNamespace(tools=[tool], nextCursor='next'), SimpleNamespace(tools=[], nextCursor=None)]))
    @asynccontextmanager
    async def connect(server, **kwargs):
        assert kwargs['allow_unhealthy'] is True
        yield client
    monkeypatch.setattr(transport, 'connect', connect)
    assert await transport.discover_tools(object()) == [{'name': 'lookup', 'inputSchema': {'type': 'object'}}]
    assert client.list_tools.await_count == 2


@pytest.mark.asyncio
async def test_discovery_rejects_repeated_cursor(monkeypatch):
    client = SimpleNamespace(list_tools=AsyncMock(return_value=SimpleNamespace(tools=[], nextCursor='repeat')))
    @asynccontextmanager
    async def connect(server, **kwargs):
        yield client
    monkeypatch.setattr(transport, 'connect', connect)
    with pytest.raises(ValueError):
        await transport.discover_tools(object())


@pytest.mark.asyncio
async def test_actual_remote_call_preserves_structured_content_and_errors_are_safe(monkeypatch):
    result = SimpleNamespace(isError=False, model_dump=lambda **_: {'structuredContent': {'id': 'draft'}})
    client = SimpleNamespace(call_tool=AsyncMock(return_value=result))
    @asynccontextmanager
    async def connect(server):
        yield client
    monkeypatch.setattr(transport, 'connect', connect)
    assert await transport.call_tool(object(), 'write', {'title': 'x'}) == {'id': 'draft'}
    client.call_tool.assert_awaited_once_with('write', arguments={'title': 'x'})
    result.isError = True
    with pytest.raises(ValueError, match='Remote MCP tool returned an error'):
        await transport.call_tool(object(), 'write', {})


@pytest.mark.parametrize('auth_type,secret,expected', [('none','',{}), ('bearer','abc',{'Authorization':'Bearer abc'}), ('api_key','{"header":"X-Key","value":"abc"}',{'X-Key':'abc'})])
def test_credentials_are_transport_headers_only(monkeypatch, auth_type, secret, expected):
    monkeypatch.setattr(transport, 'decrypt_secret', lambda value: value)
    assert transport._headers(SimpleNamespace(credentials=secret, auth_type=auth_type)) == expected


@pytest.mark.parametrize('auth_type,secret', [('unknown','abc'),('header','{"header":"X-Key\\r\\nInjected","value":"abc"}')])
def test_unsupported_or_malicious_authentication_rejected(monkeypatch, auth_type, secret):
    monkeypatch.setattr(transport, 'decrypt_secret', lambda value: value)
    with pytest.raises(ValueError):
        transport._headers(SimpleNamespace(credentials=secret, auth_type=auth_type))
