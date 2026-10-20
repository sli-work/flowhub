"""Pure migration-plan tests; never connect to application databases."""
import copy
import runpy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'cutover_acceptance_tools.py'


def helpers():
    return runpy.run_path(str(SCRIPT))


def canvas():
    return {'version_id': 'tpl-req:v16-acceptance', 'nodes': [
        {'id': 'start', 'label': '开始', 'type': 'start', 'cfg': {}},
        {'id': 'nmtidmrmc3cb', 'label': '验收', 'type': 'end',
         'cfg': {'handler': 'Expert 协助', 'expert': {'expertDeploymentId': 'depb165eb5f8bca'},
                 'schema': [{'key': 'summary', 'type': 'file'}],
                 'deliverable': {'instruction': 'preserve'}, 'split': {'mode': 'off'}}}],
        'edges': [['start', 'nmtidmrmc3cb']], 'fallbacks': []}


def test_patch_only_changes_acceptance_deployment_and_automation():
    h = helpers()
    original = canvas()
    before = copy.deepcopy(original)
    patched = h['patch_canvas'](original, 'v16-tools')
    assert original == before
    cfg = patched['nodes'][1]['cfg']
    assert cfg['expert']['expertDeploymentId'] == h['NEW_DEPLOYMENT']
    assert {r['event'] for r in cfg['automation']['rules']} == {'node_entered', 'expert_output_validated'}
    cfg.pop('automation')
    cfg['expert'] = before['nodes'][1]['cfg']['expert']
    patched['version_id'] = before['version_id']
    assert patched == before


def test_patch_rejects_schema_and_deployment_drift():
    h = helpers()
    for field, value in [('schema', []), ('expert', {'expertDeploymentId': 'someone-else'})]:
        original = canvas()
        original['nodes'][1]['cfg'][field] = value
        with pytest.raises(RuntimeError):
            h['patch_canvas'](original, 'v16-tools')


def test_patch_rejects_existing_automation():
    h = helpers()
    original = canvas()
    original['nodes'][1]['cfg']['automation'] = {'rules': [{'id': 'unknown'}]}
    with pytest.raises(RuntimeError):
        h['patch_canvas'](original, 'v16-tools')


def test_instance_state_rejects_partial_and_unknown_scope():
    h = helpers()
    original = [{'id': 'i1', 'work_item_id': 'w1', 'version': 'v16-acceptance'},
                {'id': 'i2', 'work_item_id': 'w2', 'version': 'v20-acceptance'}]
    assert h['migration_state'](original, original) == 'original'
    after = [{**row, 'version': h['VERSION_MAP'][row['version']]} for row in original]
    assert h['migration_state'](after, original) == 'applied'
    with pytest.raises(RuntimeError):
        h['migration_state']([original[0], after[1]], original)
    with pytest.raises(RuntimeError):
        h['migration_state'](original[:1], original)


def test_backup_checksum_binds_review():
    h = helpers()
    backup = {'format': 1, 'operation': h['OP'], 'scope': list(h['IDS']), 'protected': {}, 'plan': {}}
    backup['content_sha256'] = h['digest'](backup)
    h['check_backup'](backup)
    backup['plan']['unreviewed'] = True
    with pytest.raises(RuntimeError):
        h['check_backup'](backup)


def test_grant_preserves_other_grants_and_is_pinned():
    h = helpers()
    original = [{'tool_version_id': 'other', 'scope': 'current_work_item', 'approval': 'required'}]
    before = copy.deepcopy(original)
    grants = h['archive_grants'](original)
    assert original == before
    assert grants[0] == original[0]
    assert grants[-1] == {'tool_version_id': h['TOOL_VERSION'], 'scope': 'current_work_item', 'approval': 'inherit'}
    assert h['archive_grants'](grants) == grants


def test_prompt_safety_and_compact_version_lengths():
    h = helpers()
    prompt = h['tool_prompt']('原始输出契约')
    assert prompt.startswith('原始输出契约')
    assert '待人工验收' in prompt and '不得自动提交' in prompt
    assert all(len(v) <= 16 for v in h['VERSION_MAP'].values())


def baseline(h):
    sources = ['v16-acceptance'] * 3 + ['v18-acceptance'] + ['v20-acceptance'] * 7
    versions = [*h['VERSION_MAP'], 'v21']
    data = {
        'work_items': [{'id': wid, 'type': 'requirement', 'status': 'in_progress'} for wid in h['IDS']],
        'instances': [{'id': f'i-{idx}', 'work_item_id': wid, 'template_id': 'tpl-req',
                       'state': 'running', 'current_node': 'cursor', 'version': source}
                      for idx, (wid, source) in enumerate(zip(h['IDS'], sources))],
        'projects': [{'id': key, 'code': value} for key, value in h['PROJECTS'].items()],
        'global_template': {'versions': ['v21'], 'nodes': [{'id': 'frozen'}]},
        'expert': {'id': 'expert', 'status': 'published', 'current_version_id': 'old', 'updated_at': 'before'},
        'old_deployment': {'id': h['OLD_DEPLOYMENT'], 'expert_id': 'expert',
                           'name': 'acceptance', 'status': 'active', 'expert_version_id': 'old'},
        'old_expert_version': {'id': 'old', 'expert_id': 'expert', 'status': 'published', 'tested_at': 'source-test',
                               'tool_policies': {}, 'system_prompt': 'source prompt', 'tool_bindings': []},
        'bindings': [{'id': project, 'project_id': project, 'template_id': 'tpl-req',
                      'type': 'requirement', 'version': 'v21', 'status': 'active'} for project in h['PROJECTS']],
        'versions': [{'id': 'tpl-req:' + v, 'version': v, 'status': 'published', 'nodes': 2} for v in versions],
        'canvases': [{**canvas(), 'version_id': 'tpl-req:' + v} for v in versions],
        'tasks': [{'id': 'task', 'expert_pending': False, 'form_values': {'file': ['doc']}}],
        'runs': [], 'expert_jobs': [], 'tool_invocations': [], 'tool_automation_jobs': [],
    }
    return data


def test_plan_preserves_source_and_limits_configuration_updates():
    h = helpers()
    original = baseline(h)
    before = copy.deepcopy(original)
    plan = h['build_plan'](original, {'id': 'admin', 'name': '管理员'})
    assert original == before
    assert plan['expert_version']['tested_at'] == 'source-test'
    assert plan['expert_version']['version'] == 'v0.2-tools'
    assert len(plan['template_versions']) == len(plan['canvases']) == 4
    assert {r['version'] for r in plan['template_versions'] if r['status'] == 'published'} == {'v22'}
    expected = h['expected_protected'](original, plan)
    for key in ('work_items', 'tasks', 'old_deployment', 'old_expert_version', 'canvases', 'versions'):
        assert expected[key] == original[key]
    assert expected['global_template']['nodes'] == original['global_template']['nodes']
    assert expected['global_template']['versions'] == ['v21', 'v22']
    assert {r['version'] for r in expected['bindings']} == {'v22'}
    assert expected['expert']['current_version_id'] == h['NEW_EXPERT_VERSION']


def test_idle_guard_rejects_inflight_work():
    h = helpers()
    original = baseline(h)
    h['assert_idle'](original)
    for collection, active in [('runs', {'id': 'run', 'status': 'interrupted'}),
                               ('tool_invocations', {'id': 'call', 'status': 'awaiting_approval'}),
                               ('tool_automation_jobs', {'id': 'job', 'status': 'running'})]:
        changed = {**original, collection: [active]}
        with pytest.raises(RuntimeError):
            h['assert_idle'](changed)
    changed = {**original, 'tasks': [{'expert_pending': True}]}
    with pytest.raises(RuntimeError):
        h['assert_idle'](changed)


def test_protected_comparison_rejects_task_or_cursor_changes():
    h = helpers()
    original = baseline(h)
    for collection, field, value in [('tasks', 'form_values', {'later': 'activity'}),
                                      ('instances', 'current_node', 'moved')]:
        changed = copy.deepcopy(original)
        changed[collection][0][field] = value
        with pytest.raises(RuntimeError):
            h['compare'](changed, original)
