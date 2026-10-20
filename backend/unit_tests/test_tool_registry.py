import pytest
from flowhub_api.services.tool_registry import contract_checksum, validate_schema, validate_contract
from flowhub_api.services.mcp_client import validate_endpoint


def test_contract_checksum_is_canonical_and_freezes_execution_policy():
    first = dict(input_schema={'type': 'object'}, output_schema={}, config_json={'adapter': 'a'}, risk='read', approval='none')
    assert contract_checksum(first) == contract_checksum(dict(reversed(list(first.items()))))
    assert contract_checksum(first) != contract_checksum({**first, 'risk': 'write_commit'})


def test_json_schema_rejects_invalid_arguments_and_extra_properties():
    schema = {'type': 'object', 'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids'], 'additionalProperties': False}
    validate_schema(schema, {'ids': ['doc']})
    with pytest.raises(ValueError):
        validate_schema(schema, {'ids': [3]})
    with pytest.raises(ValueError):
        validate_schema(schema, {'ids': [], 'task_id': 'foreign'})


def test_contract_requires_object_and_strict_write_approval():
    with pytest.raises(ValueError):
        validate_contract({'input_schema': {'type': 'string'}, 'output_schema': {}, 'risk': 'read', 'approval': 'none'})
    with pytest.raises(ValueError):
        validate_contract({'input_schema': {'type': 'object'}, 'output_schema': {}, 'risk': 'critical', 'approval': 'none'})


@pytest.mark.parametrize('endpoint', ['file:///tmp/x', 'http://localhost/api/v1/mcp', 'http://127.0.0.1/mcp', 'https://user:secret@example.org/mcp', 'https://example.org/mcp#frag'])
def test_endpoint_rejects_self_and_credential_urls(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


@pytest.mark.asyncio
async def test_grants_reject_unpublished_disabled_duplicates_and_invalid_scope():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from flowhub_api.services.tool_registry import validate_tool_bindings
    version = SimpleNamespace(id='v', tool_id='t', status='published', input_schema={'type': 'object'}, output_schema={}, config_json={}, risk='read', approval='none')
    version.checksum = contract_checksum(version)
    tool = SimpleNamespace(status='active')
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model, identifier: version if identifier == 'v' else tool))
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v', 'scope': 'current_work_item', 'approval': 'inherit'}]) == []
    assert await validate_tool_bindings(session, {})
    assert await validate_tool_bindings(session, [None])
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v', 'scope': 'all_tasks'}])
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v', 'approval': 'none'}])
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v'}, {'tool_version_id': 'v'}])
    version.status = 'draft'
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v'}])
    version.status, tool.status = 'published', 'disabled'
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v'}])
    tool.status, version.checksum = 'active', 'tampered'
    assert await validate_tool_bindings(session, [{'tool_version_id': 'v'}])


def test_remote_scope_requires_explicit_context_mapping_and_rejects_unbound_targets():
    from flowhub_api.services.tool_registry import validate_context_bindings
    schema = {'type': 'object', 'properties': {'work_item_id': {'type': 'string'}, 'title': {'type': 'string'}}}
    with pytest.raises(ValueError):
        validate_context_bindings(schema, {}, require_scope=True)
    assert validate_context_bindings(schema, {'work_item_id': 'work_item_id'}, require_scope=True) == {'work_item_id': 'work_item_id'}
    for bindings in ({'absent': 'task_id'}, {'work_item_id': 'other'}, {'title': 'project'}):
        with pytest.raises(ValueError):
            validate_context_bindings(schema, bindings, require_scope=True)
    nested = {'type': 'object', 'properties': {'work_item_id': {'type': 'string'}, 'payload': {'type': 'object'}}}
    with pytest.raises(ValueError):
        validate_context_bindings(nested, {'work_item_id': 'work_item_id'}, require_scope=True)


@pytest.mark.asyncio
async def test_remote_grant_rejects_published_contract_without_enforceable_task_scope():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from flowhub_api.services.tool_registry import validate_tool_bindings
    version = SimpleNamespace(id='v',tool_id='t',status='published', input_schema={'type':'object','properties':{'work_item_id':{'type':'string'}}},output_schema={},config_json={},risk='write_commit',approval='required')
    version.checksum = contract_checksum(version)
    tool = SimpleNamespace(status='active',source='mcp')
    session = SimpleNamespace(get=AsyncMock(side_effect=lambda model,key:version if key=='v' else tool))
    assert await validate_tool_bindings(session,[{'tool_version_id':'v','scope':'current_work_item'}])
    version.config_json = {'context_bindings':{'work_item_id':'work_item_id'}}
    version.checksum = contract_checksum(version)
    assert await validate_tool_bindings(session,[{'tool_version_id':'v','scope':'current_work_item'}]) == []


@pytest.mark.parametrize('properties,bindings', [
    ({'project':{'type':'string'},'content':{'type':'string'}},{'project':'project'}),
    ({'work_item_id':{'type':'string'},'target':{'type':'string'},'content':{'type':'string'}},{'work_item_id':'work_item_id'}),
    ({'work_item_id':{'type':'string'},'destination':{'type':'string'}},{'work_item_id':'work_item_id'}),
])
def test_scope_rejects_project_only_and_unknown_target_aliases(properties,bindings):
    from flowhub_api.services.tool_registry import validate_context_bindings
    with pytest.raises(ValueError):
        validate_context_bindings({'type':'object','properties':properties},bindings,require_scope=True)
