"""Agent 路由（docs/05）：注册 / 状态 / 能力边界 / 调用 / 确认请求 / 模型与类型配置。"""
from typing import Annotated
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import (
    Agent, AgentCapability, AgentConfirmRequest, AgentInvocation, AgentModel,
    AgentSuggestion, AgentTool, AgentType, TaskItem, User,
)
from flowhub_api.schemas.api import (
    AgentBindReq, AgentInvokeReq, AgentModelReq, AgentRegisterReq, AgentToolReq, AgentUpdateReq,
    AgentTypeReq, ConfirmDecisionReq, OpencodeApplyReq, TestConnectionReq,
)
from flowhub_api.services.agent_runner import now_iso, now_short
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])

# 默认 14 项能力边界（docs/05 §3）
DEFAULT_CAPABILITIES = [
    ("read_context", "direct"), ("read_documents", "direct"), ("read_history", "direct"),
    ("generate_content", "confirm"), ("write_form", "confirm"), ("append_form", "confirm"),
    ("upload_document", "confirm"), ("create_subtask", "confirm"),
    ("submit_task", "forbid"), ("return_task", "forbid"), ("transfer_task", "forbid"),
    ("pause_workflow", "forbid"), ("resume_workflow", "forbid"), ("close_work_item", "forbid"),
]
_CAP_NAMES = {n for n, _ in DEFAULT_CAPABILITIES}


def _brief(a: Agent) -> dict:
    return {
        "id": a.id, "name": a.name, "code": a.code, "desc": a.desc, "status": a.status,
        "scope": a.scope, "bindings": a.bindings, "owner": a.owner,
        "calls": a.calls, "successRate": a.success_rate, "avgMs": a.avg_ms, "updated": a.updated,
        "engine": a.engine, "provider": a.provider, "model": a.model, "agentType": a.agent_type,
        "toolId": a.tool_id,
        "baseUrl": a.base_url,
        "systemPrompt": (a.system_prompt or "")[:120],
        # 注意：api_key 永不回显（加密落库，仅执行时解密）
        "capabilities": [{"name": c.name, "mode": c.mode} for c in a.capabilities],
    }


def _detail(a: Agent) -> dict:
    data = _brief(a)
    data["systemPrompt"] = a.system_prompt or ""
    return data


def _provider_models(tool: AgentTool) -> list[str]:
    configured = [str(model) for model in (tool.models or []) if str(model).strip()]
    if configured or tool.engine != "opencode" or not tool.provider:
        return configured
    try:
        from flowhub_api.services.opencode_config import list_models
        return [item["model"] for item in list_models() if item["provider"] == tool.provider]
    except Exception:
        return configured


async def _resolve_provider(
    session: AsyncSession, provider: str, model: str = "",
) -> AgentTool | None:
    if not provider:
        return None
    tools = (await session.execute(
        select(AgentTool).where(AgentTool.provider == provider, AgentTool.status == "active")
    )).scalars().all()
    if not tools:
        return None
    if not model:
        return tools[0]
    return next((tool for tool in tools if model in _provider_models(tool)), None)


def _invocation_brief(inv: AgentInvocation) -> dict:
    return {
        "id": inv.id, "agentId": inv.agent_id, "taskId": inv.task_id, "wiId": inv.wi_id,
        "nodeId": inv.node_id, "capability": inv.capability, "mode": inv.mode,
        "action": inv.action, "prompt": inv.prompt, "result": inv.result,
        "status": inv.status, "elapsedMs": inv.elapsed_ms, "authorizedUser": inv.authorized_user,
        "error": inv.error, "createdAt": inv.created_at, "finishedAt": inv.finished_at,
    }


def _confirm_brief(req: AgentConfirmRequest, agent_name: str = "") -> dict:
    return {
        "id": req.id, "agentId": req.agent_id, "agentName": agent_name or req.agent_id,
        "taskId": req.task_id, "nodeId": req.node_id,
        "action": req.action, "capability": req.capability, "opScope": req.op_scope,
        "authorizedUser": req.authorized_user, "status": req.status,
        "prompt": req.prompt, "result": req.result,
        "expireAt": req.expire_at, "createdAt": req.created_at, "decidedAt": req.decided_at,
        "decisionNote": req.decision_note,
    }


def _suggestion_brief(sg: AgentSuggestion) -> dict:
    return {
        "id": sg.id, "agentId": sg.agent_id, "taskId": sg.task_id, "nodeId": sg.node_id,
        "title": sg.title, "body": sg.body, "status": sg.status, "time": sg.time,
        "appliedAt": sg.applied_at, "data": sg.data,
    }


@router.get("")
async def list_agents(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    agents = (await session.execute(select(Agent))).scalars().all()
    active = sum(1 for a in agents if a.status == "active")
    return ok({
        "items": [_brief(a) for a in agents],
        "kpi": {"total": len(agents), "active": active,
                "monthlyCalls": sum(a.calls for a in agents),
                "avgMs": round(sum(a.avg_ms for a in agents) / len(agents)) if agents else 0,
                "pendingApprove": sum(1 for a in agents if a.status == "pending")},
    })


@router.get("/capabilities")
async def capabilities():
    return ok({"items": [{"name": n, "mode": m} for n, m in DEFAULT_CAPABILITIES]})


@router.post("/register")
async def register_agent(
    body: AgentRegisterReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:register")
    code = f"agent_{uuid4().hex[:8]}"
    secret = f"sk_{uuid4().hex}{uuid4().hex}"  # 一次性返回

    # 执行配置解析：优先客户端工具（tool_id → agent_tools 快照）；无 tool_id 走旧链路字段
    tool = None
    configured_tool = None
    if body.tool_id:
        tool = await session.get(AgentTool, body.tool_id)
        if tool is None or tool.status != "active":
            raise BizError(BizCode.NOT_FOUND, "客户端工具不存在或未启用，请先在「Agent 客户端」中配置")
        engine = tool.engine
        provider = tool.provider
        # Agent 级模型优先；统一存储纯模型名，执行时由 provider 负责拼接。
        if body.model:
            model_stored = body.model.split("/")[-1]
        else:
            model_stored = tool.model
    else:
        if not body.provider and body.agent_type:
            agent_type = (await session.execute(
                select(AgentType).where(AgentType.code == body.agent_type, AgentType.status == "active")
            )).scalar_one_or_none()
            if agent_type:
                body.provider = agent_type.provider
                body.model = body.model or agent_type.model
        configured_tool = await _resolve_provider(session, body.provider, body.model) if body.provider else None
        if body.provider and configured_tool is None:
            raise BizError(BizCode.VALIDATION, "Provider 或模型未配置，请先在 Provider 管理中配置")
        engine = configured_tool.engine if configured_tool else (body.engine or "opencode")
        provider = configured_tool.provider if configured_tool else body.provider
        if engine == "api" and configured_tool is None:
            if not body.api_key:
                raise BizError(BizCode.VALIDATION, "API Key 必填（api 引擎）")
            if not body.base_url:
                raise BizError(BizCode.VALIDATION, "Base URL 必填（api 引擎）")
            model_stored = body.model or (configured_tool.model if configured_tool else "")
        else:
            model_stored = body.model or (configured_tool.model if configured_tool else "")

    from flowhub_api.services.crypto import encrypt_secret
    system_prompt = body.system_prompt or ""
    agent = Agent(
        id=f"a{uuid4().hex[:6]}", name=body.name, code=code,
        desc=(system_prompt or body.desc or "")[:255],   # desc 为系统提示词截断（列表/审计展示）
        status="pending", scope=body.scope, bindings="待绑定", owner=user.name,
        updated="刚刚",
        engine=engine, provider=provider, model=model_stored,
        agent_type=body.agent_type or "",
        tool_id=body.tool_id or "",
        system_prompt=system_prompt,
        base_url=(tool.base_url if tool else (configured_tool.base_url if configured_tool else body.base_url)) or "",
        api_key=encrypt_secret(tool.api_key if tool else (configured_tool.api_key if configured_tool else body.api_key)) if (tool.api_key if tool else (configured_tool.api_key if configured_tool else body.api_key)) else "",
        secret_hash=bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode("utf-8"),
    )
    caps = body.capabilities or [n for n, _ in DEFAULT_CAPABILITIES]
    for name, mode in DEFAULT_CAPABILITIES:
        agent.capabilities.append(AgentCapability(
            id=f"cap{uuid4().hex[:6]}", agent_id=agent.id, name=name,
            mode=mode if name in caps else "forbid",
        ))
    session.add(agent)
    await AuditService(session).record(
        actor=user.name, action="agent:register", target=f"{agent.name}（{code}）", result="success",
    )
    await session.commit()
    return ok({"agent": _brief(agent), "secret": secret}, "创建成功：密钥仅此一次展示，请立即保存")


@router.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentUpdateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:register")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    provider = await _resolve_provider(session, body.provider, body.model)
    if provider is None:
        raise BizError(BizCode.VALIDATION, "Provider 或模型未配置，请先在 Provider 管理中配置")
    agent.name = body.name
    agent.agent_type = body.agent_type
    agent.engine = provider.engine
    agent.provider = provider.provider
    agent.model = body.model
    agent.tool_id = provider.id
    agent.base_url = provider.base_url
    agent.system_prompt = body.system_prompt
    agent.desc = body.system_prompt[:255]
    agent.updated = "刚刚"
    await AuditService(session).record(
        actor=user.name, action="agent:update", target=f"{agent.name}（{agent.code}）", result="success",
    )
    await session.commit()
    return ok({"agent": _brief(agent)}, "Agent 已更新")


@router.post("/{agent_id}/bind")
async def bind_agent(
    agent_id: str,
    body: AgentBindReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:bind")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    if body.capabilities:
        unknown = set(body.capabilities) - _CAP_NAMES
        if unknown:
            raise BizError(BizCode.VALIDATION, f"未知能力：{', '.join(sorted(unknown))}")
    agent.scope = body.scope or agent.scope
    agent.bindings = body.bindings or agent.bindings
    if body.capabilities:
        for cap in agent.capabilities:
            if cap.name in body.capabilities:
                cap.mode = body.capabilities[cap.name]
    await AuditService(session).record(
        actor=user.name, action="agent:bind", target=f"{agent.name} · {body.bindings}", result="success",
    )
    await session.commit()
    return ok({"agent": _brief(agent)}, "Agent 已绑定")


@router.post("/{agent_id}/status")
async def change_status(
    agent_id: str,
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    action = payload.get("action")  # suspend | activate | revoke
    auth = build_authorizer(user)
    auth.require("agent:revoke")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    new_status = {"suspend": "suspended", "activate": "active", "revoke": "revoked"}.get(action)
    if new_status is None:
        raise BizError(BizCode.VALIDATION, "未知动作")
    agent.status = new_status
    await AuditService(session).record(
        actor=user.name, action=f"agent:{action}", target=agent.name, result="success",
    )
    await session.commit()
    return ok({"agent": _brief(agent)}, f"Agent 已{ {'suspend': '挂起', 'activate': '激活', 'revoke': '吊销'}[action] }")


@router.post("/{agent_id}/invoke")
async def invoke(
    agent_id: str,
    body: AgentInvokeReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """按能力 mode 分流：direct 执行 / confirm 建确认请求 / forbid 拒绝（docs/05 §4.1）。"""
    auth = build_authorizer(user)
    auth.require("agent:invoke")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    if agent.status != "active":
        await AuditService(session).record(
            actor=agent.name, actor_type="agent", authorized=user.name,
            action=f"agent:{body.capability}", target=agent.name, result="denied",
            failure_reason=f"Agent 状态 {agent.status} 不可调用",
        )
        await session.commit()
        raise BizError(BizCode.FORBIDDEN, f"Agent 当前状态 {agent.status}，不可调用")
    task = None
    if body.task_id:
        task = await session.get(TaskItem, body.task_id)
    from flowhub_api.services.agent_runner import invoke_agent

    try:
        payload = await invoke_agent(
            session, agent, body.capability, body.prompt, user,
            action=body.action, task=task, node_id=body.node_id or (task.node_id if task else ""),
            op_scope=body.op_scope, expire_minutes=body.expire_minutes,
            provider_override=body.provider, model_override=body.model,
        )
    except BizError:
        # 权限/能力边界拒绝：审计（denied）必须先提交，避免回滚丢痕
        await session.commit()
        raise
    await session.commit()
    resp = {
        "invocation": _invocation_brief(payload["invocation"]),
        "suggestion": _suggestion_brief(payload["suggestion"]),
        "mode": payload["mode"],
    }
    if payload.get("confirm_request"):
        resp["confirm_request"] = _confirm_brief(payload["confirm_request"], agent.name)
        return ok(resp, "Agent 已产出，等待人工确认")
    return ok(resp, "Agent 执行完成")


@router.get("/{agent_id}/invocations")
async def list_invocations(
    agent_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status: str = "",
    page: int = 1,
    page_size: int = 20,
):
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    stmt = select(AgentInvocation).where(AgentInvocation.agent_id == agent_id)
    if status:
        stmt = stmt.where(AgentInvocation.status == status)
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.order_by(AgentInvocation.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return ok({"items": [_invocation_brief(r) for r in rows], "total": total})


@router.get("/confirm-requests")
async def list_confirm_requests(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    status: str = "pending",
    page: int = 1,
    page_size: int = 20,
):
    """待确认列表。普通用户：authorized_user==自己；管理员全量。过期自动置 expired。"""
    auth = build_authorizer(user)
    is_admin = any(r.id in ("system_admin", "organization_admin") for r in user.roles)
    stmt = select(AgentConfirmRequest)
    if not is_admin:
        stmt = stmt.where(AgentConfirmRequest.authorized_user == user.name)
    if status and status != "all":
        stmt = stmt.where(AgentConfirmRequest.status == status)
    rows = (await session.execute(stmt.order_by(AgentConfirmRequest.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    # 过期标记（pending 且已过 expire_at → expired）
    now = now_iso()
    agent_names: dict[str, str] = {}
    agent_ids = {r.agent_id for r in rows}
    if agent_ids:
        ags = (await session.execute(select(Agent).where(Agent.id.in_(agent_ids)))).scalars().all()
        agent_names = {a.id: a.name for a in ags}
    out = []
    for r in rows:
        if r.status == "pending" and r.expire_at and now > r.expire_at:
            r.status = "expired"
        out.append(_confirm_brief(r, agent_names.get(r.agent_id, "")))
    await session.commit()
    return ok({"items": out, "total": len(out)})


async def _decide_confirm(
    req_id: str, decision: str, note: str, user: User, session: AsyncSession,
) -> dict:
    """approve / reject 共用逻辑：过期/幂等校验 + 状态迁移 + 审计。"""
    req = await session.get(AgentConfirmRequest, req_id)
    if req is None:
        raise BizError(BizCode.NOT_FOUND, "确认请求不存在")
    auth = build_authorizer(user)
    is_admin = any(r.id in ("system_admin", "organization_admin") for r in user.roles)
    if not is_admin and req.authorized_user != user.name:
        auth.require("agent:authorize")
    if req.status != "pending":
        raise BizError(BizCode.DUPLICATE_OPERATION, f"该确认请求已处理（{req.status}）")
    if req.expire_at and now_iso() > req.expire_at:
        req.status = "expired"
        await session.commit()
        raise BizError(BizCode.DUPLICATE_OPERATION, "确认请求已过期")

    req.status = decision
    req.decided_at = now_short()
    req.decision_note = note
    # 关联 invocation / suggestion / task 状态迁移
    if req.invocation_id:
        inv = await session.get(AgentInvocation, req.invocation_id)
        if inv:
            inv.status = "success" if decision == "approved" else "cancelled"
            inv.finished_at = now_short()
    sg = (await session.execute(
        select(AgentSuggestion).where(AgentSuggestion.invocation_id == req.invocation_id)
    )).scalar_one_or_none()
    if sg:
        sg.status = "applied" if decision == "approved" else "rejected"
        if decision == "approved":
            sg.applied_at = now_short()
    if req.task_id:
        task = await session.get(TaskItem, req.task_id)
        if task:
            task.agent_pending = False
            if task.status == "pending_confirmation":
                task.status = "assigned"
    await AuditService(session).record(
        actor=user.name, action=f"agent:confirm_{decision}", target=f"确认请求 {req.id}",
        result="success", after={"agent": req.agent_id, "capability": req.capability},
    )
    await session.commit()
    return _confirm_brief(req)


@router.post("/confirm-requests/{req_id}/approve")
async def approve_confirm(
    req_id: str,
    body: ConfirmDecisionReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    return ok({"confirm_request": await _decide_confirm(req_id, "approved", body.note, user, session)}, "已批准并生效")


@router.post("/confirm-requests/{req_id}/reject")
async def reject_confirm(
    req_id: str,
    body: ConfirmDecisionReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    return ok({"confirm_request": await _decide_confirm(req_id, "rejected", body.note, user, session)}, "已拒绝")


@router.get("/models")
async def list_models(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """模型列表：静态 seed（API 直连）+ opencode 实际可用模型（baseUrl 空，标记 source=opencode）。"""
    rows = (await session.execute(select(AgentModel).where(AgentModel.status == "active"))).scalars().all()
    items = [{
        "id": m.id, "provider": m.provider, "model": m.model, "label": m.label,
        "desc": m.desc or "", "baseUrl": m.base_url or "", "source": "static",
    } for m in rows]
    # 合并 opencode 实际模型（动态读 ~/.local/share/opencode/auth.json + opencode models）
    try:
        from flowhub_api.services.opencode_config import list_models as _oc_models
        for m in _oc_models():
            items.append({
                "id": f"oc:{m['provider']}/{m['model']}",   # 虚拟 id（无 DB 记录）
                "provider": m["provider"], "model": m["model"],
                "label": f"{m['provider']}/{m['model']}",
                "desc": "opencode 实际可用模型（从 opencode models 动态获取）",
                "baseUrl": "", "source": "opencode",
            })
    except Exception:
        pass
    return ok({"items": items})


@router.get("/tools/opencode/providers")
async def opencode_providers(_: Annotated[User, Depends(get_current_user)]):
    """opencode auth.json 中已配置的 providers 列表（供前端编辑 opencode 工具时选择）。"""
    from flowhub_api.services.opencode_config import list_models, list_providers

    runtime_models: dict[str, list[str]] = {}
    for item in list_models():
        runtime_models.setdefault(item["provider"], []).append(item["model"])
    items = list_providers()
    for item in items:
        if not item["models"]:
            item["models"] = runtime_models.get(item["provider"], [])
    return ok({"items": items})


@router.post("/tools/opencode/apply")
async def opencode_apply(
    body: OpencodeApplyReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """opencode 工具配置：写 auth.json（key）+ opencode.json（baseURL/models）+ upsert agent_tools（name="opencode" 唯一）。"""
    auth = build_authorizer(user)
    auth.require("agent:register")
    from flowhub_api.services.opencode_config import set_provider_config
    set_provider_config(body.provider, body.api_key, kind=body.kind or "api", base_url=body.base_url, models=body.models)
    # upsert agent_tools opencode 记录
    tool = (await session.execute(select(AgentTool).where(AgentTool.name == "opencode"))).scalar_one_or_none()
    if tool is None:
        tool = AgentTool(
            id=f"tool{uuid4().hex[:4]}", name="opencode", engine="opencode",
            provider=body.provider, model="", models=body.models, base_url=body.base_url or "", desc="opencode CLI 引擎（auth.json 已配置 " + body.provider + "）",
            api_key="", status="active", created_at=now_short(),
        )
        session.add(tool)
    else:
        tool.provider = body.provider
        tool.engine = "opencode"
        tool.models = body.models
        tool.base_url = body.base_url or ""
        tool.status = "active"
    await AuditService(session).record(
        actor=user.name, action="agent:register", target=f"opencode 客户端（provider={body.provider}）", result="success",
    )
    await session.commit()
    return ok({"tool": {"id": tool.id, "name": tool.name, "engine": tool.engine, "provider": tool.provider}}, "opencode 客户端已配置（auth.json 已写入）")


@router.post("/test-connection")
async def test_connection(
    body: TestConnectionReq,
    user: Annotated[User, Depends(get_current_user)],
):
    """创建前验证 API Key 可用：以 ping 请求直调厂商，返回延迟/错误（不回显 key）。"""
    auth = build_authorizer(user)
    auth.require("agent:register")
    from flowhub_api.services.agent_runner import run_chat_completion
    run = await run_chat_completion(body.base_url, body.api_key, body.model, "ping", "请回复 pong", timeout=15)
    return ok({"ok": run.ok, "latencyMs": run.elapsed_ms, "error": run.error}, "连接测试完成")


@router.post("/models")
async def create_model(
    body: AgentModelReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:bind")
    dup = await session.execute(
        select(AgentModel).where(AgentModel.provider == body.provider, AgentModel.model == body.model)
    )
    if dup.scalar_one_or_none():
        raise BizError(BizCode.DUPLICATE_OPERATION, "该模型已存在")
    m = AgentModel(
        id=f"am{uuid4().hex[:6]}", provider=body.provider, model=body.model,
        label=body.label or f"{body.provider}/{body.model}", desc=body.desc,
        base_url=body.base_url or "", status="active",
    )
    session.add(m)
    await session.commit()
    return ok({"model": {"id": m.id, "provider": m.provider, "model": m.model, "label": m.label}}, "模型已添加")


@router.get("/types")
async def list_types(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = (await session.execute(select(AgentType).where(AgentType.status == "active"))).scalars().all()
    return ok({"items": [{
        "id": t.id, "code": t.code, "label": t.label, "desc": t.desc,
        "defaultCaps": t.default_caps, "systemPrompt": t.system_prompt or "",
        "provider": t.provider, "model": t.model,
    } for t in rows]})


@router.post("/types")
async def create_type(
    body: AgentTypeReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:bind")
    dup = await session.execute(select(AgentType).where(AgentType.code == body.code))
    if dup.scalar_one_or_none():
        raise BizError(BizCode.DUPLICATE_OPERATION, "该类型已存在")
    if body.provider and await _resolve_provider(session, body.provider, body.model) is None:
        raise BizError(BizCode.VALIDATION, "Provider 或模型未配置，请先在 Provider 管理中配置")
    t = AgentType(id=f"at{uuid4().hex[:6]}", code=body.code, label=body.label, desc=body.desc,
                  default_caps=body.default_caps, system_prompt=body.system_prompt or "",
                  provider=body.provider, model=body.model, status="active")
    session.add(t)
    await session.commit()
    return ok({"type": {"id": t.id, "code": t.code, "label": t.label, "provider": t.provider, "model": t.model}}, "Agent 类型已添加")


@router.get("/tools")
async def list_tools(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """客户端工具配置列表（第一类）。api_key 永不回显。"""
    rows = (await session.execute(select(AgentTool).order_by(AgentTool.created_at))).scalars().all()
    return ok({"items": [{
        "id": t.id, "name": t.name, "engine": t.engine,
        "provider": t.provider, "model": t.model, "models": _provider_models(t), "baseUrl": t.base_url,
        "desc": t.desc, "status": t.status, "createdAt": t.created_at,
    } for t in rows]})


@router.post("/tools")
async def create_tool(
    body: AgentToolReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """新增客户端工具配置（opencode CLI 引擎 / API 直连）。"""
    auth = build_authorizer(user)
    auth.require("agent:register")
    dup = await session.execute(select(AgentTool).where(AgentTool.name == body.name))
    if dup.scalar_one_or_none():
        raise BizError(BizCode.DUPLICATE_OPERATION, "工具名已存在")
    engine = body.engine or "opencode"
    if engine == "api":
        if not body.api_key:
            raise BizError(BizCode.VALIDATION, "API Key 必填（api 引擎）")
        if not body.base_url:
            raise BizError(BizCode.VALIDATION, "Base URL 必填（api 引擎）")
    # opencode 引擎工具全局唯一：已存在 engine=opencode 工具时拒绝再新增
    if engine == "opencode":
        from sqlalchemy import func
        cnt = (await session.execute(
            select(func.count()).select_from(AgentTool).where(AgentTool.engine == "opencode")
        )).scalar()
        if cnt and cnt > 0:
            raise BizError(BizCode.DUPLICATE_OPERATION, "opencode 客户端已配置（全局唯一），请编辑现有 opencode 工具")
    from flowhub_api.services.crypto import encrypt_secret
    tool = AgentTool(
        id=f"tool{uuid4().hex[:4]}", name=body.name, engine=engine,
        provider=body.provider, model="", models=body.models,
        base_url=body.base_url or "", desc=body.desc or "",
        api_key=encrypt_secret(body.api_key) if body.api_key else "",
        status="active", created_at=now_short(),
    )
    session.add(tool)
    await AuditService(session).record(
        actor=user.name, action="agent:register", target=f"工具 {tool.name}", result="success",
    )
    await session.commit()
    return ok({"tool": {"id": tool.id, "name": tool.name, "engine": tool.engine}}, "客户端工具已配置")


@router.put("/tools/{tool_id}")
async def update_tool(
    tool_id: str,
    body: AgentToolReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """编辑客户端工具配置（修改模型厂商配置 / 更新 API Key）。api_key 传空则保留原值。"""
    auth = build_authorizer(user)
    auth.require("agent:register")
    tool = await session.get(AgentTool, tool_id)
    if tool is None:
        raise BizError(BizCode.NOT_FOUND, "客户端工具不存在")
    dup = await session.execute(select(AgentTool).where(AgentTool.name == body.name, AgentTool.id != tool_id))
    if dup.scalar_one_or_none():
        raise BizError(BizCode.DUPLICATE_OPERATION, "工具名已存在")
    engine = body.engine or tool.engine
    if engine == "api":
        if not body.base_url:
            raise BizError(BizCode.VALIDATION, "Base URL 必填（api 引擎）")
        if body.api_key and not body.base_url:
            raise BizError(BizCode.VALIDATION, "Base URL 必填（api 引擎）")
    from flowhub_api.services.crypto import encrypt_secret
    tool.name = body.name or tool.name
    tool.engine = engine
    tool.provider = body.provider or tool.provider
    tool.model = ""
    tool.models = body.models
    tool.base_url = body.base_url
    tool.desc = body.desc if body.desc is not None else tool.desc
    if body.api_key:   # 传新 Key 则重新加密覆盖；空则保留原值
        tool.api_key = encrypt_secret(body.api_key)
    await AuditService(session).record(
        actor=user.name, action="agent:register", target=f"工具 {tool.name}（编辑）", result="success",
    )
    await session.commit()
    return ok({"tool": {"id": tool.id, "name": tool.name, "engine": tool.engine}}, "客户端工具已更新")


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    return ok({"agent": _detail(agent)})


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("agent:revoke")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise BizError(BizCode.NOT_FOUND, "Agent 不存在")
    if agent.status != "revoked":
        raise BizError(BizCode.VALIDATION, "仅已吊销的 Agent 可以删除")
    await AuditService(session).record(
        actor=user.name, action="agent:delete", target=f"{agent.name}（{agent.code}）", result="success",
    )
    await session.delete(agent)
    await session.commit()
    return ok({"id": agent_id}, "已删除吊销的 Agent")
