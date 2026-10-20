"""Expose only frozen Expert tool grants to the LangGraph runtime."""
from dataclasses import dataclass, field
import json
from langchain_core.tools import StructuredTool
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from flowhub_api.models.tool import ToolDefinition, ToolVersion
from flowhub_api.services.tool_execution import execute_tool, invocation_key, scoped_mcp_arguments


@dataclass
class BusinessToolBundle:
    tools: list = field(default_factory=list)
    traces: list = field(default_factory=list)
    evidence_context: list = field(default_factory=list)


class ToolArguments(BaseModel):
    arguments: dict = Field(default_factory=dict, description='The exact JSON object satisfying the tool input schema')


async def create_business_tool_bundle(session, run, version, task, user):
    bundle = BusinessToolBundle()
    snapshot = getattr(run, 'config_snapshot', None) or {}
    bindings = snapshot['tool_bindings'] if 'tool_bindings' in snapshot else getattr(version, 'tool_bindings', [])
    for binding in bindings or []:
        pinned = await session.get(ToolVersion, binding['tool_version_id'])
        definition = await session.get(ToolDefinition, pinned.tool_id) if pinned else None
        if not definition or not pinned or pinned.status != 'published' or definition.status != 'active':
            continue

        def make_wrapper(tool_version, tool):
            async def invoke(arguments: dict) -> str:
                if tool.source == 'mcp':
                    arguments = scoped_mcp_arguments(tool_version, arguments, task)
                key = invocation_key(run.id, run.execution_generation, tool_version.id, arguments)
                result = await execute_tool(session, user=user, tool_version_id=tool_version.id, arguments=arguments,
                                            expert_version_id=version.id, task=task, run=run, idempotency_key=key)
                if result['status'] == 'awaiting_approval':
                    interrupt({'kind': 'tool_approval', 'toolInvocationId': result['invocationId'], 'tool': tool.name, 'risk': tool_version.risk})
                    # The durable ledger, rather than the arbitrary resume payload, decides authorization.
                    result = await execute_tool(session, user=user, tool_version_id=tool_version.id, arguments=arguments,
                                                expert_version_id=version.id, task=task, run=run, idempotency_key=key)
                bundle.traces.append({'tool': tool.slug, 'status': result['status'], 'invocationId': result['invocationId'],
                                      'summary': result.get('error') or '工具调用已记录'})
                return json.dumps(result, ensure_ascii=False)
            return invoke

        bundle.tools.append(StructuredTool.from_function(coroutine=make_wrapper(pinned, definition),
            name=f'business_{pinned.id.replace("-", "_")}', args_schema=ToolArguments,
            description=f'{definition.name}: {definition.description}\nInput JSON schema: {json.dumps(pinned.input_schema, ensure_ascii=False)}\nServer supplies these context parameters; omit them: {list(pinned.config_json.get("context_bindings", {}))}'))
    return bundle
