"""Real PostgreSQL archive/outbox regressions; disposable test DB fixture only."""
import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from flowhub_api.core.config import get_settings
from flowhub_api.models import (
    DocItem, Expert, ExpertRun, ExpertVersion, GlobalTemplate, TaskItem,
    TemplateCanvas, TemplateVersion, User, WorkItem, WorkflowInstance,
)
from flowhub_api.models.tool import ToolAutomationJob, ToolInvocation
from flowhub_api.services import document_archive as archive

SUMMARY_MARKDOWN = '# Delivery summary\n\nThe delivery implements independent acceptance archives for the adopted predecessor files. Each archived document keeps its original bytes, sensitivity and source audit information. The generated summary is stored separately and remains a draft for the assigned human reviewer.\n\n## Verification and remaining review\n\nRegression checks cover authorization, deterministic archive keys, failed storage writes, duplicate backfill requests and approval resumption. The workflow task stays assigned with an unchanged form. A human must inspect the linked evidence and decide whether the delivery satisfies the acceptance criteria.'
from flowhub_api.services import tool_automation as automation
from flowhub_api.services.runtime_quality import content_hash


class ObjectStore:
    def __init__(self):
        self.objects = {}
        self.fail_writes = False
    def get_object(self, bucket, key):
        response = BytesIO(self.objects[key])
        response.release_conn = lambda: None
        return response
    def put_object(self, bucket, key, stream, length):
        if self.fail_writes:
            raise OSError('store unavailable')
        self.objects[key] = stream.read()


async def seed(factory, store):
    prefix = uuid4().hex[:10]
    ids = {key: f'{key}-{prefix}' for key in ['wi', 'tpl', 'expert', 'version', 'task', 'done', 'doc', 'run', 'prototype']}
    async with factory() as session:
        user = (await session.execute(select(User).where(User.account == 'liting'))).scalar_one()
        session.add(GlobalTemplate(id=ids['tpl'], name='Archive regression', type='requirement', nodes=[]))
        session.add(Expert(id=ids['expert'], name='Archive test', slug=ids['expert'], kind='custom', status='published', owner_id=user.id))
        session.add(WorkItem(id=ids['wi'], type='requirement', title='Archive test', project='Archive test', status='in_progress', creator=user.name))
        await session.flush()
        session.add(TemplateVersion(id=f"{ids['tpl']}:v1", template_id=ids['tpl'], version='v1', status='published'))
        session.add(ExpertVersion(id=ids['version'], expert_id=ids['expert'], version='v1', status='published', created_by=user.id,
            tool_bindings=[{'tool_version_id': 'tv-native-document-archive-1', 'scope': 'current_work_item', 'approval': 'inherit'}]))
        session.add(WorkflowInstance(id='inst-' + prefix, work_item_id=ids['wi'], template_id=ids['tpl'], version='v1'))
        await session.flush()
        rules = [{'id': 'entry', 'event': 'node_entered', 'toolVersionId': 'tv-native-document-archive-1', 'enabled': True},
                 {'id': 'summary', 'event': 'expert_output_validated', 'toolVersionId': 'tv-native-document-archive-1', 'enabled': True}]
        session.add(TemplateCanvas(version_id=f"{ids['tpl']}:v1", nodes=[
            {'id': 'start', 'type': 'start', 'cfg': {'schema': [{'key': 'prototype', 'type': 'file'}]}},
            {'id': 'done', 'type': 'task', 'cfg': {'schema': [{'key': 'delivery', 'type': 'upload'}]}},
            {'id': 'accept', 'type': 'end', 'cfg': {'schema': [{'key': 'fmu4u8pua', 'type': 'file', 'required': True}], 'automation': {'rules': rules}}}],
            edges=[['start', 'done'], ['done', 'accept']]))
        for task_id, node_id, status, values in [
            ('start-' + prefix, 'start', 'completed', {'prototype': [{'id': ids['prototype']}]}),
            (ids['done'], 'done', 'completed', {'delivery': [{'id': ids['doc']}]}),
            (ids['task'], 'accept', 'assigned', {})]:
            session.add(TaskItem(id=task_id, wi_id=ids['wi'], title='Archive test', project='Archive test', node=node_id,
                node_id=node_id, type='requirement', status=status, assignee=user.name, form_values=values))
        for key in ['doc', 'prototype']:
            session.add(DocItem(id=ids[key], name=f'{key}.md', project='Archive test', level='L3', scan='已扫描',
                uploader=user.name, wi=ids['wi'], object_name=ids[key], kind='节点表单附件'))
            store.objects[ids[key]] = (key + ' original bytes').encode()
        await session.flush()
        session.add(ExpertRun(id=ids['run'], expert_id=ids['expert'], expert_version_id=ids['version'], task_id=ids['task'],
            requested_by=user.id, status='succeeded', trace_id='trace-' + prefix, output='validated-output',
            parsed={'values': {'fmu4u8pua': SUMMARY_MARKDOWN}},
            quality_result={'status': 'passed', 'contentHash': content_hash('validated-output')}))
        await session.commit()
    return ids, user.id


def test_archive_concurrent_requests_copy_once_and_survive_source_deletion(client, monkeypatch):
    store = ObjectStore()
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    async def scenario():
        engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ids, user_id = await seed(factory, store)
        second_task_id = 'same-node-' + uuid4().hex[:10]
        async with factory() as session:
            user = await session.get(User, user_id)
            session.add(TaskItem(id=second_task_id, wi_id=ids['wi'], title='Concurrent archive', project='Archive test',
                node='accept', node_id='accept', type='requirement', status='assigned', assignee=user.name))
            await session.commit()
        async def copy(task_id):
            async with factory() as session:
                task, user = await session.get(TaskItem, task_id), await session.get(User, user_id)
                result = await archive.archive_documents(session, task=task, user=user, source_document_ids=[ids['doc']])
                await session.commit()
                return result
        first, second = await asyncio.gather(copy(ids['task']), copy(second_task_id))
        assert first == second
        async with factory() as session:
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert len(docs) == 1 and docs[0].level == 'L3'
            archived = docs[0]
            assert store.objects[archived.object_name] == b'doc original bytes'
            assert archived.object_name != ids['doc']
            source = await session.get(DocItem, ids['doc'])
            await session.delete(source)
            await session.commit()
        store.objects.pop(ids['doc'])
        async with factory() as session:
            assert await session.get(DocItem, archived.id) is not None
            assert store.objects[archived.object_name] == b'doc original bytes'
            assert (await session.get(TaskItem, ids['task'])).status == 'assigned'
        await engine.dispose()
    asyncio.run(scenario())


def test_durable_automation_recovers_expired_claim_and_archives_summary_without_adoption(client, monkeypatch):
    from unittest.mock import AsyncMock
    store = ObjectStore()
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    real_process = automation.process_one_automation_job
    # The test drives the same worker deterministically; pause only its background polling.
    monkeypatch.setattr(automation, 'process_one_automation_job', AsyncMock(return_value=False))
    async def scenario():
        engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(automation, 'SessionFactory', factory)
        ids, user_id = await seed(factory, store)
        async with factory() as session:
            task, user, run = await session.get(TaskItem, ids['task']), await session.get(User, user_id), await session.get(ExpertRun, ids['run'])
            jobs = await automation.schedule_automation(session, task=task, actor=user, expert_version_id=ids['version'],
                                                        event='expert_output_validated', run=run)
            job_id = jobs[0].id
            assert jobs[0].payload_json['sources'][0]['id'] == ids['doc']
            assert ids['prototype'] not in str(jobs[0].payload_json)
            await session.commit()
        first = await automation._claim_job()
        assert first[0] == job_id
        async with factory() as session:
            job = await session.get(ToolAutomationJob, job_id)
            job.lease_until = datetime.now(UTC) - timedelta(seconds=5)
            await session.commit()
        assert await real_process()
        async with factory() as session:
            job = await session.get(ToolAutomationJob, job_id)
            assert job.status == 'succeeded', job.error
            assert job.attempts == 2 and job.claim_token != first[1]
            invocation = (await session.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == f'automation:{job.event_key}'))).scalar_one()
            assert invocation.status == 'succeeded'
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert len(docs) == 2
            summary = next(doc for doc in docs if doc.provenance.get('origin') == 'AI生成')
            assert summary.provenance['reviewStatus'] == 'pending_human_review'
            assert store.objects[summary.object_name] == SUMMARY_MARKDOWN.encode()
            task = await session.get(TaskItem, ids['task'])
            assert task.status == 'assigned' and task.form_values == {}
        await engine.dispose()
    asyncio.run(scenario())


def test_archive_batch_storage_failure_rolls_back_all_visible_metadata(client, monkeypatch):
    import pytest
    store = ObjectStore()
    original_put = store.put_object
    writes = []
    def fail_second(bucket, key, stream, length):
        writes.append(key)
        if len(writes) == 2:
            raise OSError('second write failed')
        original_put(bucket, key, stream, length)
    store.put_object = fail_second
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    async def scenario():
        engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ids, user_id = await seed(factory, store)
        async with factory() as session:
            task, user, run = await session.get(TaskItem, ids['task']), await session.get(User, user_id), await session.get(ExpertRun, ids['run'])
            with pytest.raises(ValueError, match='storage write failed'):
                await archive.archive_documents(session, task=task, user=user, source_document_ids=[ids['doc']],
                                                run=run, summary_content=SUMMARY_MARKDOWN)
            await session.commit()
        async with factory() as session:
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert docs == []
        # Retry uses deterministic objects and creates the entire batch once.
        store.put_object = original_put
        async with factory() as session:
            task, user, run = await session.get(TaskItem, ids['task']), await session.get(User, user_id), await session.get(ExpertRun, ids['run'])
            result = await archive.archive_documents(session, task=task, user=user, source_document_ids=[ids['doc']],
                                                    run=run, summary_content=SUMMARY_MARKDOWN)
            assert len(result['documents']) == 2
            await session.commit()
        await engine.dispose()
    asyncio.run(scenario())


def test_instruction_echo_is_a_durable_failed_summary_job_without_archive_success(client, monkeypatch):
    store = ObjectStore()
    monkeypatch.setattr(archive, 'get_minio', lambda: store)
    async def scenario():
        engine = create_async_engine(get_settings().sqlalchemy_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ids, user_id = await seed(factory, store)
        async with factory() as session:
            task, user, run = await session.get(TaskItem, ids['task']), await session.get(User, user_id), await session.get(ExpertRun, ids['run'])
            content = 'Only output the JSON object.'
            run.output = content
            run.parsed = {'values': {'fmu4u8pua': content}}
            run.quality_result = {'status': 'passed', 'contentHash': content_hash(content)}
            jobs = await automation.schedule_automation(session, task=task, actor=user,
                expert_version_id=ids['version'], event='expert_output_validated', run=run)
            job_id = jobs[0].id
            await session.commit()
        async with factory() as session:
            job = await session.get(ToolAutomationJob, job_id)
            assert job.status == 'failed' and 'Summary format' in job.error
            assert job.result_json == {} and 'freeze_error' in job.payload_json
            docs = (await session.execute(select(DocItem).where(DocItem.wi == ids['wi'], DocItem.kind == archive.ARCHIVE_KIND))).scalars().all()
            assert docs == []
            assert (await session.get(TaskItem, ids['task'])).status == 'assigned'
        await engine.dispose()
    asyncio.run(scenario())
