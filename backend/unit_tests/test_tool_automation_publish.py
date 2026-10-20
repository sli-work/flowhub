from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from flowhub_api.routes.templates import _validate_automation_bindings
from flowhub_api.services import tool_automation

@pytest.mark.asyncio
async def test_archive_rules_cannot_publish_automatic_human_acceptance(monkeypatch):
    monkeypatch.setattr(tool_automation, 'validate_automation_rule', AsyncMock())
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status='active', expert_version_id='ev1')))
    node = {'id': 'accept', 'type': 'end', 'cfg': {'handler': 'Expert 自动',
        'expert': {'expertDeploymentId': 'dep1'}, 'automation': {'rules': [
            {'id': 'archive', 'event': 'node_entered', 'toolVersionId': 'tv1', 'enabled': True}]}}}
    errors = await _validate_automation_bindings(session, {'nodes': [node]})
    assert any('人工' in error['message'] for error in errors)
    node['cfg']['handler'] = 'Expert 协助'
    assert await _validate_automation_bindings(session, {'nodes': [node]}) == []
