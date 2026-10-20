"""Tool Center API integration; uses the guarded disposable integration database."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


def test_catalog_seeded_and_native_configuration_is_readonly(client, org_headers, dev_headers):
    response = client.get('/api/v1/tools', headers=org_headers)
    assert response.status_code == 200, response.text
    tools = response.json()['data']['items']
    archive = next(item for item in tools if item['slug'] == 'flowhub_document_archive')
    assert archive['versions'][0]['status'] == 'published'
    assert 'credentials' not in str(tools)
    denied = client.patch(f"/api/v1/tools/{archive['id']}", headers=dev_headers, json={'status': 'disabled'})
    assert denied.status_code == 403
    version = archive['versions'][0]
    changed = client.post(f"/api/v1/tools/{archive['id']}/versions", headers=org_headers,
                          json={'input_schema': version['inputSchema'], 'output_schema': version['outputSchema'],
                                'risk': 'write_draft', 'approval': 'none', 'config_json': {'adapter': 'arbitrary_script'}})
    assert changed.status_code == 400


def test_write_preview_is_not_a_publishable_real_test(client, org_headers):
    tool_id = 'tool-native-document-archive'
    result = client.post(f'/api/v1/tools/{tool_id}/versions/tv-native-document-archive-1/test', headers=org_headers,
                         json={'arguments': {}, 'dry_run': True})
    assert result.status_code == 200, result.text
    data = result.json()['data']
    assert data['result']['preview'] is True
    assert data['status'] == 'succeeded'
    listed = client.get('/api/v1/tool-invocations', headers=org_headers).json()['data']['items']
    assert any(item['id'] == data['invocationId'] and item['result']['preview'] for item in listed)


def test_real_mcp_discovery_approval_execution_and_publish(client, org_headers, monkeypatch):
    import flowhub_api.services.mcp_client as mcp_client
    import flowhub_api.services.tool_execution as execution
    discovered = AsyncMock(return_value=[{'name': 'create_draft', 'description': 'Create a draft',
                                          'inputSchema': {'type': 'object', 'properties': {'title': {'type': 'string'}}, 'required': ['title'], 'additionalProperties': False},
                                          'outputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}}])
    monkeypatch.setattr(mcp_client, 'discover_tools', discovered)
    server = client.post('/api/v1/mcp-servers', headers=org_headers,
                         json={'name': 'Tool registry integration server', 'endpoint': 'https://mcp.example.org/tools', 'direction': 'outbound', 'transport': 'streamable-http', 'auth_type': 'none'})
    assert server.status_code == 200, server.text
    server_id = server.json()['data']['server']['id']
    response = client.post(f'/api/v1/mcp-servers/{server_id}/discover-tools', headers=org_headers)
    assert response.status_code == 200, response.text
    tool = response.json()['data']['items'][0]
    version = tool['versions'][0]
    assert version['status'] == 'draft'
    assert version['approval'] == 'required'
    assert discovered.await_count == 1
    # Re-discovery of the exact contract is idempotent.
    again = client.post(f'/api/v1/mcp-servers/{server_id}/discover-tools', headers=org_headers).json()['data']['items'][0]
    assert len(again['versions']) == 1
    prefix = f"/api/v1/tools/{tool['id']}/versions/{version['id']}"
    assert client.post(f'{prefix}/publish', headers=org_headers).status_code == 400
    preview = client.post(f'{prefix}/test', headers=org_headers, json={'arguments': {'title': 'draft'}}).json()['data']
    assert preview['result']['preview'] is True
    assert client.post(f'{prefix}/publish', headers=org_headers).status_code == 400
    dispatch = AsyncMock(return_value={'id': 'remote-draft'})
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    requested = client.post(f'{prefix}/test', headers=org_headers, json={'arguments': {'title': 'draft'}, 'dry_run': False})
    assert requested.status_code == 200, requested.text
    invocation = requested.json()['data']
    assert invocation['status'] == 'awaiting_approval'
    dispatch.assert_not_awaited()
    approved = client.post(f"/api/v1/tool-invocations/{invocation['invocationId']}/decision", headers=org_headers, json={'decision': 'approved'})
    assert approved.status_code == 200, approved.text
    assert approved.json()['data']['status'] == 'succeeded'
    dispatch.assert_awaited_once()
    assert client.post(f'{prefix}/publish', headers=org_headers).status_code == 200
    assert client.post(f"/api/v1/tool-invocations/{invocation['invocationId']}/decision", headers=org_headers, json={'decision': 'approved'}).status_code == 409
    published = client.get(f"/api/v1/tools/{tool['id']}", headers=org_headers).json()['data']
    assert published['currentVersionId'] == version['id']
