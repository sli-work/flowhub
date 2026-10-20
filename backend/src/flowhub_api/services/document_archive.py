"""Scoped immutable acceptance archives backed by independent object bytes."""
import asyncio
import re
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO

from sqlalchemy import select, text

from flowhub_api.clients.minio import get_minio
from flowhub_api.core.config import get_settings
from flowhub_api.models import DocItem, TaskItem, TemplateCanvas, WorkflowInstance, WorkItem
from flowhub_api.services.audit import AuditService

ARCHIVE_KIND = '验收归档'
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


async def authorize_archive(session, task, user):
    """Require a live task actor; an administrator role alone is insufficient."""
    from flowhub_api.services.agent_context import _is_task_participant

    if user is None or user.deleted or user.status != 'active':
        raise ValueError('Archive actor is not active')
    if task.status in {'completed', 'cancelled'}:
        raise ValueError('Archive task is no longer active')
    if not await _is_task_participant(session, user, task):
        raise ValueError('Archive requires the current task actor')
    from flowhub_api.authz.authorizer import build_authorizer
    build_authorizer(user).require('document:upload')
    wi = await session.get(WorkItem, task.wi_id)
    if wi is None or wi.status in {'closed', 'cancelled', 'archived'}:
        raise ValueError('Archive work item is no longer active')
    canvas = await frozen_canvas(session, task)
    node = next((node for node in canvas.nodes if node['id'] == task.node_id), {})
    if node.get('type') not in {'acceptance', 'end'}:
        raise ValueError('Archive is restricted to an acceptance end node')


def file_references(schema, values):
    """Only adopted typed FILE/UPLOAD references; never scan arbitrary JSON."""
    refs = {}
    for field in schema:
        if str(field.get('type', '')).lower() not in {'file', 'upload'}:
            continue
        key = field.get('key')
        value = (values or {}).get(key)
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get('id'), str) and entry['id']:
                refs[entry['id']] = key
    return refs


async def frozen_canvas(session, task):
    instance = (await session.execute(select(WorkflowInstance).where(
        WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
    if instance is None or not instance.version:
        raise ValueError('Archive requires a frozen workflow version')
    canvas = await session.get(TemplateCanvas, f'{instance.template_id}:{instance.version}')
    if canvas is None:
        raise ValueError('Frozen workflow canvas is missing')
    return canvas


def _completed_predecessors(task, tasks, canvas):
    """Walk actual completed paths, isolating split lineages and excluding start."""
    by_id = {item.id: item for item in tasks}
    lineage = task.lineage_root_id
    ancestors = set()
    cursor = by_id.get(lineage) if lineage else task
    while cursor is not None and cursor.parent_task_id and cursor.parent_task_id not in ancestors:
        ancestors.add(cursor.parent_task_id)
        cursor = by_id.get(cursor.parent_task_id)
    candidates = [item for item in tasks if item.status == 'completed'
                  and not (item.source or '').startswith(('issue:', 'correction:'))
                  and (item.lineage_root_id == lineage or item.id in ancestors)]
    latest = {}
    for item in sorted(candidates, key=lambda item: (item.created_at or '', item.id)):
        latest[item.node_id] = item
    nodes = {node['id']: node for node in canvas.nodes}
    reverse = {}
    for source, target in canvas.edges:
        reverse.setdefault(target, []).append(source)
    selected, visited = [], {task.node_id}
    pending = list(reverse.get(task.node_id, []))
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes.get(node_id, {})
        if node.get('type') == 'start':
            continue
        item = latest.get(node_id)
        if item is not None:
            selected.append(item)
        elif node.get('type') not in {'parallel_join', 'parallel_split', 'timer'}:
            # An uncompleted conditional branch must not expose its ancestors.
            continue
        pending.extend(reverse.get(node_id, []))
    return selected


async def adopted_upstream_references(session, task):
    canvas = await frozen_canvas(session, task)
    tasks = (await session.execute(select(TaskItem).where(TaskItem.wi_id == task.wi_id))).scalars().all()
    nodes = {node['id']: node for node in canvas.nodes}
    refs = {}
    for upstream in _completed_predecessors(task, tasks, canvas):
        schema = (nodes.get(upstream.node_id, {}).get('cfg') or {}).get('schema') or []
        for doc_id, field in file_references(schema, upstream.form_values).items():
            refs[doc_id] = {'taskId': upstream.id, 'nodeId': upstream.node_id, 'field': field}
    return refs


def read_document_bytes(store, doc):
    response = None
    try:
        response = store.get_object(get_settings().minio_bucket, doc.object_name)
        data = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ValueError('Archive source exceeds 50MB')
        return data
    except Exception as exc:
        raise ValueError('Archive source storage read failed') from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()


async def checked_source(session, doc_id, task):
    doc = await session.get(DocItem, doc_id)
    if (doc is None or doc.deleted or doc.wi != task.wi_id or doc.project != task.project
            or doc.scan != '已扫描' or not doc.object_name or doc.kind == ARCHIVE_KIND):
        raise ValueError('Archive source is missing, unsafe or outside the current work item')
    return doc


async def snapshot_sources(session, task):
    refs = await adopted_upstream_references(session, task)
    if not refs:
        return []
    store = await asyncio.to_thread(get_minio)
    if store is None:
        raise ValueError('Archive object storage is unavailable')
    snapshots = []
    for doc_id in sorted(refs):
        doc = await checked_source(session, doc_id, task)
        digest = sha256(await asyncio.to_thread(read_document_bytes, store, doc)).hexdigest()
        snapshots.append({'id': doc.id, 'sha256': digest, **refs[doc.id]})
    return snapshots


def summary_field_content(schema, values):
    """Resolve the frozen summary field, including existing single-file contracts."""
    declared = [field for field in schema if field.get('key') == 'summaryContent']
    if not declared:
        declared = [field for field in schema if str(field.get('type', '')).lower() in {'file', 'upload'}]
    if not declared:
        return None
    if len(declared) != 1:
        raise ValueError('Summary archive requires one unambiguous output field')
    value = values.get(declared[0].get('key'))
    return value if isinstance(value, str) and value.strip() else None


async def summary_for_run(session, task, run):
    if run is None:
        return None
    canvas = await frozen_canvas(session, task)
    node = next((node for node in canvas.nodes if node['id'] == task.node_id), {})
    return summary_field_content((node.get('cfg') or {}).get('schema') or [],
                                 (run.parsed or {}).get('values') or {})


def validate_summary_markdown(content):
    """Check document completeness only; this never certifies facts or acceptance."""
    if not isinstance(content, str) or not content.strip() or len(content.encode()) > MAX_ARCHIVE_BYTES:
        raise ValueError('Summary format: content is empty or too large')
    lines = content.strip().splitlines()
    instruction = re.compile(r'^(?:only\s+(?:output|return)|(?:output|return)\s+only|(?:请)?(?:仅|只)(?:返回|输出))', re.I)
    if any(instruction.match(line.lstrip(' >*-+0123456789.')) for line in lines):
        raise ValueError('Summary format: instruction echo is not a delivery document')
    headings = {match.group(1).strip() for line in lines
                if (match := re.match(r'^#{1,6}\s+(.+?)\s*#*$', line))}
    if len(headings) < 2:
        raise ValueError('Summary format: at least two distinct Markdown headings are required')
    body = '\n'.join(line for line in lines if not re.match(r'^#{1,6}\s+', line) and not line.lstrip().startswith('```'))
    if len(re.sub(r'\s', '', body)) < 200:
        raise ValueError('Summary format: at least 200 non-whitespace body characters are required')


async def validated_summary(session, task, run, content):
    """Schema quality is formatting evidence only; the summary remains unreviewed."""
    from flowhub_api.services.expert_runtime import schema_validation_issues
    from flowhub_api.services.runtime_quality import content_hash

    if run is None or run.task_id != task.id or run.status != 'succeeded':
        raise ValueError('Summary requires a successful task run')
    values = (run.parsed or {}).get('values') or {}
    quality = run.quality_result or {}
    if (quality.get('status') != 'passed'
            or quality.get('contentHash') != content_hash(run.output)):
        raise ValueError('Summary does not match validated run output')
    canvas = await frozen_canvas(session, task)
    node = next((node for node in canvas.nodes if node['id'] == task.node_id), {})
    schema = (node.get('cfg') or {}).get('schema') or []
    if summary_field_content(schema, values) != content:
        raise ValueError('Summary does not match validated run output')
    if schema_validation_issues(schema, values):
        raise ValueError('Summary output schema validation failed')
    validate_summary_markdown(content)


def _ref(doc):
    return {'id': doc.id, 'name': doc.name, 'archiveKey': doc.archive_key,
            'downloadUrl': f'/api/v1/documents/{doc.id}/download'}


async def _persist_archive(session, *, task, user, store, data, source_id, name, level, provenance):
    digest = sha256(data).hexdigest()
    key = sha256(f'{task.wi_id}:{task.node_id}:{source_id}:{digest}'.encode()).hexdigest()
    doc_id = 'da' + key[:30]
    # Serialize different jobs targeting the same deterministic archive object.
    lock_id = int.from_bytes(bytes.fromhex(key[:16]), 'big', signed=True)
    await session.execute(text('SELECT pg_advisory_xact_lock(:archive_lock)'), {'archive_lock': lock_id})
    existing = await session.get(DocItem, doc_id, populate_existing=True)
    if existing is not None:
        if existing.deleted or existing.archive_key != key:
            raise ValueError('Archive record is unavailable')
        # Never report metadata-only success even on a retry.
        if sha256(await asyncio.to_thread(read_document_bytes, store, existing)).hexdigest() != digest:
            raise ValueError('Stored archive checksum mismatch')
        return _ref(existing)
    stem, dot, ext = name.rpartition('.')
    filename = f'{stem or name}-验收归档-{key[:10]}.{ext if dot else "md"}'[-250:]
    object_name = f'acceptance-archive/{task.wi_id}/{key}'
    try:
        await asyncio.to_thread(store.put_object, get_settings().minio_bucket, object_name, BytesIO(data), len(data))
    except Exception as exc:
        raise ValueError('Archive object storage write failed') from exc
    doc = DocItem(id=doc_id, name=filename, project=task.project, version='v1', level=level,
                  scan='已扫描', uploader=user.name, size=f'{len(data)}B', time=datetime.now(UTC).isoformat(),
                  kind=ARCHIVE_KIND, wi=task.wi_id, object_name=object_name, deleted=False,
                  archive_key=key, provenance={**provenance, 'sha256': digest, 'taskId': task.id,
                    'nodeId': task.node_id, 'actorId': user.id, 'reviewStatus': 'pending_human_review'})
    session.add(doc)
    await AuditService(session).record(actor=user.name, action='document:archive', target=doc.id,
                                       after={'archiveKey': key, 'provenance': doc.provenance})
    await session.flush()
    return _ref(doc)


async def _archive_documents(session, *, task, user, source_document_ids, run=None, summary_content=None, frozen_sources=None):
    await authorize_archive(session, task, user)
    if not isinstance(source_document_ids, list) or any(not isinstance(item, str) for item in source_document_ids):
        raise ValueError('source_document_ids must be document IDs')
    refs = await adopted_upstream_references(session, task)
    if not set(source_document_ids).issubset(refs):
        raise ValueError('Archive sources must be adopted completed upstream FILE/UPLOAD references')
    if summary_content is not None:
        await validated_summary(session, task, run, summary_content)
    store = await asyncio.to_thread(get_minio)
    if store is None:
        raise ValueError('Archive object storage is unavailable')
    expected = {source['id']: source['sha256'] for source in frozen_sources} if frozen_sources is not None else None
    if expected is not None and (len(expected) != len(frozen_sources) or set(expected) != set(source_document_ids)):
        raise ValueError('Frozen archive source set does not match requested documents')
    prepared = []
    for doc_id in sorted(set(source_document_ids)):
        doc = await checked_source(session, doc_id, task)
        data = await asyncio.to_thread(read_document_bytes, store, doc)
        if expected is not None and sha256(data).hexdigest() != expected[doc_id]:
            raise ValueError('Frozen archive source bytes changed before copying')
        prepared.append((doc, data))
    results = []
    for doc, data in prepared:
        results.append(await _persist_archive(session, task=task, user=user, store=store, data=data,
            source_id=doc.id, name=doc.name, level=doc.level,
            provenance={'sourceDocumentId': doc.id, 'sourceObjectName': doc.object_name,
                        'sourceVersion': doc.version, 'upstream': refs[doc.id], 'origin': 'adopted_upstream'}))
    if summary_content is not None:
        source_levels = [(await checked_source(session, doc_id, task)).level for doc_id in refs]
        results.append(await _persist_archive(session, task=task, user=user, store=store,
            data=summary_content.encode('utf-8'), source_id=f'summary:{task.id}',
            name='AI生成-待人工验收-验收总结.md', level=max(source_levels or ['L2']),
            provenance={'origin': 'AI生成', 'sourceRunId': run.id, 'generation': run.execution_generation,
                        'schemaQuality': 'passed'}))
    return {'documents': results, 'reviewStatus': 'pending_human_review'}


async def archive_documents(session, *, task, user, source_document_ids, run=None, summary_content=None, frozen_sources=None):
    """Rollback all metadata on partial failure; deterministic objects permit retry."""
    async with session.begin_nested():
        current_task = await session.get(TaskItem, task.id, with_for_update=True, populate_existing=True)
        if current_task is None:
            raise ValueError('Archive task no longer exists')
        return await _archive_documents(session, task=current_task, user=user,
            source_document_ids=source_document_ids, run=run, summary_content=summary_content, frozen_sources=frozen_sources)
