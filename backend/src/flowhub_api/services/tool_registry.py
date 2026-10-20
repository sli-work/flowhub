"""Catalog and immutable, tested contracts shared by runtime and administration."""
from datetime import UTC, datetime
import hashlib
import json
import re
import uuid

from jsonschema import Draft202012Validator
from sqlalchemy import select
from flowhub_api.models.tool import ToolDefinition, ToolVersion

RISK_LEVELS = {'read', 'generate', 'write_draft', 'write_commit', 'critical'}
APPROVALS = {'none', 'required', 'administrator_only'}
CONTRACT_FIELDS = ('input_schema', 'output_schema', 'config_json', 'risk', 'approval')


def now_iso():
    return datetime.now(UTC).isoformat()


def new_id(prefix='tool'):
    return f'{prefix}-{uuid.uuid4().hex}'[:32]


def contract_checksum(contract) -> str:
    value = {key: contract.get(key) if isinstance(contract, dict) else getattr(contract, key) for key in CONTRACT_FIELDS}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_schema(schema: dict, value) -> None:
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda err: str(list(err.path)))
    if errors:
        path = '.'.join(str(part) for part in errors[0].path) or '$'
        raise ValueError(f'JSON schema validation failed at {path}: {errors[0].validator}')


def validate_contract(contract: dict) -> None:
    if contract['input_schema'].get('type') != 'object':
        raise ValueError('Tool input schema must describe an object')
    for key in ('input_schema', 'output_schema'):
        Draft202012Validator.check_schema(contract[key])
    if contract['risk'] not in RISK_LEVELS or contract['approval'] not in APPROVALS:
        raise ValueError('Unsupported tool risk or approval policy')
    if contract['risk'] in {'write_commit', 'critical'} and contract['approval'] == 'none':
        raise ValueError('Committed and critical writes require approval')
    if contract['risk'] == 'critical' and contract['approval'] != 'administrator_only':
        raise ValueError('Critical tools require administrator approval')


def validate_context_bindings(schema, bindings, *, require_scope=False):
    if not isinstance(bindings, dict) or (require_scope and not bindings):
        raise ValueError('MCP task scope requires explicit context bindings')
    properties = schema.get('properties', {})
    if not isinstance(properties, dict):
        raise ValueError('MCP context bindings require declared input properties')
    for parameter, context in bindings.items():
        if parameter not in properties or context not in {'work_item_id', 'task_id', 'project'}:
            raise ValueError('MCP context binding property or context is invalid')
    if require_scope:
        if not any(context in {'work_item_id', 'task_id'} for context in bindings.values()):
            raise ValueError('MCP task scope requires a work-item or task binding, not project alone')
        safe_payloads = {'title', 'content', 'text', 'description', 'summary', 'body', 'comment', 'message', 'reason', 'note', 'notes', 'format', 'language', 'dry_run'}
        for parameter, spec in properties.items():
            target = parameter in {'id', 'ids', 'project', 'project_name', 'document', 'documents', 'workspace', 'namespace', 'url', 'path'} or bool(re.search(r'(?:_ids?|Ids?|IDs?)$', parameter))
            if target and parameter not in bindings:
                raise ValueError('MCP target parameters require context binding or a scoped connector adapter')
            if parameter not in bindings:
                if parameter not in safe_payloads:
                    raise ValueError('Unknown MCP argument semantics require a scoped connector adapter')
                scalar_types = {'string', 'number', 'integer', 'boolean', 'null'}
                if not isinstance(spec, dict) or not isinstance(spec.get('type'), str) or spec['type'] not in scalar_types | {'array'} or any(key in spec for key in ('anyOf', 'oneOf', 'allOf', '$ref')):
                    raise ValueError('Nested or unbounded MCP targets require a scoped connector adapter')
                if spec['type'] == 'array':
                    items = spec.get('items', {})
                    if not isinstance(items, dict) or not isinstance(items.get('type'), str) or items['type'] not in scalar_types:
                        raise ValueError('Nested MCP targets require a scoped connector adapter')
    return dict(bindings)


def task_scope_ready(version):
    if version.config_json.get('adapter'):
        return True
    try:
        validate_context_bindings(version.input_schema, version.config_json.get('context_bindings', {}), require_scope=True)
        return True
    except ValueError:
        return False


def version_brief(version):
    return {'id': version.id, 'revision': version.revision, 'status': version.status,
            'inputSchema': version.input_schema, 'outputSchema': version.output_schema,
            'risk': version.risk, 'approval': version.approval, 'checksum': version.checksum,
            'testedAt': version.tested_at, 'createdAt': version.created_at,
            'contextBindings': version.config_json.get('context_bindings', {}), 'taskScopeReady': task_scope_ready(version)}


def tool_brief(tool, versions):
    return {'id': tool.id, 'name': tool.name, 'slug': tool.slug, 'description': tool.description,
            'source': tool.source, 'status': tool.status, 'serverId': tool.server_id,
            'externalName': tool.external_name, 'currentVersionId': tool.current_version_id,
            'versions': [version_brief(version) for version in versions]}


async def list_tools(session) -> list[dict]:
    definitions = (await session.execute(select(ToolDefinition).order_by(ToolDefinition.created_at, ToolDefinition.id))).scalars().all()
    versions = (await session.execute(select(ToolVersion).order_by(ToolVersion.revision.desc()))).scalars().all()
    return [tool_brief(tool, [v for v in versions if v.tool_id == tool.id]) for tool in definitions]


async def validate_tool_bindings(session, bindings) -> list[str]:
    if not isinstance(bindings, list):
        return ['工具授权必须是数组']
    errors, seen = [], set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - {'tool_version_id', 'scope', 'approval'}:
            errors.append('工具授权格式无效'); continue
        identifier = binding.get('tool_version_id')
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            errors.append('工具版本不能为空或重复'); continue
        seen.add(identifier)
        if binding.get('scope', 'current_work_item') != 'current_work_item' or binding.get('approval', 'inherit') not in {'inherit', 'required'}:
            errors.append('工具授权范围或审批策略无效'); continue
        version = await session.get(ToolVersion, identifier)
        tool = await session.get(ToolDefinition, version.tool_id) if version else None
        if not version or version.status != 'published' or not tool or tool.status != 'active':
            errors.append(f'工具版本不可用：{identifier}')
        elif getattr(tool, 'source', 'native') == 'mcp' and not task_scope_ready(version):
            errors.append(f'MCP 工具缺少可强制执行的任务范围映射：{identifier}')
        elif version.checksum != contract_checksum(version):
            errors.append(f'工具版本合同校验失败：{identifier}')
    return errors


async def seed_builtin_tools(session):
    specs = [
        ('document-archive', 'flowhub_document_archive', '归档节点交付物', '将当前节点授权前序交付物复制为独立验收归档，保留来源和人工验收状态。', 'write_draft',
         {'type': 'object', 'properties': {'source_document_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 200}, 'summary_content': {'type': 'string', 'maxLength': 200000}}, 'additionalProperties': False}),
        ('attachment-list', 'flowhub_attachment_list', '列出任务附件', '列出当前任务权限范围内的附件。', 'read',
         {'type': 'object', 'properties': {}, 'additionalProperties': False}),
    ]
    for suffix, adapter, name, description, risk, schema in specs:
        tool_id, version_id = f'tool-native-{suffix}', f'tv-native-{suffix}-1'
        if await session.get(ToolDefinition, tool_id):
            continue
        tool = ToolDefinition(id=tool_id, slug=adapter, name=name, description=description, source='native',
                              status='active', current_version_id=version_id, created_by='system', created_at=now_iso(), updated_at=now_iso())
        contract = dict(input_schema=schema, output_schema={'type': 'object'}, config_json={'adapter': adapter}, risk=risk, approval='none')
        version = ToolVersion(id=version_id, tool_id=tool_id, revision=1, status='published', **contract,
                              checksum=contract_checksum(contract), tested_at=now_iso(), created_at=now_iso())
        session.add(tool)
        await session.flush()
        session.add(version)
    await session.flush()


async def discover_server_tools(session, server, actor_id):
    from flowhub_api.services.mcp_client import discover_tools
    discovered = await discover_tools(server)
    for item in discovered:
        name = item['name']
        slug = f'mcp-{server.id}-{name}'
        if len(slug) > 160:
            slug = f'mcp-{server.id}-{hashlib.sha256(name.encode()).hexdigest()[:32]}'
        tool = (await session.execute(select(ToolDefinition).where(ToolDefinition.slug == slug))).scalar_one_or_none()
        if not tool:
            tool = ToolDefinition(id=new_id(), slug=slug, name=name, description=item.get('description', ''), source='mcp',
                                  server_id=server.id, external_name=name, status='active', created_by=actor_id, created_at=now_iso(), updated_at=now_iso())
            session.add(tool)
            await session.flush()
        # MCP annotations are untrusted hints: every discovered tool starts behind approval.
        contract = dict(input_schema=item.get('inputSchema', {'type': 'object'}), output_schema=item.get('outputSchema', {}),
                        config_json={'external_name': name, 'endpoint': server.endpoint, 'server_id': server.id}, risk='write_commit', approval='required')
        validate_contract(contract)
        existing = (await session.execute(select(ToolVersion).where(ToolVersion.tool_id == tool.id).order_by(ToolVersion.revision.desc()))).scalars().all()
        checksum = contract_checksum(contract)
        if not any(v.checksum == checksum for v in existing):
            session.add(ToolVersion(id=new_id('tv'), tool_id=tool.id, revision=max((v.revision for v in existing), default=0) + 1,
                                    status='draft', **contract, checksum=checksum, created_at=now_iso()))
    server.status, server.health, server.updated_at = 'active', '发现成功', now_iso()
    await session.flush()
    return [tool for tool in await list_tools(session) if tool['serverId'] == server.id]
