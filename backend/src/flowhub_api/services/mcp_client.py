"""Real outbound Streamable HTTP MCP transport. Credentials stay inside the adapter."""
from contextlib import asynccontextmanager
import ipaddress
import json
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from flowhub_api.services.crypto import decrypt_secret


def validate_endpoint(endpoint: str) -> str:
    url = urlsplit(endpoint)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('MCP endpoint must be an HTTP(S) URL without credentials or fragments')
    host = url.hostname.lower().rstrip('.')
    if host in {'localhost', 'host.docker.internal', 'metadata.google.internal'} or host.endswith('.localhost'):
        raise ValueError('Local/application MCP endpoints are not permitted')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast):
        raise ValueError('Local and metadata MCP endpoints are not permitted')
    # The application publishes its own MCP transport at /mcp. Refuse its configured
    # public origin as well as the known local addresses, while allowing approved LAN servers.
    from flowhub_api.core.config import get_settings
    settings = get_settings()
    for field in ('public_base_url', 'app_base_url', 'frontend_url', 'api_base_url'):
        origin = getattr(settings, field, '')
        if origin and urlsplit(origin).hostname == host:
            raise ValueError('Application endpoints cannot be outbound MCP servers')
    return endpoint


def _headers(server) -> dict[str, str]:
    if not server.credentials:
        return {}
    secret = decrypt_secret(server.credentials)
    if server.auth_type == 'bearer':
        return {'Authorization': f'Bearer {secret}'}
    if server.auth_type in {'api_key', 'header'}:
        config = json.loads(secret)
        name, value = config.get('header', 'X-API-Key'), config.get('value', config.get('api_key', ''))
        if not isinstance(name, str) or not isinstance(value, str) or '\n' in name + value or '\r' in name + value:
            raise ValueError('Invalid MCP authentication configuration')
        return {name: value}
    if server.auth_type != 'none':
        raise ValueError('Unsupported outbound MCP authentication')
    return {}


@asynccontextmanager
async def connect(server, *, allow_unhealthy=False):
    if server.deleted or server.direction != 'outbound' or server.transport != 'streamable-http':
        raise ValueError('Only active outbound Streamable HTTP MCP servers are supported')
    if server.status not in {'active', 'healthy'} and not (allow_unhealthy and server.status == 'unhealthy'):
        raise ValueError('MCP server is not active')
    endpoint = validate_endpoint(server.endpoint)
    async with streamablehttp_client(endpoint, headers=_headers(server), timeout=30, sse_read_timeout=60) as (read, write, _):
        async with ClientSession(read, write) as client:
            await client.initialize()
            yield client


async def discover_tools(server) -> list[dict]:
    tools, cursor, seen_cursors = [], None, set()
    async with connect(server, allow_unhealthy=True) as client:
        while True:
            result = await client.list_tools(cursor=cursor)
            tools.extend(tool.model_dump(mode='json', by_alias=True, exclude_none=True) for tool in result.tools)
            cursor = result.nextCursor
            if not cursor:
                break
            if cursor in seen_cursors or len(tools) > 1000:
                raise ValueError('MCP discovery pagination is invalid or exceeded 1000 tools')
            seen_cursors.add(cursor)
    return tools


async def call_tool(server, name: str, arguments: dict) -> dict:
    async with connect(server) as client:
        result = await client.call_tool(name, arguments=arguments)
        if result.isError:
            # Do not expose remote exceptions or credentials echoed by a server.
            raise ValueError('Remote MCP tool returned an error')
        payload = result.model_dump(mode='json', by_alias=True, exclude_none=True)
        return payload['structuredContent'] if payload.get('structuredContent') is not None else {'content': payload.get('content', [])}
