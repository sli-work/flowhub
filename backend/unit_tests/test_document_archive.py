"""Archive regression tests use an isolated in-memory ledger and object store."""
from io import BytesIO
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest

from flowhub_api.services import document_archive as archive

SUMMARY_MARKDOWN = '# Delivery summary\n\nThe delivery implements independent acceptance archives for the adopted predecessor files. Each archived document keeps its original bytes, sensitivity and source audit information. The generated summary is stored separately and remains a draft for the assigned human reviewer.\n\n## Verification and remaining review\n\nRegression checks cover authorization, deterministic archive keys, failed storage writes, duplicate backfill requests and approval resumption. The workflow task stays assigned with an unchanged form. A human must inspect the linked evidence and decide whether the delivery satisfies the acceptance criteria.'


class Store:
    def __init__(self):
        self.objects = {'source/key': b'original bytes'}
    def get_object(self, bucket, key):
        response = BytesIO(self.objects[key])
        response.release_conn = lambda: None
        return response
    def put_object(self, bucket, key, stream, length, **kwargs):
        self.objects[key] = stream.read()


class Session:
    def __init__(self, source):
        self.source, self.rows = source, []
    async def get(self, model, key, **kwargs):
        if model.__name__ == 'TaskItem':
            return self.task
        if key == self.source.id:
            return self.source
        return next((row for row in self.rows if getattr(row, 'id', None) == key), None)
    async def execute(self, *args, **kwargs):
        pass
    def begin_nested(self):
        return self
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def add(self, row):
        self.rows.append(row)
    async def flush(self):
        pass


@pytest.fixture
def setup(monkeypatch):
    source = N(id='source', name='result.md', wi='wi', project='project', level='L3',
               scan='已扫描', deleted=False, object_name='source/key', kind='节点表单附件', version='v1')
    task = N(id='accept', wi_id='wi', node_id='acceptance', project='project', status='assigned', assignee='Alice')
    user = N(id='alice', name='Alice', account='alice', status='active', deleted=False)
    store = Store()
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    monkeypatch.setattr(archive, 'authorize_archive', AsyncMock())
    monkeypatch.setattr(archive, 'adopted_upstream_references', AsyncMock(return_value={'source': {'taskId': 'done', 'field': 'result'}}))
    session = Session(source)
    session.task = task
    return session, source, task, user, store


@pytest.mark.asyncio
async def test_archive_physically_copies_preserves_sensitivity_and_is_idempotent(setup):
    session, source, task, user, store = setup
    result = await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    doc = next(row for row in session.rows if getattr(row, 'kind', '') == '验收归档')
    assert doc.object_name != source.object_name
    assert store.objects[doc.object_name] == b'original bytes'
    assert doc.level == 'L3' and doc.provenance['sourceDocumentId'] == 'source'
    store.objects.pop(source.object_name)
    assert store.objects[doc.object_name] == b'original bytes'
    store.objects[source.object_name] = b'original bytes'
    again = await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    assert again == result
    assert len([row for row in session.rows if getattr(row, 'kind', '') == '验收归档']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'wi': 'other'}, {'scan': '含毒'}, {'deleted': True}, {'object_name': None}])
async def test_archive_rejects_unsafe_missing_or_cross_work_item_source(setup, change):
    session, source, task, user, store = setup
    for key, value in change.items():
        setattr(source, key, value)
    with pytest.raises(ValueError):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    assert not session.rows


@pytest.mark.asyncio
async def test_archive_rejects_unadopted_reference_and_missing_storage(setup, monkeypatch):
    session, source, task, user, store = setup
    with pytest.raises(ValueError, match='adopted'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['initial'])
    monkeypatch.setattr(archive, 'get_minio', lambda: None)
    with pytest.raises(ValueError, match='storage'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])


@pytest.mark.asyncio
async def test_changed_bytes_create_a_new_archive_revision(setup):
    session, source, task, user, store = setup
    first = await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    store.objects[source.object_name] = b'revised'
    second = await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    assert first['documents'][0]['id'] != second['documents'][0]['id']


@pytest.mark.asyncio
async def test_storage_write_failure_never_creates_metadata_success(setup, monkeypatch):
    session, source, task, user, store = setup
    def fail(*args, **kwargs):
        raise OSError('unavailable')
    monkeypatch.setattr(store, 'put_object', fail)
    with pytest.raises(ValueError, match='write'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    assert not session.rows


def test_only_declared_file_upload_fields_are_selected():
    schema = [{'key': 'file', 'type': 'FILE'}, {'key': 'upload', 'type': 'upload'}, {'key': 'text', 'type': 'textarea'}]
    values = {'file': [{'id': 'a'}], 'upload': [{'id': 'b'}], 'text': [{'id': 'secret'}], 'unknown': [{'id': 'c'}]}
    assert archive.file_references(schema, values) == {'a': 'file', 'b': 'upload'}


def task_item(id, node_id, *, status='completed', lineage=None, parent=None, source=None):
    return N(id=id, node_id=node_id, status=status, lineage_root_id=lineage,
             parent_task_id=parent, created_at=id, source=source)


def test_predecessors_exclude_initial_uncompleted_siblings_and_old_revisions():
    task = task_item('z', 'accept', status='assigned')
    canvas = N(nodes=[{'id': 'start', 'type': 'start'}, {'id': 'dev', 'type': 'task'},
                      {'id': 'other', 'type': 'task'}, {'id': 'accept', 'type': 'acceptance'}],
               edges=[['start', 'dev'], ['dev', 'accept'], ['other', 'accept']])
    tasks = [task, task_item('a', 'start'), task_item('b', 'dev'), task_item('c', 'dev'),
             task_item('d', 'other', status='cancelled'), task_item('e', 'dev', lineage='sibling'),
             task_item('f', 'dev', source='issue:1')]
    assert [item.id for item in archive._completed_predecessors(task, tasks, canvas)] == ['c']


def test_split_lineage_can_inherit_parent_but_not_other_branches():
    task = task_item('accept', 'accept', status='assigned', lineage='root')
    root = task_item('root', 'dev', lineage='root', parent='parent')
    parent = task_item('parent', 'design')
    sibling = task_item('sibling', 'dev', lineage='other')
    canvas = N(nodes=[{'id': 'design', 'type': 'task'}, {'id': 'dev', 'type': 'task'},
                      {'id': 'join', 'type': 'parallel_join'}, {'id': 'accept', 'type': 'acceptance'}],
               edges=[['design', 'dev'], ['dev', 'join'], ['join', 'accept']])
    assert {item.id for item in archive._completed_predecessors(task, [task, root, parent, sibling], canvas)} == {'root', 'parent'}


@pytest.mark.asyncio
async def test_authority_never_falls_back_to_admin_roles(monkeypatch):
    from flowhub_api.services import agent_context
    from flowhub_api.core.response import BizError
    participant = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_context, '_is_task_participant', participant)
    user = N(deleted=False, status='active', roles=[N(id='system_admin', perms={'document:upload': True})])
    task = N(status='assigned', wi_id='wi', node_id='accept')
    wi = N(status='in_progress')
    session = N(get=AsyncMock(return_value=wi))
    canvas = N(nodes=[{'id': 'accept', 'type': 'end'}])
    monkeypatch.setattr(archive, 'frozen_canvas', AsyncMock(return_value=canvas))
    with pytest.raises(ValueError, match='current task actor'):
        await archive.authorize_archive(session, task, user)
    user.deleted = True
    with pytest.raises(ValueError, match='not active'):
        await archive.authorize_archive(session, task, user)
    user.deleted, task.status = False, 'completed'
    with pytest.raises(ValueError, match='no longer active'):
        await archive.authorize_archive(session, task, user)
    task.status, participant.return_value = 'assigned', True
    await archive.authorize_archive(session, task, user)
    wi.status = 'archived'
    with pytest.raises(ValueError, match='work item'):
        await archive.authorize_archive(session, task, user)
    wi.status, canvas.nodes[0]['type'] = 'in_progress', 'task'
    with pytest.raises(ValueError, match='acceptance end'):
        await archive.authorize_archive(session, task, user)
    user.roles[0].perms = {}
    with pytest.raises(BizError):
        await archive.authorize_archive(session, task, user)


@pytest.mark.asyncio
async def test_independent_summary_archive_requires_validated_content_and_keeps_pending_review(setup, monkeypatch):
    session, source, task, user, store = setup
    validated = AsyncMock()
    monkeypatch.setattr(archive, 'validated_summary', validated)
    run = N(id='run', execution_generation=2)
    result = await archive.archive_documents(session, task=task, user=user,
        source_document_ids=[], run=run, summary_content=SUMMARY_MARKDOWN)
    validated.assert_awaited_once_with(session, task, run, SUMMARY_MARKDOWN)
    doc = next(row for row in session.rows if getattr(row, 'kind', '') == '验收归档')
    assert doc.provenance['origin'] == 'AI生成'
    assert doc.provenance['reviewStatus'] == 'pending_human_review'
    assert result['reviewStatus'] == 'pending_human_review'
    assert store.objects[doc.object_name] == SUMMARY_MARKDOWN.encode()
    assert task.status == 'assigned'


@pytest.mark.asyncio
async def test_summary_schema_and_hash_must_match(setup, monkeypatch):
    from flowhub_api.services.runtime_quality import content_hash
    session, source, task, user, store = setup
    monkeypatch.setattr(archive, 'frozen_canvas', AsyncMock(return_value=N(nodes=[{
        'id': 'acceptance', 'cfg': {'schema': [{'key': 'summaryContent', 'type': 'textarea', 'required': True}]}}])))
    run = N(id='run', task_id=task.id, status='succeeded', output='output', parsed={'values': {'summaryContent': SUMMARY_MARKDOWN}},
            quality_result={'status': 'passed', 'contentHash': content_hash('output')})
    await archive.validated_summary(session, task, run, SUMMARY_MARKDOWN)
    with pytest.raises(ValueError, match='does not match'):
        await archive.validated_summary(session, task, run, 'Unvalidated summary')
    run.output = 'changed'
    with pytest.raises(ValueError, match='does not match'):
        await archive.validated_summary(session, task, run, SUMMARY_MARKDOWN)
    with pytest.raises(ValueError, match='successful'):
        await archive.validated_summary(session, task, None, SUMMARY_MARKDOWN)


@pytest.mark.asyncio
async def test_snapshot_freezes_hash_and_upstream_provenance(setup):
    session, source, task, user, store = setup
    snapshot = await archive.snapshot_sources(session, task)
    assert snapshot[0]['id'] == source.id
    assert len(snapshot[0]['sha256']) == 64
    assert snapshot[0]['taskId'] == 'done'


def test_read_failure_and_size_limits(setup, monkeypatch):
    session, source, task, user, store = setup
    source.object_name = 'missing'
    with pytest.raises(ValueError, match='read failed'):
        archive.read_document_bytes(store, source)
    source.object_name = 'source/key'
    monkeypatch.setattr(archive, 'MAX_ARCHIVE_BYTES', 2)
    with pytest.raises(ValueError, match='read failed'):
        archive.read_document_bytes(store, source)


def test_existing_acceptance_single_file_field_is_the_summary_contract():
    schema = [{'key': 'fmu4u8pua', 'label': '任务总结', 'type': 'file', 'required': True}]
    assert archive.summary_field_content(schema, {'fmu4u8pua': '# Draft'}) == '# Draft'
    assert archive.summary_field_content(schema, {'fmu4u8pua': [{'id': 'adopted'}]}) is None
    assert archive.summary_field_content([], {}) is None
    with pytest.raises(ValueError, match='unambiguous'):
        archive.summary_field_content(schema + [{'key': 'another', 'type': 'upload'}], {})


@pytest.mark.asyncio
async def test_frozen_canvas_and_references_never_use_live_template_or_initial_values(monkeypatch):
    task = task_item('accept', 'accept', status='assigned')
    task.wi_id = 'wi'
    source_task = task_item('dev', 'dev')
    source_task.form_values = {'final': [{'id': 'result'}], 'text': [{'id': 'secret'}]}
    initial = task_item('start', 'start')
    initial.form_values = {'prototype': [{'id': 'prototype'}]}
    canvas = N(nodes=[{'id': 'start', 'type': 'start', 'cfg': {'schema': [{'key': 'prototype', 'type': 'file'}]}},
                      {'id': 'dev', 'type': 'task', 'cfg': {'schema': [{'key': 'final', 'type': 'upload'}]}},
                      {'id': 'accept', 'type': 'acceptance'}], edges=[['start', 'dev'], ['dev', 'accept']])
    instance = N(template_id='template', version='v17')
    async def execute(stmt):
        if 'workflow_instances' in str(stmt):
            return N(scalar_one_or_none=lambda: instance)
        return N(scalars=lambda: N(all=lambda: [task, source_task, initial]))
    async def get(model, key):
        assert key == 'template:v17'
        return canvas
    session = N(execute=execute, get=get)
    assert await archive.adopted_upstream_references(session, task) == {
        'result': {'taskId': 'dev', 'nodeId': 'dev', 'field': 'final'}}
    instance.version = ''
    with pytest.raises(ValueError, match='frozen workflow'):
        await archive.frozen_canvas(session, task)


@pytest.mark.asyncio
async def test_archive_retry_verifies_physical_bytes_not_only_row(setup):
    session, source, task, user, store = setup
    await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])
    doc = next(row for row in session.rows if getattr(row, 'kind', '') == '验收归档')
    store.objects[doc.object_name] = b'corrupt'
    with pytest.raises(ValueError, match='checksum'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'])


@pytest.mark.asyncio
async def test_bytes_changed_after_scheduling_cannot_replace_frozen_archive(setup):
    session, source, task, user, store = setup
    from flowhub_api.services.tool_automation import verify_frozen_sources
    frozen = await archive.snapshot_sources(session, task)
    await verify_frozen_sources(session, task, frozen)
    store.objects[source.object_name] = b'changed between verification and copy'
    with pytest.raises(ValueError, match='bytes changed'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'], frozen_sources=frozen)
    assert not session.rows
    with pytest.raises(ValueError, match='source set'):
        await archive.archive_documents(session, task=task, user=user, source_document_ids=['source'], frozen_sources=[])


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [
    'Only output the JSON object.',
    'acceptance-summary.md',
    '# Summary\n\nDone.\n\n## Verification\n\nPassed.',
    'The delivery has been reviewed and awaits human acceptance. ' * 8,
    '# Summary\n\nOnly output the JSON object.\n\n## Verification\n\n' + 'Only output the JSON object. ' * 15,
])
async def test_schema_passed_summary_still_rejects_instruction_echo_or_incomplete_markdown(setup, monkeypatch, content):
    from flowhub_api.services.runtime_quality import content_hash
    session, source, task, user, store = setup
    monkeypatch.setattr(archive, 'frozen_canvas', AsyncMock(return_value=N(nodes=[{
        'id': 'acceptance', 'cfg': {'schema': [{'key': 'summaryContent', 'type': 'file', 'required': True}]}}])))
    run = N(task_id=task.id, status='succeeded', output=content,
            parsed={'values': {'summaryContent': content}},
            quality_result={'status': 'passed', 'contentHash': content_hash(content)})
    with pytest.raises(ValueError, match='Summary format'):
        await archive.validated_summary(session, task, run, content)
