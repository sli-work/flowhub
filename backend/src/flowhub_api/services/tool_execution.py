"""One authorization, approval, audit and idempotency boundary for every tool call."""
import hashlib
import json
import logging
import re
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.models import ExpertVersion, McpServer, WorkItem
from flowhub_api.models.tool import ToolDefinition, ToolInvocation, ToolVersion
from flowhub_api.services.agent_context import can_read_task
from flowhub_api.services.audit import AuditService
from flowhub_api.services.tool_registry import contract_checksum, new_id, now_iso, validate_context_bindings, validate_schema


logger = logging.getLogger(__name__)

ADMIN_ROLES = {'system_admin', 'organization_admin'}
_SECRET_KEYS = re.compile(r'(credential|authorization|password|secret|api.?key|access.?token|refresh.?token|^token$)', re.I)


def is_admin(user):
    return any(role.id in ADMIN_ROLES for role in user.roles)


def invocation_key(run_id, generation, version_id, arguments):
    data = json.dumps([run_id, generation, version_id, arguments], sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(data.encode()).hexdigest()


def sanitize_result(value, secrets=()):
    if isinstance(value, dict):
        return {str(key): '[redacted]' if _SECRET_KEYS.search(str(key)) else sanitize_result(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_result(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[redacted]')
        return value
    return value


async def authorize_tool(session, *, user, version, task, run, expert_version_id, source):
    binding = None
    if source == 'test':
        if not is_admin(user):
            raise BizError(BizCode.PERM_DENIED, '仅管理员可测试工具')
    elif source in {'expert', 'automation'}:
        expert = await session.get(ExpertVersion, expert_version_id) if expert_version_id else None
        snapshot = getattr(run, 'config_snapshot', None) or {}
        bindings = snapshot['tool_bindings'] if 'tool_bindings' in snapshot else (getattr(expert, 'tool_bindings', []) if expert else [])
        binding = next((item for item in bindings if isinstance(item, dict) and item.get('tool_version_id') == version.id), None)
        if not binding or binding.get('scope', 'current_work_item') != 'current_work_item':
            raise BizError(BizCode.PERM_DENIED, 'Expert 版本未授权此工具或范围无效')
        if task is None:
            raise BizError(BizCode.PERM_DENIED, '工具必须绑定当前任务')
    else:
        raise BizError(BizCode.VALIDATION, '无效的工具调用来源')
    if getattr(user, 'deleted', False) or getattr(user, 'status', 'active') != 'active':
        raise BizError(BizCode.PERM_DENIED, '工具调用身份已停用')
    if run and (run.expert_version_id != expert_version_id or run.requested_by != user.id or run.task_id != getattr(task, 'id', None)):
        raise BizError(BizCode.PERM_DENIED, '工具调用与运行身份或任务不匹配')
    pinned_node = (getattr(run, 'config_snapshot', None) or {}).get('node_id')
    if run and task and pinned_node and pinned_node != getattr(task, 'node_id', None):
        raise BizError(BizCode.LOCKED, '任务节点已变更，旧运行不能继续调用工具')
    if task:
        # Always read live identity and task scope, including when resuming approval.
        if not await can_read_task(session, user, task):
            raise BizError(BizCode.PERM_DENIED, '当前用户无权访问该任务')
        wi = await session.get(WorkItem, task.wi_id)
        if not wi:
            raise BizError(BizCode.NOT_FOUND, '工作项不存在')
        if version.risk not in {'read', 'generate'} and (task.status in {'completed', 'cancelled'} or wi.status in {'closed', 'cancelled', 'archived'}):
            raise BizError(BizCode.LOCKED, '已结束或归档任务不允许写工具调用')
    return binding


def scoped_mcp_arguments(version, arguments, task):
    try:
        mappings = validate_context_bindings(version.input_schema, version.config_json.get('context_bindings', {}), require_scope=True)
    except ValueError as exc:
        raise BizError(BizCode.PERM_DENIED, str(exc)) from None
    properties = version.input_schema.get('properties', {})
    if set(arguments) - set(properties):
        raise BizError(BizCode.VALIDATION, 'MCP 参数包含未声明的目标或字段')
    context = {'work_item_id': task.wi_id, 'task_id': task.id, 'project': task.project}
    injected = {parameter: context[name] for parameter, name in mappings.items()}
    for parameter, value in injected.items():
        if parameter in arguments and arguments[parameter] != value:
            raise BizError(BizCode.PERM_DENIED, 'MCP 参数目标与当前任务范围冲突')
    return {**arguments, **injected}


def invocation_brief(invocation):
    return {'id': invocation.id, 'invocationId': invocation.id, 'toolVersionId': invocation.tool_version_id,
            'expertVersionId': invocation.expert_version_id, 'runId': invocation.run_id, 'generation': invocation.generation,
            'taskId': invocation.task_id, 'requestedBy': invocation.requested_by, 'source': invocation.source,
            'input': sanitize_result(invocation.input_json), 'result': sanitize_result(invocation.result_json),
            'status': invocation.status, 'error': invocation.error, 'approvalStatus': invocation.approval_status,
            'decidedBy': invocation.decided_by, 'createdAt': invocation.created_at, 'finishedAt': invocation.finished_at}


def outcome(invocation):
    return {'status': invocation.status, 'invocationId': invocation.id,
            'result': invocation.result_json or {}, 'error': invocation.error or ''}


async def _dispatch(session, tool, version, arguments, task, user, run, *, frozen_sources=None):
    if tool.source == 'native':
        adapter = version.config_json.get('adapter')
        if adapter == 'flowhub_document_archive':
            if task is None:
                raise ValueError('Archive requires a current task')
            from flowhub_api.services.document_archive import archive_documents
            frozen_options = {'frozen_sources': frozen_sources} if frozen_sources is not None else {}
            return await archive_documents(session, task=task, user=user,
                                           source_document_ids=arguments.get('source_document_ids', []),
                                           run=run, summary_content=arguments.get('summary_content'), **frozen_options)
        if adapter == 'flowhub_attachment_list':
            if task is None:
                raise ValueError('Attachment list requires a current task')
            from flowhub_api.services.attachment_tools import create_attachment_tool_bundle
            bundle = await create_attachment_tool_bundle(session, task, user)
            return json.loads(await bundle.tools[0].ainvoke({}))
        raise ValueError('Unsupported native adapter')
    if tool.source == 'mcp':
        from flowhub_api.services.mcp_client import call_tool
        server = await session.get(McpServer, tool.server_id)
        if not server or server.endpoint != version.config_json.get('endpoint') or server.id != version.config_json.get('server_id'):
            raise ValueError('MCP endpoint changed; rediscover and publish a new version')
        return await call_tool(server, version.config_json['external_name'], arguments)
    raise ValueError('Unsupported tool source')


async def _execute_tool(session, *, user, tool_version_id, arguments, expert_version_id=None, task=None,
                       run=None, idempotency_key, source='expert', dry_run=False, frozen_sources=None):
    version = await session.get(ToolVersion, tool_version_id)
    tool = await session.get(ToolDefinition, version.tool_id) if version else None
    if not version or not tool:
        raise BizError(BizCode.NOT_FOUND, '工具版本不存在')
    if tool.status != 'active':
        raise BizError(BizCode.LOCKED, '工具已停用')
    if source != 'test' and version.status != 'published':
        raise BizError(BizCode.LOCKED, '工具版本尚未发布')
    if version.checksum != contract_checksum(version):
        raise BizError(BizCode.LOCKED, '工具版本合同已变更，拒绝执行')
    binding = await authorize_tool(session, user=user, version=version, task=task, run=run, expert_version_id=expert_version_id, source=source)
    if tool.source == 'mcp' and source != 'test':
        arguments = scoped_mcp_arguments(version, arguments, task)
    elif tool.source == 'mcp' and set(arguments) - set(version.input_schema.get('properties', {})):
        raise BizError(BizCode.VALIDATION, 'MCP 参数包含未声明字段')
    try:
        validate_schema(version.input_schema, arguments)
    except Exception as exc:
        raise BizError(BizCode.VALIDATION, str(exc)) from None
    if arguments.get('summary_content') is not None and source != 'automation':
        raise BizError(BizCode.VALIDATION, '归档摘要只能来自验证通过的运行结果')
    async def persist():
        if source == 'automation' and tool.source == 'native':
            await session.flush()
        else:
            await session.commit()

    generation = getattr(run, 'execution_generation', 0 if source == 'automation' else 1)
    invocation = (await session.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == idempotency_key))).scalar_one_or_none()
    if invocation:
        expected = (tool_version_id, expert_version_id, getattr(task, 'id', None), getattr(run, 'id', None), generation, user.id, source, arguments)
        actual = (invocation.tool_version_id, invocation.expert_version_id, invocation.task_id, invocation.run_id,
                  invocation.generation, invocation.requested_by, invocation.source, invocation.input_json)
        if actual != expected:
            raise BizError(BizCode.DUPLICATE_OPERATION, '幂等标识已用于不同调用')
        if invocation.status == 'failed' and source == 'automation' and tool.source == 'native' and version.config_json.get('adapter') == 'flowhub_document_archive':
            invocation.status, invocation.error, invocation.finished_at = 'queued', '', ''
        if invocation.status not in {'queued', 'awaiting_approval'}:
            return outcome(invocation)
        if invocation.approval_status == 'pending':
            return outcome(invocation)
        if invocation.approval_status == 'rejected':
            invocation.status = 'denied'
            return outcome(invocation)
    else:
        needs_approval = version.approval != 'none' or bool(binding and binding.get('approval') == 'required')
        invocation = ToolInvocation(id=new_id('ti'), idempotency_key=idempotency_key, tool_version_id=version.id,
                                    expert_version_id=expert_version_id, run_id=getattr(run, 'id', None), generation=generation,
                                    task_id=getattr(task, 'id', None), requested_by=user.id, source=source, input_json=arguments,
                                    status='queued', approval_status='not_required', created_at=now_iso())
        # Preview never creates executable approval state and never claims a successful external call.
        if dry_run and version.risk not in {'read', 'generate'}:
            invocation.status = 'succeeded'
            invocation.result_json = {'preview': True, 'wouldExecute': tool.slug, 'arguments': sanitize_result(arguments)}
            invocation.finished_at = now_iso()
        elif needs_approval:
            invocation.status, invocation.approval_status = 'awaiting_approval', 'pending'
        try:
            async with session.begin_nested():
                session.add(invocation)
                await session.flush()
        except IntegrityError:
            # A simultaneous request owns this key. Never dispatch a second call.
            existing = (await session.execute(select(ToolInvocation).where(ToolInvocation.idempotency_key == idempotency_key))).scalar_one()
            if (existing.tool_version_id, existing.requested_by, existing.input_json, existing.expert_version_id, existing.task_id, existing.run_id, existing.generation, existing.source) != (tool_version_id, user.id, arguments, expert_version_id, getattr(task, 'id', None), getattr(run, 'id', None), generation, source):
                raise BizError(BizCode.DUPLICATE_OPERATION, '幂等标识已用于不同调用')
            return outcome(existing)
        await AuditService(session).record(actor=getattr(user, 'name', user.id), action='tool:request', target=invocation.id,
                                            after={'toolVersionId': version.id, 'status': invocation.status, 'source': source, 'preview': dry_run})
        await persist()
        if invocation.status != 'queued':
            return outcome(invocation)
    claimed = await session.execute(update(ToolInvocation).where(ToolInvocation.id == invocation.id,
                                         ToolInvocation.status.in_(['queued', 'awaiting_approval'])).values(status='running'))
    if not claimed.rowcount:
        await session.refresh(invocation)
        return outcome(invocation)
    # Persist the execution boundary before an external side effect. A crashed MCP
    # write stays running/unknown and can never be replayed by this key.
    await persist()
    try:
        async with session.begin_nested():
            dispatch_options = {'frozen_sources': frozen_sources} if frozen_sources is not None else {}
            result = await _dispatch(session, tool, version, arguments, task, user, run, **dispatch_options)
            validate_schema(version.output_schema, result)
        secrets = []
        if tool.source == 'mcp':
            from flowhub_api.services.crypto import decrypt_secret
            server = await session.get(McpServer, tool.server_id)
            if server and server.credentials:
                plaintext = decrypt_secret(server.credentials)
                secrets.append(plaintext)
                try:
                    secrets.extend(value for value in json.loads(plaintext).values() if isinstance(value, str))
                except (ValueError, AttributeError):
                    pass
        invocation.result_json = sanitize_result(result, secrets)
        invocation.status = 'succeeded'
    except Exception as exc:
        logger.warning('Tool invocation failed invocation=%s adapter=%s exception=%s', invocation.id, tool.source, type(exc).__name__)
        # The external response may have been lost after a committed write. Do not retry.
        invocation.status = 'unknown' if tool.source == 'mcp' and version.risk not in {'read', 'generate'} else 'failed'
        safe_cause = str(exc)[:240] if tool.source == 'native' and isinstance(exc, (ValueError, BizError)) else '请核查权限、输入和服务配置'
        invocation.error = '远程写入结果不确定，请人工核查；禁止自动重试' if invocation.status == 'unknown' else f'工具执行失败：{safe_cause}'
    invocation.finished_at = now_iso()
    await AuditService(session).record(actor=getattr(user, 'name', user.id), action='tool:execute', target=invocation.id,
                                        result='success' if invocation.status == 'succeeded' else 'failed',
                                        after={'status': invocation.status, 'toolVersionId': version.id}, failure_reason=invocation.error or None)
    await persist()
    return outcome(invocation)


async def execute_tool(session, *, user, tool_version_id, arguments, expert_version_id=None, task=None,
                       run=None, idempotency_key, source='expert', dry_run=False, frozen_sources=None):
    try:
        return await _execute_tool(session, user=user, tool_version_id=tool_version_id, arguments=arguments,
                                   expert_version_id=expert_version_id, task=task, run=run,
                                   idempotency_key=idempotency_key, source=source, dry_run=dry_run, frozen_sources=frozen_sources)
    except BizError as exc:
        await AuditService(session).record(actor=getattr(user, 'name', user.id), action='tool:denied', target=tool_version_id,
                                            result='denied', failure_reason=str(exc.detail)[:240],
                                            after={'source': source, 'taskId': getattr(task, 'id', None)})
        if source == 'automation':
            await session.flush()
        else:
            await session.commit()
        raise
