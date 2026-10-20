from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from flowhub_api.services.tool_execution import authorize_tool, invocation_key, sanitize_result


def test_stable_invocation_key_distinguishes_generation_and_actor():
    assert invocation_key('run', 1, 'v', {'b': 2, 'a': 1}) == invocation_key('run', 1, 'v', {'a': 1, 'b': 2})
    assert invocation_key('run', 1, 'v', {}) != invocation_key('run', 2, 'v', {})


def test_result_redaction_is_recursive():
    assert sanitize_result({'token': 'bad', 'nested': [{'Authorization': 'bad', 'text': 'Bearer secret'}]}, ['secret']) == {'token': '[redacted]', 'nested': [{'Authorization': '[redacted]', 'text': 'Bearer [redacted]'}]}


@pytest.mark.asyncio
async def test_execution_rejects_missing_version_grant_even_for_admin():
    from flowhub_api.core.response import BizError
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(tool_bindings=[])))
    user = SimpleNamespace(id='user', roles=[SimpleNamespace(id='system_admin')])
    with pytest.raises(BizError):
        await authorize_tool(session, user=user, version=SimpleNamespace(id='v', risk='read'), task=None, run=None, expert_version_id='ev', source='expert')


@pytest.mark.asyncio
async def test_standalone_test_requires_admin():
    from flowhub_api.core.response import BizError
    user = SimpleNamespace(id='user', roles=[])
    with pytest.raises(BizError):
        await authorize_tool(SimpleNamespace(), user=user, version=SimpleNamespace(id='v', risk='read'), task=None, run=None, expert_version_id=None, source='test')


class Rows:
    def __init__(self, value=None, rowcount=1):
        self.value, self.rowcount = value, rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def executor_context(*, risk='read', approval='none', source='native', status='published'):
    from flowhub_api.services.tool_registry import contract_checksum
    contract = dict(input_schema={'type': 'object', 'additionalProperties': False}, output_schema={'type': 'object'},
                    config_json={'adapter': 'flowhub_attachment_list'}, risk=risk, approval=approval)
    version = SimpleNamespace(id='v', tool_id='t', status=status, **contract, checksum=contract_checksum(contract))
    tool = SimpleNamespace(id='t', slug='tool', source=source, status='active', server_id='server')
    async def get(model, key):
        if model.__name__ == 'ToolVersion':
            return version
        if model.__name__ == 'ToolDefinition':
            return tool
        return None
    session = SimpleNamespace(get=AsyncMock(side_effect=get), execute=AsyncMock(return_value=Rows()),
                              add=lambda item: None, begin_nested=lambda: Nested(), flush=AsyncMock(), commit=AsyncMock(), refresh=AsyncMock())
    user = SimpleNamespace(id='user', roles=[SimpleNamespace(id='system_admin')])
    return session, user, version, tool


@pytest.mark.asyncio
async def test_write_preview_is_durable_but_never_dispatches(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context(risk='write_draft')
    dispatch = AsyncMock()
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    result = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='preview', source='test', dry_run=True)
    assert result['status'] == 'succeeded'
    assert result['result']['preview'] is True
    dispatch.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_write_requires_durable_approval_before_dispatch(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context(risk='write_commit', approval='required', source='mcp')
    dispatch = AsyncMock()
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    result = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='pending', source='test')
    assert result['status'] == 'awaiting_approval'
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_read_dispatches_and_validates_result(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context()
    dispatch = AsyncMock(return_value={'documents': []})
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    result = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='read', source='test')
    assert result['status'] == 'succeeded'
    assert result['result'] == {'documents': []}
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_uncertain_remote_write_is_terminal_unknown(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context(risk='write_commit', approval='required', source='mcp')
    existing = SimpleNamespace(id='i', tool_version_id='v', expert_version_id=None, task_id=None, run_id=None, generation=1,
                               requested_by='user', source='test', input_json={}, result_json={}, status='queued', approval_status='approved', error='')
    session.execute.side_effect = [Rows(existing), Rows()]
    monkeypatch.setattr(execution, '_dispatch', AsyncMock(side_effect=TimeoutError('secret response text')))
    result = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='unknown', source='test')
    assert result['status'] == 'unknown'
    assert 'secret' not in result['error']
    session.execute.side_effect = None
    session.execute.return_value = Rows(existing)
    second = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='unknown', source='test')
    assert second == result


@pytest.mark.asyncio
@pytest.mark.parametrize('mutate', ['disable', 'checksum', 'unpublished', 'arguments'])
async def test_execution_rejects_invalid_live_contract(mutate):
    from flowhub_api.services.tool_execution import execute_tool
    from flowhub_api.core.response import BizError
    session, user, version, tool = executor_context()
    arguments = {}
    if mutate == 'disable':
        tool.status = 'disabled'
    elif mutate == 'checksum':
        version.risk = 'critical'
    elif mutate == 'unpublished':
        version.status = 'draft'
    else:
        arguments = {'foreign': 'id'}
    with pytest.raises(BizError):
        await execute_tool(session, user=user, tool_version_id='v', arguments=arguments, idempotency_key='invalid', source='expert' if mutate == 'unpublished' else 'test')


@pytest.mark.asyncio
async def test_executor_denies_idempotency_key_reuse_for_changed_arguments():
    from flowhub_api.services.tool_execution import execute_tool
    from flowhub_api.core.response import BizError
    session, user, version, tool = executor_context()
    existing = SimpleNamespace(tool_version_id='different', expert_version_id=None, task_id=None, run_id=None, generation=1,
                               requested_by='user', source='test', input_json={})
    session.execute.return_value = Rows(existing)
    with pytest.raises(BizError):
        await execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='used', source='test')


@pytest.mark.asyncio
async def test_frozen_run_grants_override_mutated_draft_grants(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    from flowhub_api.models import ExpertVersion, WorkItem
    from flowhub_api.core.response import BizError
    user = SimpleNamespace(id='user', roles=[], deleted=False)
    expert = SimpleNamespace(tool_bindings=[])
    wi = SimpleNamespace(status='in_progress')
    async def get(model, key):
        return expert if model is ExpertVersion else wi if model is WorkItem else None
    session = SimpleNamespace(get=AsyncMock(side_effect=get))
    task = SimpleNamespace(id='task', wi_id='wi', status='assigned')
    run = SimpleNamespace(expert_version_id='ev', requested_by='user', task_id='task',
                          config_snapshot={'tool_bindings': [{'tool_version_id': 'v', 'scope': 'current_work_item'}]})
    monkeypatch.setattr(execution, 'can_read_task', AsyncMock(return_value=True))
    assert await execution.authorize_tool(session, user=user, version=SimpleNamespace(id='v', risk='read'), task=task, run=run, expert_version_id='ev', source='expert')
    run.config_snapshot = {'tool_bindings': []}
    expert.tool_bindings = [{'tool_version_id': 'v'}]
    with pytest.raises(BizError):
        await execution.authorize_tool(session, user=user, version=SimpleNamespace(id='v', risk='read'), task=task, run=run, expert_version_id='ev', source='expert')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_status,wi_status', [('completed','in_progress'), ('cancelled','in_progress'), ('assigned','archived'), ('assigned','closed')])
async def test_write_rechecks_closed_and_archived_scope(monkeypatch, task_status, wi_status):
    import flowhub_api.services.tool_execution as execution
    from flowhub_api.core.response import BizError
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status=wi_status)))
    user = SimpleNamespace(id='u', roles=[SimpleNamespace(id='system_admin')])
    monkeypatch.setattr(execution, 'can_read_task', AsyncMock(return_value=True))
    with pytest.raises(BizError):
        await execution.authorize_tool(session, user=user, version=SimpleNamespace(id='v', risk='write_draft'),
             task=SimpleNamespace(id='t', wi_id='wi', status=task_status), run=None, expert_version_id=None, source='test')


@pytest.mark.asyncio
async def test_failed_native_automation_archive_can_retry_without_committing(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context(risk='write_draft')
    version.config_json = {'adapter': 'flowhub_document_archive'}
    from flowhub_api.services.tool_registry import contract_checksum
    version.checksum = contract_checksum(version)
    existing = SimpleNamespace(id='i', tool_version_id='v', expert_version_id='ev', task_id='t', run_id=None, generation=0,
                               requested_by='user', source='automation', input_json={}, result_json={}, status='failed', approval_status='not_required', error='old')
    session.execute.side_effect = [Rows(existing), Rows()]
    monkeypatch.setattr(execution, 'authorize_tool', AsyncMock(return_value={'tool_version_id': 'v'}))
    dispatch = AsyncMock(return_value={'documents': []})
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    result = await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, expert_version_id='ev',
                task=SimpleNamespace(id='t'), idempotency_key='retry', source='automation')
    assert result['status'] == 'succeeded'
    dispatch.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('run_id', [None, 'completed-run'])
async def test_automation_approval_requeues_automation_without_restarting_expert(run_id):
    from flowhub_api.routes.tool_registry import resume_automation_invocation
    invocation = SimpleNamespace(id='invocation', task_id='task', run_id=run_id, generation=1)
    job = SimpleNamespace(status='failed', result_json={'invocationId': 'invocation'}, payload_json={'sources': [{'id': 'doc', 'sha256': 'hash'}]},
                          requested_by='original-actor', claim_token='old', lease_until='old', finished_at='old', error='pending')
    session = SimpleNamespace(execute=AsyncMock(return_value=Rows(job)))
    await resume_automation_invocation(session, invocation, 'approved')
    assert job.status == 'queued'
    assert job.requested_by == 'original-actor'
    assert job.payload_json['sources'][0]['sha256'] == 'hash'
    assert job.claim_token == '' and job.lease_until is None and job.finished_at == ''


@pytest.mark.asyncio
async def test_automation_rejection_does_not_enqueue_execution():
    from flowhub_api.routes.tool_registry import resume_automation_invocation
    invocation = SimpleNamespace(id='i', task_id='t', run_id=None, generation=1)
    job = SimpleNamespace(status='failed', result_json={'invocationId': 'i'}, error='pending')
    session = SimpleNamespace(execute=AsyncMock(return_value=Rows(job)))
    await resume_automation_invocation(session, invocation, 'rejected')
    assert job.status == 'failed'
    assert job.result_json == {'invocationId': 'i', 'status': 'denied'}


@pytest.mark.asyncio
async def test_frozen_sources_reach_native_archive_without_becoming_model_input(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    import flowhub_api.services.document_archive as archive
    frozen = [{'id': 'doc', 'sha256': 'frozen-bytes'}]
    adapter = AsyncMock(return_value={'documents': []})
    monkeypatch.setattr(archive, 'archive_documents', adapter)
    result = await execution._dispatch(SimpleNamespace(), SimpleNamespace(source='native'),
                  SimpleNamespace(config_json={'adapter': 'flowhub_document_archive'}), {'source_document_ids': ['doc']},
                  SimpleNamespace(id='task'), SimpleNamespace(id='user'), None, frozen_sources=frozen)
    assert result == {'documents': []}
    assert adapter.await_args.kwargs['frozen_sources'] is frozen
    assert adapter.await_args.kwargs['source_document_ids'] == ['doc']


@pytest.mark.asyncio
@pytest.mark.parametrize('actor_status', ['disabled', 'inactive', 'suspended'])
async def test_inactive_actor_cannot_execute_even_as_administrator(actor_status):
    from flowhub_api.services.tool_execution import execute_tool
    from flowhub_api.core.response import BizError
    session, user, version, tool = executor_context()
    user.status = actor_status
    with pytest.raises(BizError):
        await execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='inactive', source='test')


@pytest.mark.asyncio
@pytest.mark.parametrize('task_status', ['returned', 'transferred'])
async def test_returned_and_transferred_tasks_allow_fresh_current_actor(monkeypatch, task_status):
    import flowhub_api.services.tool_execution as execution
    from flowhub_api.core.response import BizError
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status='in_progress')))
    user = SimpleNamespace(id='u', roles=[SimpleNamespace(id='system_admin')], status='active')
    monkeypatch.setattr(execution, 'can_read_task', AsyncMock(return_value=True))
    await execution.authorize_tool(session, user=user, version=SimpleNamespace(id='v', risk='write_draft'),
         task=SimpleNamespace(id='t', wi_id='wi', status=task_status), run=None, expert_version_id=None, source='test')


@pytest.mark.asyncio
async def test_executor_forwards_internal_frozen_sources(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    session, user, version, tool = executor_context()
    frozen = [{'id': 'doc', 'sha256': 'expected'}]
    dispatch = AsyncMock(return_value={'documents': []})
    monkeypatch.setattr(execution, '_dispatch', dispatch)
    await execution.execute_tool(session, user=user, tool_version_id='v', arguments={}, idempotency_key='frozen', source='test', frozen_sources=frozen)
    assert dispatch.await_args.kwargs['frozen_sources'] is frozen


def test_remote_context_injection_rejects_cross_work_item_arguments():
    from flowhub_api.services.tool_execution import scoped_mcp_arguments
    from flowhub_api.core.response import BizError
    version = SimpleNamespace(config_json={'context_bindings': {'work_item_id': 'work_item_id'}},
         input_schema={'type':'object','properties': {'work_item_id': {'type':'string'}, 'title': {'type':'string'}}})
    task = SimpleNamespace(id='task-current', wi_id='wi-current', project='project-current')
    assert scoped_mcp_arguments(version, {'title':'draft'}, task) == {'title':'draft','work_item_id':'wi-current'}
    with pytest.raises(BizError):
        scoped_mcp_arguments(version, {'work_item_id':'foreign'}, task)
    with pytest.raises(BizError):
        scoped_mcp_arguments(version, {'foreign_id':'outside'}, task)


@pytest.mark.asyncio
async def test_injected_scope_is_the_arguments_sent_to_mcp_transport(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    import flowhub_api.services.mcp_client as transport
    from flowhub_api.services.tool_registry import contract_checksum
    session, user, version, tool = executor_context(source='mcp')
    version.input_schema = {'type':'object','properties': {'work_item_id':{'type':'string'},'title':{'type':'string'}},'required':['work_item_id','title'],'additionalProperties':False}
    version.config_json = {'server_id':'server','endpoint':'https://remote.example/mcp','external_name':'create_draft','context_bindings':{'work_item_id':'work_item_id'}}
    version.checksum = contract_checksum(version)
    original_get = session.get
    async def get(model,key):
        if model.__name__ == 'McpServer':
            return SimpleNamespace(id='server',endpoint='https://remote.example/mcp',credentials='')
        return await original_get(model,key)
    session.get = AsyncMock(side_effect=get)
    monkeypatch.setattr(execution,'authorize_tool',AsyncMock(return_value={'tool_version_id':'v'}))
    call = AsyncMock(return_value={'id':'draft'})
    monkeypatch.setattr(transport,'call_tool',call)
    task = SimpleNamespace(id='task',wi_id='WI-current',project='Current')
    result = await execution.execute_tool(session,user=user,tool_version_id='v',arguments={'title':'Draft'},expert_version_id='ev',task=task,idempotency_key='scoped-call')
    assert result['status'] == 'succeeded'
    assert call.await_args.args[2] == {'title':'Draft','work_item_id':'WI-current'}


@pytest.mark.asyncio
async def test_stale_node_snapshot_cannot_write_after_task_moves(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    from flowhub_api.core.response import BizError
    user = SimpleNamespace(id='u',roles=[SimpleNamespace(id='system_admin')])
    run = SimpleNamespace(expert_version_id='ev',requested_by='u',task_id='t',config_snapshot={'node_id':'old'})
    task = SimpleNamespace(id='t',wi_id='wi',node_id='new',status='returned')
    with pytest.raises(BizError,match='任务节点已变更'):
        await execution.authorize_tool(SimpleNamespace(),user=user,version=SimpleNamespace(id='v',risk='write_draft'),task=task,run=run,expert_version_id='ev',source='test')


@pytest.mark.asyncio
async def test_runless_automation_approval_ledger_uses_generation_zero(monkeypatch):
    import flowhub_api.services.tool_execution as execution
    from flowhub_api.models.tool import ToolInvocation, ToolAutomationJob
    session,user,version,tool = executor_context(risk='write_draft',approval='required')
    recorded=[]
    session.add=recorded.append
    monkeypatch.setattr(execution,'authorize_tool',AsyncMock(return_value={'tool_version_id':'v'}))
    result=await execution.execute_tool(session,user=user,tool_version_id='v',arguments={},expert_version_id='ev',
         task=SimpleNamespace(id='task'),idempotency_key='runless-approval',source='automation')
    assert result['status']=='awaiting_approval'
    assert next(row for row in recorded if isinstance(row,ToolInvocation)).generation == 0
    assert ToolAutomationJob.__table__.columns.status.type.length >= len('awaiting_approval')
