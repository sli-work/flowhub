#!/usr/bin/env python3
"""Reviewed Tool Center configuration cutover. No tools, jobs, runs or tasks execute.

Run inside the deployed backend container after schema migration/builtin seeding:
  python /tmp/cutover_acceptance_tools.py --dry-run --actor-id ADMIN_ID > backup.json
  python /tmp/cutover_acceptance_tools.py --apply --backup backup.json \
    --reviewed-sha256 CONTENT_SHA256
  python /tmp/cutover_acceptance_tools.py --verify --backup backup.json

Dry-run is a read-only SERIALIZABLE transaction and prints JSON only. Preserve the
backup with mode 0600; review its protected state, new Expert prompt and exact plan.
Apply requires that exact reviewed checksum and refuses changed task data, cursors,
active runs, partial migrations, changed templates, bindings or tool contracts.
Do not run task backfill until verification: that is a separate authorized endpoint.
After subsequent task activity strict verification intentionally refuses drift.
Rollback is a separately reviewed operation: restore protected instance/binding/
Expert pointers from backup AND archive v22 (latest-published selection uses rows,
not just GlobalTemplate.versions). This script deliberately has no force/rollback.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

OP = 'acceptance-tools-20261020'
TEMPLATE = 'tpl-req'
NODE = 'nmtidmrmc3cb'
OLD_DEPLOYMENT = 'depb165eb5f8bca'
NEW_DEPLOYMENT = 'dep-accept-tools-20261020'
NEW_EXPERT_VERSION = 'ev-accept-tools-20261020'
EXPERT_LABEL = 'v0.2-tools'
TOOL_VERSION = 'tv-native-document-archive-1'
VERSION_MAP = {'v16-acceptance': 'v16-tools', 'v18-acceptance': 'v18-tools',
               'v20-acceptance': 'v20-tools'}
PROJECTS = {'p8d8a0de8': 'DRCC', 'pca0e735f': 'OSM'}
IDS = tuple(sorted('''REQ-20261008081209-FB43
REQ-20260929025143-491A
REQ-20260921062203-B907
REQ-20260920100444-E7DF
REQ-20260920095216-E758
REQ-20260920094954-3305
REQ-20260917013841-ECC5
REQ-20260911094418-EA54
REQ-20260909074504-1A11
REQ-20260909074012-1B44
REQ-20260908071722-31C4'''.split()))
PROMPT_ADDENDUM = '''

【Tool Center 验收归档授权与边界】
原始验收职责、输出契约及人工审批边界继续有效。你获准使用固定版本的原生平台归档工具 flowhub_document_archive，范围仅限当前工作项、当前节点已授权的实际前序分支已采纳最终 FILE/UPLOAD 交付文档。不得归档原始需求附件、原型、整个工作项全部文档，或模型自行选定的跨工作项文档；不得修改或删除原始文档、前序任务历史。
节点进入时由 node_entered 确定性自动化保存前序最终交付物；结构与格式校验通过后由 expert_output_validated 确定性自动化归档生成的任务总结。平台保存与复制原始字节，保留来源；不要把自动化描述成已完成的人工验收，也不要以提示词代替真实工具执行。
总结必须基于已有证据，缺失事实明确标注待核实，不得捏造已测试、已发布或已验收结论。生成总结标记为“AI生成 / 待人工验收”；格式质量通过不代表事实正确或人工验收通过。仅在工具执行结果明确成功时说明已归档，失败如实说明。不得自动提交任务、关闭流程或代替人工验收。
'''


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def serial(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=serial)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalized(value):
    return json.loads(canonical(value))


def tool_prompt(original):
    require(bool(original.strip()), 'Original acceptance prompt is empty')
    return original.rstrip() + PROMPT_ADDENDUM


def archive_grants(original):
    grants = copy.deepcopy(original or [])
    existing = [g for g in grants if g.get('tool_version_id') == TOOL_VERSION]
    grant = {'tool_version_id': TOOL_VERSION, 'scope': 'current_work_item', 'approval': 'inherit'}
    require(not existing or existing == [grant], 'Existing archive grant has incompatible policy')
    return grants if existing else [*grants, grant]


def patch_canvas(original, version):
    require(len(version) <= 16, 'Version exceeds DB limit')
    result = copy.deepcopy(original)
    result['version_id'] = TEMPLATE + ':' + version
    matches = [n for n in result['nodes'] if n.get('id') == NODE]
    require(len(matches) == 1, 'Acceptance node missing or duplicated')
    node = matches[0]
    require(node.get('label') == '验收' and node.get('type') == 'end', 'Acceptance node identity changed')
    cfg = node.get('cfg') or {}
    require(bool(cfg.get('schema')), 'Acceptance node must retain a nonempty file schema')
    require((cfg.get('expert') or {}).get('expertDeploymentId') == OLD_DEPLOYMENT,
            'Unexpected original acceptance deployment')
    require(not cfg.get('automation'), 'Original acceptance already has automation; review separately')
    cfg['expert'] = {**cfg.get('expert', {}), 'expertDeploymentId': NEW_DEPLOYMENT}
    cfg['automation'] = {'rules': [
        {'id': 'acceptance-archive-inputs', 'event': 'node_entered',
         'toolVersionId': TOOL_VERSION, 'enabled': True},
        {'id': 'acceptance-archive-summary', 'event': 'expert_output_validated',
         'toolVersionId': TOOL_VERSION, 'enabled': True}]}
    node['cfg'] = cfg
    require(bool(result['edges']), 'Explicit engine edges required')
    return result


def migration_state(current, original):
    by_id = {row['id']: row for row in original}
    require(len(current) == len(original) and {r['id'] for r in current} == set(by_id),
            'Scoped instance set changed')
    states = set()
    for row in current:
        before = by_id[row['id']]
        require(row['work_item_id'] == before['work_item_id'], 'Instance ownership changed')
        old = before['version']
        require(old in VERSION_MAP, 'Backup source instance version unsupported')
        require(row['version'] in (old, VERSION_MAP[old]), 'Unexpected instance version')
        states.add('original' if row['version'] == old else 'applied')
    require(len(states) == 1, 'Mixed/partial instance cutover; abort')
    return states.pop()


def check_backup(backup):
    require(backup.get('format') == 1 and backup.get('operation') == OP, 'Wrong backup format/operation')
    require(backup.get('scope') == list(IDS), 'Wrong backup scope')
    require(backup.get('content_sha256') == digest({k: v for k, v in backup.items()
                                                  if k != 'content_sha256'}), 'Backup checksum mismatch')


async def fetch(session, table, where=None, lock=False):
    from sqlalchemy import select
    statement = select(table)
    if where is not None:
        statement = statement.where(where)
    statement = statement.order_by(*table.primary_key.columns)
    if lock:
        statement = statement.with_for_update()
    return normalized([dict(row) for row in (await session.execute(statement)).mappings()])


async def one(session, table, where, lock=False):
    rows = await fetch(session, table, where, lock)
    require(len(rows) == 1, 'Expected exactly one row in ' + table.name)
    return rows[0]


async def snapshot(session, t, lock=False):
    from sqlalchemy import or_
    wi, inst, task = t['work_items'], t['workflow_instances'], t['tasks']
    result = {'work_items': await fetch(session, wi, wi.c.id.in_(IDS), lock),
              'instances': await fetch(session, inst, inst.c.work_item_id.in_(IDS), lock),
              'tasks': await fetch(session, task, task.c.wi_id.in_(IDS), lock)}
    result['other_instances'] = await fetch(session, inst,
        (inst.c.template_id == TEMPLATE) & ~inst.c.work_item_id.in_(IDS))
    task_ids = [row['id'] for row in result['tasks']]
    result['global_template'] = await one(session, t['global_templates'],
                                         t['global_templates'].c.id == TEMPLATE, lock)
    result['bindings'] = await fetch(session, t['project_template_bindings'], lock=lock)
    assignments = t['node_assignments']
    result['assignments'] = await fetch(session, assignments, lock=lock)
    project = t['projects']
    result['projects'] = await fetch(session, project, project.c.id.in_(PROJECTS), lock)
    destination_ids = [TEMPLATE + ':' + v for v in [*VERSION_MAP.values(), 'v22']]
    tv, canvas = t['template_versions'], t['template_canvases']
    result['versions'] = await fetch(session, tv, (tv.c.template_id == TEMPLATE)
                                     & ~tv.c.id.in_(destination_ids), lock)
    result['canvases'] = await fetch(session, canvas,
        canvas.c.version_id.in_([row['id'] for row in result['versions']]), lock)
    dep, expert, ev = t['expert_deployments'], t['experts'], t['expert_versions']
    result['old_deployment'] = await one(session, dep, dep.c.id == OLD_DEPLOYMENT, lock)
    old = result['old_deployment']
    result['expert'] = await one(session, expert, expert.c.id == old['expert_id'], lock)
    result['old_expert_version'] = await one(session, ev, ev.c.id == old['expert_version_id'], lock)
    tools, versions = t['tool_definitions'], t['tool_versions']
    result['archive_tool_version'] = await one(session, versions, versions.c.id == TOOL_VERSION, lock)
    result['archive_tool'] = await one(session, tools,
        tools.c.id == result['archive_tool_version']['tool_id'], lock)
    for name in ('task_appends', 'task_corrections', 'workflow_issues', 'notifications'):
        result[name] = await fetch(session, t[name], t[name].c.wi_id.in_(IDS), lock)
    # Preserve direct documents and document IDs referenced by scoped structured fields.
    references = set()
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                references.add(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            references.add(value)
    walk([result['work_items'], result['tasks'], result['task_appends'], result['task_corrections']])
    doc = t['documents']
    result['documents'] = await fetch(session, doc,
        or_(doc.c.wi.in_(IDS), doc.c.id.in_(sorted(references))), lock)
    runs = await fetch(session, t['expert_runs'], t['expert_runs'].c.task_id.in_(task_ids), lock)
    run_ids = [r['id'] for r in runs]
    result['runs'] = [{'id': r['id'], 'task_id': r['task_id'], 'status': r['status'],
                       'sha256': digest(r)} for r in runs]
    scopes = {'expert_run_events': ('run_id', run_ids), 'expert_approvals': ('run_id', run_ids),
              'expert_jobs': ('run_id', run_ids), 'tool_invocations': ('task_id', task_ids),
              'tool_automation_jobs': ('task_id', task_ids)}
    for name, (key, identifiers) in scopes.items():
        rows = await fetch(session, t[name], t[name].c[key].in_(identifiers), lock)
        result[name] = [{'id': r['id'], 'status': r.get('status'), 'sha256': digest(r)} for r in rows]
    return result


def validate_original(snapshot):
    require(sorted(row['id'] for row in snapshot['work_items']) == list(IDS), 'Exact 11 workitems required')
    require(all(row['type'] == 'requirement' and row['status'] == 'in_progress'
                for row in snapshot['work_items']), 'Workitems must be in-progress requirements')
    instances = snapshot['instances']
    require(len(instances) == 11 and sorted(r['work_item_id'] for r in instances) == list(IDS),
            'Exactly one instance per scoped workitem required')
    require(all(r['template_id'] == TEMPLATE and r['state'] == 'running' for r in instances),
            'All scoped instances must be running tpl-req')
    require(Counter(r['version'] for r in instances) ==
            {'v16-acceptance': 3, 'v18-acceptance': 1, 'v20-acceptance': 7}, 'Version distribution changed')
    require({r['id']: r['code'] for r in snapshot['projects']} == PROJECTS, 'Project identities changed')
    require(snapshot['old_deployment']['status'] == 'active', 'Old deployment inactive')
    require(snapshot['expert']['status'] == snapshot['old_expert_version']['status'] == 'published',
            'Old Expert/version must be published')
    require(snapshot['old_expert_version']['expert_id'] == snapshot['expert']['id'], 'Expert mismatch')
    require(snapshot['old_expert_version']['tested_at'], 'Source ExpertVersion has no test lineage')
    require(not snapshot['old_expert_version']['tool_policies'], 'Legacy tool policies are unsupported')
    versions = {r['version']: r for r in snapshot['versions']}
    require('v21' in versions and versions['v21']['status'] == 'published', 'Published v21 required')
    published_numeric = [int(v[1:]) for v, row in versions.items()
                         if v.startswith('v') and v[1:].isdigit() and row['status'] == 'published']
    require(max(published_numeric) == 21, 'Latest published numeric template is not v21')
    require('v22' not in snapshot['global_template']['versions'], 'v22 already listed')
    targets = [r for r in snapshot['bindings'] if r['project_id'] in PROJECTS
               and r['template_id'] == TEMPLATE and r['type'] == 'requirement']
    require(len(targets) == 2 and {r['project_id'] for r in targets} == set(PROJECTS),
            'Exactly DRCC/OSM requirement bindings required')
    require(all(r['status'] == 'active' and r['version'] == 'v21' for r in targets),
            'DRCC/OSM requirement bindings must be active v21')
    for source in VERSION_MAP:
        require(source in versions, 'Original compatible snapshot missing')


def assert_idle(snapshot):
    require(not any(task['expert_pending'] for task in snapshot['tasks']), 'Scoped task expert_pending is active')
    require(not any(r['status'] in {'queued', 'running', 'interrupted'} for r in snapshot['runs']),
            'Scoped Expert run still active; wait for completion before cutover')
    for name in ('expert_jobs', 'tool_invocations', 'tool_automation_jobs'):
        require(not any(r['status'] in {'queued', 'running', 'awaiting_approval'} for r in snapshot[name]),
                'Scoped execution ledger still active: ' + name)


def build_plan(snapshot, actor, prompt=None):
    validate_original(snapshot)
    timestamp = datetime.now(timezone.utc).isoformat()
    old = snapshot['old_expert_version']
    new_version = {**copy.deepcopy(old), 'id': NEW_EXPERT_VERSION, 'version': EXPERT_LABEL,
                   'tool_bindings': archive_grants(old.get('tool_bindings')),
                   'system_prompt': prompt if prompt is not None else tool_prompt(old['system_prompt']),
                   'status': 'published', 'created_by': actor['id'], 'created_at': timestamp,
                   'published_by': actor['id'], 'published_at': timestamp}
    # No model execution is faked: inherited test timestamp is explicitly disclosed.
    new_version['validation'] = {**(old.get('validation') or {}), 'toolCutover': {
        'operation': OP, 'sourceVersionId': old['id'], 'validation': 'static configuration validation',
        'executionTest': 'inherited from unchanged source contract; no new model run'}}
    checksum_fields = ('system_prompt', 'provider_model_id', 'skills', 'knowledge_base_ids',
                       'tool_bindings', 'output_contract_json', 'execution_profile_json')
    new_version['checksum'] = digest({k: new_version.get(k) for k in checksum_fields})
    deployment = {**copy.deepcopy(snapshot['old_deployment']), 'id': NEW_DEPLOYMENT,
                  'expert_version_id': NEW_EXPERT_VERSION,
                  'name': snapshot['old_deployment']['name'] + '-tools-20261020',
                  'alias': 'acceptance-tools-20261020', 'created_by': actor['id'], 'created_at': timestamp}
    require(len(deployment['name']) <= 128, 'New deployment name exceeds limit')
    versions = {r['version']: r for r in snapshot['versions']}
    canvases = {r['version_id']: r for r in snapshot['canvases']}
    new_versions, new_canvases = [], []
    for source, target in [*VERSION_MAP.items(), ('v21', 'v22')]:
        canvas = patch_canvas(canvases[TEMPLATE + ':' + source], target)
        row = {**copy.deepcopy(versions[source]), 'id': TEMPLATE + ':' + target,
               'version': target, 'status': 'published' if target == 'v22' else 'archived',
               'updated': timestamp[:19], 'updated_by': actor['name'], 'instances': 0}
        new_versions.append(row)
        new_canvases.append(canvas)
    targets = [r['id'] for r in snapshot['bindings'] if r['project_id'] in PROJECTS
               and r['template_id'] == TEMPLATE and r['type'] == 'requirement']
    return {'actor': actor, 'expert_version': new_version, 'deployment': deployment,
            'template_versions': new_versions, 'canvases': new_canvases,
            'binding_ids': targets, 'expert_updated_at': timestamp}


def expected_protected(original, plan):
    result = copy.deepcopy(original)
    for row in result['instances']:
        row['version'] = VERSION_MAP[row['version']]
    for row in result['bindings']:
        if row['id'] in plan['binding_ids']:
            row['version'] = 'v22'
    result['global_template']['versions'] = [*result['global_template']['versions'], 'v22']
    result['expert']['current_version_id'] = NEW_EXPERT_VERSION
    result['expert']['updated_at'] = plan['expert_updated_at']
    return result


def compare(current, expected):
    require(set(current) == set(expected), 'Protected snapshot keys changed')
    for key in expected:
        require(current[key] == expected[key], 'Protected state changed: ' + key)


async def validate_plan(session, plan, actor):
    from flowhub_api.models import ExpertVersion
    from flowhub_api.models.tool import ToolDefinition, ToolVersion
    from flowhub_api.services.expert_runtime import validate_version
    from flowhub_api.services.tool_registry import contract_checksum, validate_tool_bindings
    from flowhub_api.routes.templates import _validate_canvas
    from flowhub_api.authz.authorizer import build_authorizer
    require(actor is not None and actor.status == 'active' and not actor.deleted, 'Actor not active')
    auth = build_authorizer(actor)
    for permission in ('expert:update', 'expert:publish', 'expert:deploy',
                       'workflow_template:update', 'workflow_template:publish', 'project:update'):
        auth.require(permission)
    tool_version = await session.get(ToolVersion, TOOL_VERSION)
    tool = await session.get(ToolDefinition, tool_version.tool_id) if tool_version else None
    require(tool and tool.status == 'active' and tool.source == 'native', 'Native archive tool unavailable')
    require(tool_version.status == 'published' and tool_version.checksum == contract_checksum(tool_version),
            'Archive tool contract invalid')
    require(tool_version.config_json == {'adapter': 'flowhub_document_archive'}
            and tool_version.risk == 'write_draft' and tool_version.approval == 'none',
            'Archive tool differs from pre-authorized scoped contract')
    require(not await validate_tool_bindings(session, plan['expert_version']['tool_bindings']),
            'Expert tool grants invalid')
    transient = ExpertVersion(**plan['expert_version'])
    require(not await validate_version(session, transient), 'New ExpertVersion fails runtime static validation')
    for canvas in plan['canvases']:
        require(not _validate_canvas(canvas), 'New canvas fails static validation')


async def target_rows(session, t, plan, create=False):
    from sqlalchemy import insert
    targets = [('expert_versions', 'id', plan['expert_version']),
               ('expert_deployments', 'id', plan['deployment'])]
    targets += [('template_versions', 'id', row) for row in plan['template_versions']]
    targets += [('template_canvases', 'version_id', row) for row in plan['canvases']]
    created = 0
    for name, key, expected in targets:
        table = t[name]
        existing = await fetch(session, table, table.c[key] == expected[key])
        if not existing and create:
            await session.execute(insert(table).values(**expected))
            created += 1
        else:
            require(existing == [expected], 'Destination absent or incompatible: ' + name + '/' + expected[key])
    return created


async def engine_checks(session, plan):
    from flowhub_api.models import GlobalTemplate
    from flowhub_api.services.workflow import WorkflowService
    from flowhub_api.routes.templates import _validate_automation_bindings
    service = WorkflowService(session)
    template = await session.get(GlobalTemplate, TEMPLATE, populate_existing=True)
    for canvas in plan['canvases']:
        version = canvas['version_id'].split(':', 1)[1]
        require(await service.nodes_of(template, version) == canvas['nodes'], 'Engine nodes mismatch')
        require(await service._edges_of(template, version) == [tuple(e[:2]) for e in canvas['edges']],
                'Engine routes mismatch')
        cfg = next(n['cfg'] for n in canvas['nodes'] if n['id'] == NODE)
        require(await service._node_cfg_of(template, NODE, version) == cfg, 'Engine cfg mismatch')
        require(not await _validate_automation_bindings(session, canvas), 'Automation rules fail pinned grant validation')


async def apply_updates(session, t, backup):
    from sqlalchemy import update
    baseline, plan = backup['protected'], backup['plan']
    for row in baseline['instances']:
        table = t['workflow_instances']
        result = await session.execute(update(table).where(table.c.id == row['id'],
            table.c.version == row['version'], table.c.current_node == row['current_node'],
            table.c.state == row['state']).values(version=VERSION_MAP[row['version']]))
        require(result.rowcount == 1, 'Cursor or version changed during cutover')
    for row in baseline['bindings']:
        if row['id'] in plan['binding_ids']:
            table = t['project_template_bindings']
            result = await session.execute(update(table).where(table.c.id == row['id'],
                table.c.version == row['version']).values(version='v22'))
            require(result.rowcount == 1, 'Binding changed during cutover')
    table = t['global_templates']
    await session.execute(update(table).where(table.c.id == TEMPLATE).values(
        versions=[*baseline['global_template']['versions'], 'v22']))
    table = t['experts']
    await session.execute(update(table).where(table.c.id == baseline['expert']['id']).values(
        current_version_id=NEW_EXPERT_VERSION, updated_at=plan['expert_updated_at']))


async def audits(session, backup):
    from flowhub_api.services.audit import AuditService
    service = AuditService(session)
    before, plan = backup['protected'], backup['plan']
    after = expected_protected(before, plan)
    common = {'actor': plan['actor']['name'], 'actor_type': 'user', 'request_id': OP,
              'authorized': backup['content_sha256'], 'action': 'workflow:tool_center_cutover'}
    for old, new in zip(before['instances'], after['instances']):
        await service.record(**common, target=old['id'], before=old, after=new)
    for old, new in zip(before['bindings'], after['bindings']):
        if old != new:
            await service.record(**common, target=old['id'], before=old, after=new)
    await service.record(**common, target=before['expert']['id'], before=before['expert'], after=after['expert'])
    await service.record(**common, target=TEMPLATE, before=before['global_template'], after=after['global_template'])
    await service.record(**common, target=OP,
        before={'backup_sha256': backup['content_sha256']},
        after={'expertVersionId': NEW_EXPERT_VERSION, 'deploymentId': NEW_DEPLOYMENT,
               'templateVersions': [r['id'] for r in plan['template_versions']],
               'instanceIds': [r['id'] for r in before['instances']], 'taskBackfillScheduled': False})
    await session.flush()


async def run(args):
    from sqlalchemy import text
    from flowhub_api.db.session import Base, SessionFactory
    from flowhub_api.models import User
    from flowhub_api.models.expert import ExpertJob
    from flowhub_api.models.tool import ToolAutomationJob
    backup = None
    if args.backup:
        backup = json.loads(Path(args.backup).read_text())
        check_backup(backup)
    if args.apply or args.verify:
        require(backup is not None, '--backup is required')
    if args.apply:
        require(args.reviewed_sha256 == backup['content_sha256'], 'Reviewed backup checksum is required')
    async with SessionFactory() as session:
        async with session.begin():
            await session.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE' +
                                       ('' if args.apply else ' READ ONLY')))
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '60s'"))
            await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '90s'"))
            await session.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                {'key': int.from_bytes(hashlib.sha256(OP.encode()).digest()[:8], 'big', signed=True)})
            tables = Base.metadata.tables
            current = await snapshot(session, tables, lock=args.apply)
            actor_id = backup['plan']['actor']['id'] if backup else args.actor_id
            require(bool(actor_id), '--actor-id is required for a dry-run')
            actor = await session.get(User, actor_id)
            if not (args.apply or args.verify):
                assert_idle(current)
                plan = build_plan(current, {'id': actor_id, 'name': actor.name if actor else ''},
                                  Path(args.prompt_file).read_text() if args.prompt_file else None)
                await validate_plan(session, plan, actor)
                # Destination conflicts detected during review; do not seed or overwrite.
                for name, key, values in [('expert_versions', 'id', [NEW_EXPERT_VERSION]),
                    ('expert_deployments', 'id', [NEW_DEPLOYMENT]),
                    ('template_versions', 'id', [r['id'] for r in plan['template_versions']]),
                    ('template_canvases', 'version_id', [r['version_id'] for r in plan['canvases']])]:
                    require(not await fetch(session, tables[name], tables[name].c[key].in_(values)),
                            'Destination exists already; use original backup to verify or reapply')
                summary = {'format': 1, 'operation': OP, 'scope': list(IDS),
                           'captured_at': datetime.now(timezone.utc).isoformat(),
                           'protected': current, 'plan': plan}
                summary['content_sha256'] = digest(summary)
            else:
                baseline, plan = backup['protected'], backup['plan']
                validate_original(baseline)
                await validate_plan(session, plan, actor)
                state = migration_state(current['instances'], baseline['instances'])
                compare(current, expected_protected(baseline, plan) if state == 'applied' else baseline)
                if state == 'applied':
                    await target_rows(session, tables, plan)
                    await engine_checks(session, plan)
                    summary = {'mode': 'verify' if args.verify else 'apply', 'verified': True,
                               'changed': 0, 'audits_added': 0, 'idempotent': True}
                else:
                    require(args.apply, 'Cutover not applied')
                    assert_idle(current)
                    created = await target_rows(session, tables, plan, create=True)
                    await apply_updates(session, tables, backup)
                    await audits(session, backup)
                    await engine_checks(session, plan)
                    compare(await snapshot(session, tables), expected_protected(baseline, plan))
                    await target_rows(session, tables, plan)
                    summary = {'mode': 'apply', 'verified': True, 'changed_instances': 11,
                               'changed_bindings': 2, 'created_rows': created, 'audits_added': 16,
                               'taskBackfillScheduled': False}
                summary['backup_sha256'] = backup['content_sha256']
                summary['expertVersionId'] = NEW_EXPERT_VERSION
                summary['deploymentId'] = NEW_DEPLOYMENT
    # A successful write summary is emitted only after commit succeeds.
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=serial,
                     indent=None if args.apply or args.verify else 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--verify', action='store_true')
    parser.add_argument('--actor-id')
    parser.add_argument('--prompt-file', help='Optional fully reviewed replacement prompt for dry-run plan')
    parser.add_argument('--backup', help='Container path to reviewed dry-run JSON')
    parser.add_argument('--reviewed-sha256', help='Reviewed backup content_sha256')
    args = parser.parse_args()
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=180))
    except Exception as exc:
        # Driver errors may contain secrets or business contents: print only safe diagnostics.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
