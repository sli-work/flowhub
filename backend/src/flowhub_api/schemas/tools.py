"""Validated Tool Center request bodies; no credential fields are accepted."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ToolVersionReq(BaseModel):
    model_config = ConfigDict(extra='forbid')
    input_schema: dict = Field(default_factory=lambda: {'type': 'object', 'properties': {}, 'additionalProperties': False})
    output_schema: dict = Field(default_factory=dict)
    config_json: dict = Field(default_factory=dict)
    risk: Literal['read', 'generate', 'write_draft', 'write_commit', 'critical'] = 'read'
    approval: Literal['none', 'required', 'administrator_only'] = 'none'


class ToolTestReq(BaseModel):
    model_config = ConfigDict(extra='forbid')
    arguments: dict = Field(default_factory=dict)
    task_id: str | None = Field(default=None, max_length=40)
    dry_run: bool = True


class ToolStatusReq(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['active', 'disabled']


class ToolDecisionReq(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision: Literal['approved', 'rejected']
