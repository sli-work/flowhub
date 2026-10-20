from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from flowhub_api.models import DocItem
from flowhub_api.routes.experts import version_brief
from flowhub_api.schemas.api import ExpertCreateReq, ExpertVersionReq

BINDING = {"tool_version_id": "tv-archive-1", "scope": "current_work_item", "approval": "inherit"}


def test_expert_tool_bindings_round_trip_and_default_to_empty():
    for schema in (ExpertCreateReq, ExpertVersionReq):
        kwargs = {"name": "Acceptance", "slug": "acceptance"} if schema is ExpertCreateReq else {}
        body = schema(**kwargs, tool_bindings=[BINDING])
        assert body.model_dump()["tool_bindings"] == [BINDING]
        assert schema(**kwargs).model_dump()["tool_bindings"] == []


def test_tool_binding_scope_and_policy_are_not_arbitrary():
    for patch in ({"scope": "all_projects"}, {"approval": "bypass"}):
        with pytest.raises(ValidationError):
            ExpertVersionReq(tool_bindings=[{**BINDING, **patch}])


def test_version_detail_returns_grants_for_editor_hydration():
    version = SimpleNamespace(id="ev1", expert_id="exp1", version="v0.2", status="draft",
        system_prompt="acceptance", provider_model_id="m1", skills=[], knowledge_base_ids=[],
        tool_policies={}, tool_bindings=[BINDING], validation={}, tested_at="", published_at="")
    assert version_brief(version)["toolBindings"] == [BINDING]


def test_archival_records_support_durable_provenance_and_unique_keys():
    columns = DocItem.__table__.columns
    assert columns["archive_key"].nullable
    assert columns["archive_key"].unique
    assert columns["provenance"].nullable
