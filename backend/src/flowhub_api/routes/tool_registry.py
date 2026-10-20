"""Tool Center catalog, real MCP discovery and durable per-invocation decisions."""
from typing import Annotated
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import ExpertRun, McpServer, TaskItem, User
from flowhub_api.models.expert import ExpertJob
from flowhub_api.models.tool import ToolAutomationJob, ToolDefinition, ToolInvocation, ToolVersion
from flowhub_api.schemas.tools import ToolDecisionReq, ToolStatusReq, ToolTestReq, ToolVersionReq
from flowhub_api.services.agent_context import can_read_task
from flowhub_api.services.audit import AuditService
from flowhub_api.services.tool_execution import execute_tool, invocation_brief, is_admin
from flowhub_api.services.tool_registry import (contract_checksum, discover_server_tools, list_tools, new_id, now_iso,
                                                tool_brief, validate_context_bindings, validate_contract, version_brief)

router = APIRouter(prefix='/api/v1', tags=['tools'])
DB = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(get_current_user)]


def require_admin(user):
    if not is_admin(user) or getattr(user, 'deleted', False) or getattr(user, 'status', 'active') != 'active':
        raise BizError(BizCode.PERM_DENIED, '仅在用管理员可管理工具')


async def get_version(session, tool_id, version_id):
    tool, version = await session.get(ToolDefinition, tool_id), await session.get(ToolVersion, version_id)
    if not tool or not version or version.tool_id != tool.id:
        raise BizError(BizCode.NOT_FOUND, '工具版本不存在')
    return tool, version


@router.get('/tools')
async def catalog(session: DB, user: Actor):
    return ok({'items': await list_tools(session)})


@router.get('/tools/{tool_id}')
async def detail(tool_id: str, session: DB, user: Actor):
    tool = await session.get(ToolDefinition, tool_id)
    if not tool:
        raise BizError(BizCode.NOT_FOUND, '工具不存在')
    versions = (await session.execute(select(ToolVersion).where(ToolVersion.tool_id == tool.id).order_by(ToolVersion.revision.desc()))).scalars().all()
    return ok(tool_brief(tool, versions))


@router.patch('/tools/{tool_id}')
async def status(tool_id: str, body: ToolStatusReq, session: DB, user: Actor):
    require_admin(user)
    tool = await session.get(ToolDefinition, tool_id)
    if not tool:
        raise BizError(BizCode.NOT_FOUND, '工具不存在')
    tool.status, tool.updated_at = body.status, now_iso()
    await AuditService(session).record(actor=user.name, action='tool:status', target=tool.id, after={'status': body.status})
    await session.commit()
    return ok({'id': tool.id, 'status': tool.status})


@router.post('/tools/{tool_id}/versions')
async def create_version(tool_id: str, body: ToolVersionReq, session: DB, user: Actor):
    require_admin(user)
    tool = await session.get(ToolDefinition, tool_id, with_for_update=True)
    if not tool:
        raise BizError(BizCode.NOT_FOUND, '工具不存在')
    contract = body.model_dump()
    try:
        validate_contract(contract)
    except Exception as exc:
        raise BizError(BizCode.VALIDATION, str(exc)) from None
    latest = (await session.execute(select(ToolVersion).where(ToolVersion.tool_id == tool.id).order_by(ToolVersion.revision.desc()))).scalars().first()
    if tool.source == 'native':
        # Native implementation/configuration is maintained in code, never user-supplied executable metadata.
        if not latest or contract['config_json'] != latest.config_json or contract['input_schema'] != latest.input_schema or contract['output_schema'] != latest.output_schema:
            raise BizError(BizCode.VALIDATION, '原生工具合同由平台维护，只能调整风险和审批策略')
        if latest.risk in {'write_draft', 'write_commit', 'critical'} and contract['risk'] in {'read', 'generate'}:
            raise BizError(BizCode.VALIDATION, '写工具不能声明为只读')
    else:
        supplied_config = contract['config_json']
        frozen_config = {key: value for key, value in latest.config_json.items() if key != 'context_bindings'} if latest else {}
        if not latest or any(key != 'context_bindings' and (key not in frozen_config or value != frozen_config[key]) for key, value in supplied_config.items()):
            raise BizError(BizCode.VALIDATION, 'MCP 实现配置只能由发现过程生成')
        try:
            mappings = validate_context_bindings(contract['input_schema'], supplied_config.get('context_bindings', {}), require_scope=True)
        except ValueError as exc:
            raise BizError(BizCode.VALIDATION, str(exc)) from None
        contract = {**contract, 'config_json': {**frozen_config, 'context_bindings': mappings}}
        if contract['risk'] in {'read', 'generate'} or contract['approval'] == 'none':
            raise BizError(BizCode.VALIDATION, '远程工具必须保持写入风险和审批策略')
    version = ToolVersion(id=new_id('tv'), tool_id=tool.id, revision=(latest.revision if latest else 0) + 1,
                          status='draft', **contract, checksum=contract_checksum(contract), created_at=now_iso())
    session.add(version)
    await AuditService(session).record(actor=user.name, action='tool:version_create', target=version.id, after={'toolId': tool.id, 'checksum': version.checksum})
    await session.commit()
    return ok(version_brief(version))


@router.post('/tools/{tool_id}/versions/{version_id}/test')
async def test_version(tool_id: str, version_id: str, body: ToolTestReq, session: DB, user: Actor):
    require_admin(user)
    tool, version = await get_version(session, tool_id, version_id)
    task = await session.get(TaskItem, body.task_id) if body.task_id else None
    if body.task_id and not task:
        raise BizError(BizCode.NOT_FOUND, '任务不存在')
    result = await execute_tool(session, user=user, tool_version_id=version.id, arguments=body.arguments,
                                task=task, idempotency_key=new_id('test'), source='test', dry_run=body.dry_run)
    # A preview demonstrates validation only. Publishing remote writes requires an actual
    # approved execution, never a schema-only or metadata-only "successful test".
    if result['status'] == 'succeeded' and not result['result'].get('preview') and version.status != 'published':
        version.status, version.tested_at = 'tested', now_iso()
        await session.commit()
    return ok({**result, 'version': version_brief(version)})


@router.post('/tools/{tool_id}/versions/{version_id}/publish')
async def publish(tool_id: str, version_id: str, session: DB, user: Actor):
    require_admin(user)
    tool, version = await get_version(session, tool_id, version_id)
    if version.status != 'tested' or not version.tested_at or version.checksum != contract_checksum(version):
        raise BizError(BizCode.VALIDATION, '工具版本必须完成真实测试且合同未发生变化')
    version.status, tool.current_version_id, tool.updated_at = 'published', version.id, now_iso()
    await AuditService(session).record(actor=user.name, action='tool:publish', target=version.id, after={'toolId': tool.id, 'checksum': version.checksum})
    await session.commit()
    return ok(version_brief(version))


@router.post('/mcp-servers/{server_id}/discover-tools')
async def discover(server_id: str, session: DB, user: Actor):
    require_admin(user)
    server = await session.get(McpServer, server_id)
    if not server:
        raise BizError(BizCode.NOT_FOUND, 'MCP 服务不存在')
    try:
        async with session.begin_nested():
            items = await discover_server_tools(session, server, user.id)
    except Exception:
        server.status, server.health, server.updated_at = 'unhealthy', '发现失败', now_iso()
        await AuditService(session).record(actor=user.name, action='tool:discover', target=server.id, result='failed', failure_reason='MCP discovery failed')
        await session.commit()
        raise BizError(BizCode.VALIDATION, 'MCP 发现失败，请检查已批准的出站地址、协议和认证配置') from None
    await AuditService(session).record(actor=user.name, action='tool:discover', target=server.id, after={'count': len(items)})
    await session.commit()
    return ok({'items': items})


async def can_inspect(session, user, invocation):
    if is_admin(user):
        return True
    if invocation.requested_by != user.id:
        return False
    if not invocation.task_id:
        return True
    task = await session.get(TaskItem, invocation.task_id)
    return bool(task and await can_read_task(session, user, task))


@router.get('/tool-invocations')
async def invocations(session: DB, user: Actor, task_id: str | None = None, run_id: str | None = None):
    query = select(ToolInvocation).order_by(ToolInvocation.created_at.desc()).limit(200)
    if not is_admin(user):
        query = query.where(ToolInvocation.requested_by == user.id)
    if task_id:
        query = query.where(ToolInvocation.task_id == task_id)
    if run_id:
        query = query.where(ToolInvocation.run_id == run_id)
    rows = (await session.execute(query)).scalars().all()
    return ok({'items': [invocation_brief(item) for item in rows if await can_inspect(session, user, item)]})


async def resume_automation_invocation(session, invocation, decision):
    job = (await session.execute(select(ToolAutomationJob).where(
        ToolAutomationJob.task_id == invocation.task_id,
        ToolAutomationJob.run_id == invocation.run_id,
        ToolAutomationJob.generation == invocation.generation,
        ToolAutomationJob.result_json['invocationId'].as_string() == invocation.id,
    ).with_for_update())).scalar_one_or_none()
    if not job or job.status not in {'failed', 'awaiting_approval'}:
        raise BizError(BizCode.LOCKED, '自动化任务已变更或未等待此次审批')
    if decision == 'approved':
        job.status, job.claim_token, job.lease_until, job.finished_at, job.error = 'queued', '', None, '', ''
    else:
        job.status, job.error = 'failed', '工具调用审批已拒绝'
        job.result_json = {**job.result_json, 'status': 'denied'}


@router.post('/tool-invocations/{invocation_id}/decision')
async def decision(invocation_id: str, body: ToolDecisionReq, session: DB, user: Actor):
    if getattr(user, 'deleted', False) or getattr(user, 'status', 'active') != 'active':
        raise BizError(BizCode.PERM_DENIED, '审批身份已停用')
    invocation = await session.get(ToolInvocation, invocation_id, with_for_update=True)
    if not invocation:
        raise BizError(BizCode.NOT_FOUND, '工具调用不存在')
    if not await can_inspect(session, user, invocation):
        raise BizError(BizCode.PERM_DENIED, '无权审批此调用')
    tool, version = await get_version(session, (await session.get(ToolVersion, invocation.tool_version_id)).tool_id, invocation.tool_version_id)
    if body.decision == 'approved' and tool.status != 'active':
        raise BizError(BizCode.LOCKED, '工具已停用')
    if version.approval == 'administrator_only' and not is_admin(user):
        raise BizError(BizCode.PERM_DENIED, '此工具要求管理员审批')
    if invocation.status != 'awaiting_approval' or invocation.approval_status != 'pending':
        raise BizError(BizCode.DUPLICATE_OPERATION, '该调用已决定或已执行')
    await AuditService(session).record(actor=user.name, action='tool:decision', target=invocation.id, after={'decision': body.decision})
    invocation.approval_status, invocation.decided_by = body.decision, user.id
    invocation.status = 'queued' if body.decision == 'approved' else 'denied'
    if body.decision == 'rejected':
        invocation.finished_at = now_iso()
    if invocation.source == 'automation':
        await resume_automation_invocation(session, invocation, body.decision)
        await session.commit()
        return ok(invocation_brief(invocation))
    if invocation.run_id:
        run = await session.get(ExpertRun, invocation.run_id, with_for_update=True)
        job = (await session.execute(select(ExpertJob).where(ExpertJob.run_id == invocation.run_id,
                                 ExpertJob.generation == invocation.generation).with_for_update())).scalar_one_or_none()
        if not run or run.execution_generation != invocation.generation or not job or job.status != 'waiting':
            raise BizError(BizCode.LOCKED, '运行已变更或未处于等待审批状态')
        job.status, job.claim_token, job.lease_until, job.finished_at = 'queued', '', None, ''
        job.completion = {**(job.completion or {}), 'toolInvocationId': invocation.id, 'resume': {'toolInvocationId': invocation.id, 'decision': body.decision}}
        run.status, run.error, run.finished_at = 'queued', '', ''
        await session.commit()
        return ok(invocation_brief(invocation))
    await session.commit()
    if body.decision == 'approved':
        actor = await session.get(User, invocation.requested_by)
        task = await session.get(TaskItem, invocation.task_id) if invocation.task_id else None
        await execute_tool(session, user=actor, tool_version_id=version.id, arguments=invocation.input_json,
                           task=task, idempotency_key=invocation.idempotency_key, source=invocation.source)
        await session.refresh(invocation)
        if invocation.status == 'succeeded' and invocation.source == 'test' and version.status != 'published':
            version.status, version.tested_at = 'tested', now_iso()
            await session.commit()
    return ok(invocation_brief(invocation))
