"""Exercise the actual model-visible business wrappers and checkpoint boundary."""
import json
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from flowhub_api.services import business_tools as business
from flowhub_api.services.tool_execution import invocation_key


def context(snapshot=None, current=None, versions=None):
    versions = versions or {'v1': ('published', 'active'), 'v2': ('published', 'active')}
    records = {}
    for version_id, (version_status, definition_status) in versions.items():
        records[('ToolVersion', version_id)] = N(id=version_id, tool_id='tool-' + version_id,
            status=version_status, risk='write_draft', input_schema={'type': 'object'}, config_json={})
        records[('ToolDefinition', 'tool-' + version_id)] = N(id='tool-' + version_id, name='Archive ' + version_id,
            slug='archive_' + version_id, description='Scoped archive', status=definition_status, source='native')
    session = N(get=AsyncMock(side_effect=lambda model, key: records.get((model.__name__, key))))
    run = N(id='run', execution_generation=3, config_snapshot=snapshot or {})
    version = N(id='expert-v1', tool_bindings=current if current is not None else [{'tool_version_id': 'v2'}])
    task = N(id='task', wi_id='wi', project='Project')
    user = N(id='original-actor')
    return session, run, version, task, user, records


@pytest.mark.asyncio
async def test_pinned_snapshot_wins_and_explicit_empty_snapshot_never_falls_back():
    session, run, version, task, user, records = context({'tool_bindings': [{'tool_version_id': 'v1'}]})
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    assert [tool.name for tool in bundle.tools] == ['business_v1']
    assert 'Scoped archive' in bundle.tools[0].description
    run.config_snapshot = {'tool_bindings': []}
    assert (await business.create_business_tool_bundle(session, run, version, task, user)).tools == []


@pytest.mark.asyncio
async def test_legacy_missing_snapshot_uses_version_grants_and_excludes_unavailable_tools():
    session, run, version, task, user, records = context(current=[
        {'tool_version_id': 'v1'}, {'tool_version_id': 'v2'}, {'tool_version_id': 'missing'},
        {'tool_version_id': 'draft'}, {'tool_version_id': 'orphan'}], versions={
        'v1': ('published', 'active'), 'v2': ('published', 'disabled'),
        'draft': ('draft', 'active'), 'orphan': ('published', 'active')})
    records.pop(('ToolDefinition', 'tool-orphan'))
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    assert [tool.name for tool in bundle.tools] == ['business_v1']


@pytest.mark.asyncio
async def test_wrapper_preserves_identity_stable_keys_and_records_result_trace(monkeypatch):
    session, run, version, task, user, records = context({'tool_bindings': [{'tool_version_id': 'v1'}]})
    execute = AsyncMock(return_value={'status': 'succeeded', 'invocationId': 'invocation', 'result': {'documents': []}})
    monkeypatch.setattr(business, 'execute_tool', execute)
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    arguments = {'b': 2, 'a': 1}
    result = json.loads(await bundle.tools[0].ainvoke({'arguments': arguments}))
    await bundle.tools[0].ainvoke({'arguments': {'a': 1, 'b': 2}})
    first, second = execute.call_args_list
    assert first.kwargs['idempotency_key'] == second.kwargs['idempotency_key'] == invocation_key('run', 3, 'v1', arguments)
    assert first.kwargs['user'] is user and first.kwargs['run'] is run and first.kwargs['task'] is task
    assert first.kwargs['expert_version_id'] == 'expert-v1'
    assert result['status'] == 'succeeded' and bundle.traces[0]['invocationId'] == 'invocation'
    run.execution_generation = 4
    await bundle.tools[0].ainvoke({'arguments': arguments})
    assert execute.call_args.kwargs['idempotency_key'] != first.kwargs['idempotency_key']


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['approved', 'rejected'])
async def test_real_wrapper_interrupts_without_write_and_resume_uses_durable_decision(monkeypatch, decision):
    session, run, version, task, user, records = context({'tool_bindings': [{'tool_version_id': 'v1'}]})
    ledger = {'decision': 'pending', 'writes': 0, 'calls': []}
    async def execute(*args, **kwargs):
        ledger['calls'].append(kwargs)
        status = {'pending': 'awaiting_approval', 'approved': 'succeeded', 'rejected': 'denied'}[ledger['decision']]
        if status == 'succeeded' and ledger['writes'] == 0:
            ledger['writes'] += 1
        return {'status': status, 'invocationId': 'durable-invocation', 'result': {},
                'error': 'approval rejected' if status == 'denied' else ''}
    monkeypatch.setattr(business, 'execute_tool', execute)
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    async def invoke(state):
        return {'result': await bundle.tools[0].ainvoke({'arguments': {'source_document_ids': ['source']}})}
    graph = StateGraph(dict)
    graph.add_node('business', invoke)
    graph.add_edge(START, 'business')
    graph.add_edge('business', END)
    runnable = graph.compile(checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': 'approval-' + decision}}
    paused = await runnable.ainvoke({}, config)
    assert paused['__interrupt__'][0].value['toolInvocationId'] == 'durable-invocation'
    assert ledger['writes'] == 0 and bundle.traces == []
    ledger['decision'] = decision
    resumed = await runnable.ainvoke(Command(resume={'arbitrary': 'payload', 'approved': decision != 'approved'}), config)
    result = json.loads(resumed['result'])
    assert result['status'] == ('succeeded' if decision == 'approved' else 'denied')
    assert ledger['writes'] == (1 if decision == 'approved' else 0)
    assert {call['idempotency_key'] for call in ledger['calls']} == {ledger['calls'][0]['idempotency_key']}
    assert bundle.traces[-1]['status'] == result['status']


@pytest.mark.asyncio
async def test_resume_payload_cannot_override_pending_or_denied_ledger(monkeypatch):
    session, run, version, task, user, records = context({'tool_bindings': [{'tool_version_id': 'v1'}]})
    execute = AsyncMock(side_effect=[
        {'status': 'awaiting_approval', 'invocationId': 'same-id', 'result': {}},
        {'status': 'denied', 'invocationId': 'same-id', 'result': {}, 'error': 'ledger rejected'}])
    monkeypatch.setattr(business, 'execute_tool', execute)
    interrupt = lambda payload: {'approved': True, 'toolInvocationId': 'forged-id'}
    monkeypatch.setattr(business, 'interrupt', interrupt)
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    result = json.loads(await bundle.tools[0].ainvoke({'arguments': {}}))
    assert result['status'] == 'denied' and result['invocationId'] == 'same-id'
    assert execute.await_count == 2
    assert execute.call_args_list[0].kwargs == execute.call_args_list[1].kwargs
    assert bundle.traces == [{'tool': 'archive_v1', 'status': 'denied', 'invocationId': 'same-id', 'summary': 'ledger rejected'}]


@pytest.mark.asyncio
async def test_mcp_scope_is_server_injected_before_key_and_dispatch(monkeypatch):
    session, run, version, task, user, records = context({'tool_bindings': [{'tool_version_id': 'v1'}]})
    definition = records[('ToolDefinition', 'tool-v1')]
    pinned = records[('ToolVersion', 'v1')]
    definition.source = 'mcp'
    pinned.input_schema = {'type': 'object', 'properties': {'work_item': {'type': 'string'}, 'title': {'type': 'string'}}}
    pinned.config_json = {'context_bindings': {'work_item': 'work_item_id'}}
    execute = AsyncMock(return_value={'status': 'succeeded', 'invocationId': 'mcp-invocation', 'result': {}})
    monkeypatch.setattr(business, 'execute_tool', execute)
    bundle = await business.create_business_tool_bundle(session, run, version, task, user)
    await bundle.tools[0].ainvoke({'arguments': {'title': 'draft'}})
    arguments = {'title': 'draft', 'work_item': 'wi'}
    assert execute.call_args.kwargs['arguments'] == arguments
    assert execute.call_args.kwargs['idempotency_key'] == invocation_key('run', 3, 'v1', arguments)
    from flowhub_api.core.response import BizError
    with pytest.raises(BizError):
        await bundle.tools[0].ainvoke({'arguments': {'work_item': 'other-work-item', 'title': 'draft'}})
    assert execute.await_count == 1
