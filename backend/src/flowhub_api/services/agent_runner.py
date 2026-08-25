"""Agent 执行引擎：opencode 封装 + invoke 分流 + 工作流后台触发（docs/05 §4）。

- `run_opencode`：asyncio 子进程调 `opencode run --model {model} --format json {prompt}`，
  超时 kill，JSON 解析容错（解析失败回退 stdout 原文）。
- `invoke_agent`：按 capability.mode 分流（direct 执行 / confirm 建确认请求 / forbid 拒绝），
  落 AgentInvocation、更新 Agent KPI、写审计（actor=agent, authorized=调用用户）。
- `trigger_node_invocation`：工作流「Agent 自动」后台入口，独立会话不复用请求 session。
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.core.response import BizCode, BizError
from flowhub_api.db.session import SessionFactory
from flowhub_api.models import (
    Agent, AgentCapability, AgentConfirmRequest, AgentInvocation, AgentSuggestion, AgentTool, TaskItem, User,
)
from flowhub_api.services.audit import AuditService

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "180"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_short() -> str:
    return datetime.now().strftime("%m-%d %H:%M")


def fmt_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass
class AgentRunResult:
    ok: bool
    text: str = ""
    raw: dict | None = None
    elapsed_ms: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {"ok": self.ok, "text": self.text, "raw": self.raw, "elapsed_ms": self.elapsed_ms, "error": self.error}


async def run_opencode(model: str, prompt: str, *, timeout: int = DEFAULT_TIMEOUT) -> AgentRunResult:
    """调用 opencode CLI 非交互执行。model 形如 `provider/model`（如 `deepseek/deepseek-chat`）；
    为空时省略 `--model`，使用 opencode 默认模型（本地登录态/配置）。opencode 模型名大小写敏感，统一规范化。
    """
    cmd = ["opencode", "run"]
    if model:
        if "/" in model:
            provider, name = model.split("/", 1)
            model = f"{provider.strip().lower()}/{name.strip()}"
        cmd += ["--model", model]
    cmd += ["--format", "json", prompt]
    started = datetime.now()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return AgentRunResult(ok=False, elapsed_ms=(datetime.now() - started).total_seconds() * 1000,
                                  error=f"opencode 调用超时（>{timeout}s）")
    except FileNotFoundError:
        return AgentRunResult(ok=False, error="opencode 未安装或不在 PATH")
    except Exception as exc:  # noqa: BLE001
        logger.warning("opencode 进程异常: %s", exc)
        return AgentRunResult(ok=False, error=f"opencode 进程异常: {exc}")

    elapsed = int((datetime.now() - started).total_seconds() * 1000)
    text = (stdout or b"").decode("utf-8", errors="replace")
    err_text = (stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return AgentRunResult(ok=False, elapsed_ms=elapsed, error=err_text.strip()[:200] or f"退出码 {proc.returncode}")

    # 解析 JSON 事件流：opencode --format json 输出 NDJSON（每行一个事件），
    # 最终助手文本在 type="text" 事件的 part.text 字段（可能多段，拼接取全文）；
    # 兼容 message 事件（部分 provider 结构）；非 JSON 回退 stdout 原文。
    raw: dict | None = None
    final_text = ""
    text_parts: list[str] = []
    try:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "text":
                part = ev.get("part") or {}
                piece = part.get("text") if isinstance(part, dict) else None
                if isinstance(piece, str) and piece.strip():
                    text_parts.append(piece)
            elif t == "message" and ev.get("role") == "assistant":
                content = ev.get("content") or ""
                piece = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                if piece.strip():
                    text_parts.append(piece)
        if text_parts:
            final_text = "\n".join(text_parts).strip()
    except json.JSONDecodeError:
        raw = None
        final_text = text.strip()

    if not final_text:
        final_text = text.strip()
    return AgentRunResult(ok=bool(final_text.strip()), text=final_text.strip(), raw=raw, elapsed_ms=elapsed)


async def run_chat_completion(
    base_url: str, api_key: str, model: str, system_prompt: str, user_prompt: str,
    *, timeout: int = 120,
) -> AgentRunResult:
    """API 直连引擎：调用任意 OpenAI 兼容 Chat Completions 端点（engine=api）。

    `base_url` 为含版本路径的 endpoint（如 https://api.deepseek.com/v1），
    自动拼接 /chat/completions；`model` 为纯模型名。错误信息不含 api_key 与请求体。
    """
    from datetime import datetime as _dt

    if not base_url or not api_key or not model:
        return AgentRunResult(ok=False, error="API 直连缺少配置：base_url / api_key / model 不能为空")
    try:
        import httpx
    except ImportError:
        return AgentRunResult(ok=False, error="后端缺少 httpx 依赖，无法直连模型厂商")

    started = _dt.now()
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException:
        return AgentRunResult(ok=False, error=f"模型调用超时（>{timeout}s）：{url.split('/')[2]}")
    except httpx.HTTPError as e:
        return AgentRunResult(ok=False, error=f"模型调用失败：{type(e).__name__}")
    elapsed = int((_dt.now() - started).total_seconds() * 1000)

    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        return AgentRunResult(ok=False, elapsed_ms=elapsed, error=f"HTTP {resp.status_code}" + (f"：{str(msg)[:160]}" if msg else ""))

    choices = data.get("choices") or []
    content = ""
    if choices:
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
    if not content:
        return AgentRunResult(ok=False, elapsed_ms=elapsed, error="模型返回空内容（choices[0].message.content 为空）")
    return AgentRunResult(ok=True, text=str(content).strip(), raw=data, elapsed_ms=elapsed)


def _cap_mode_of(agent: Agent, capability: str) -> str:
    """能力模式：节点级 caps 覆盖在 invoke 入口由调用方传入；此处查 Agent 自身能力表。"""
    for c in agent.capabilities:
        if c.name == capability:
            return c.mode
    return "forbid"


async def _record_invocation(
    session: AsyncSession, agent: Agent, task: TaskItem | None, capability: str, mode: str,
    prompt: str, result: dict | None, status: str, authorized_user: str, *, elapsed_ms: int = 0,
    error: str = "", action: str = "", node_id: str = "", audit_id: str | None = None,
) -> AgentInvocation:
    inv = AgentInvocation(
        id=f"inv{uuid4().hex[:8]}", agent_id=agent.id,
        task_id=task.id if task else None, wi_id=task.wi_id if task else None,
        node_id=node_id or (task.node_id if task else ""),
        capability=capability, mode=mode, action=action, prompt=prompt,
        result=result, status=status, elapsed_ms=elapsed_ms,
        authorized_user=authorized_user, error=error,
        created_at=now_short(), finished_at=now_short() if status in ("success", "failed", "cancelled") else "",
        audit_id=audit_id,
    )
    session.add(inv)
    await session.flush()
    return inv


async def _build_prompt(
    session: AsyncSession, agent: Agent, capability: str, action: str, user_prompt: str,
    task: TaskItem | None, node_cfg: dict | None, user: User,
) -> str:
    """构造发送给 opencode 的 prompt：Agent 角色 + 权限校验后的任务/历史节点/文档上下文。

    有 task 时按当前用户权限注入上下文（无权 → 403，由调用方统一处理）；
    无 task（全局调用）则只带节点目的与用户指令。
    """
    parts = [f"你是 FlowHub 流程协同平台中的 AI Agent「{agent.name}」({agent.desc or '未填写描述'})。"]
    parts.append(f"本次执行能力：{capability}（动作：{action or '通用'}）。")
    if task:
        from flowhub_api.services.agent_context import build_task_context

        ctx = await build_task_context(session, user, task)
        parts.append(f"\n===== 任务上下文（按调用用户 {user.name} 权限可见） =====\n{ctx}")
    if node_cfg:
        purpose = (node_cfg.get("purpose") or "")
        if purpose:
            parts.append(f"\n节点目的：{purpose}")
    if user_prompt:
        parts.append(f"\n用户/系统指令：{user_prompt}")
    parts.append("\n请基于上述上下文直接输出最终结果文本，不要输出过程性对话。")
    return "\n".join(parts)


async def invoke_agent(
    session: AsyncSession, agent: Agent, capability: str, prompt: str,
    user: User, *, action: str = "", task: TaskItem | None = None,
    node_id: str = "", node_cfg: dict | None = None, op_scope: str = "",
    expire_minutes: int = 120, node_caps: dict | None = None,
    provider_override: str = "", model_override: str = "",
) -> dict:
    """按能力 mode 分流执行。返回 {invocation, confirm_request?, suggestion?, mode}。

    - direct：同步调 opencode → suggestion（status=suggestion）
    - confirm：同步调 opencode 产出草稿 → 建 AgentConfirmRequest（待人工批准）
    - forbid：抛 403（Agent 不可触碰该能力）
    node_caps 为节点级能力覆盖（{capability: mode}），优先于 Agent 自身能力表。
    """
    caps = node_caps or {}
    mode = caps.get(capability) or _cap_mode_of(agent, capability)
    if mode == "forbid":
        await AuditService(session).record(
            actor=agent.name, actor_type="agent", authorized=user.name,
            action=f"agent:{capability}", target=task.title if task else (agent.name),
            result="denied", failure_reason=f"能力 {capability} 对 Agent 为 forbid",
        )
        raise BizError(BizCode.FORBIDDEN, f"Agent 能力边界：{capability} 为禁止操作（forbid）")

    # 权限预检：Agent 读取任务上下文必须遵循调用用户的数据权限（docs/03）
    if task is not None:
        from flowhub_api.services.agent_context import can_read_task

        if not can_read_task(user, task):
            await AuditService(session).record(
                actor=agent.name, actor_type="agent", authorized=user.name,
                action=f"agent:{capability}", target=f"{task.id} · {task.title}",
                result="denied", failure_reason="调用用户无权读取该任务上下文",
            )
            raise BizError(BizCode.PERM_DENIED, "无权限读取该任务上下文（仅任务处理人或管理员可见）", http_status=403)

    final_prompt = await _build_prompt(session, agent, capability, action, prompt, task, node_cfg, user)

    # 执行分流（三层）：
    # 1) 绑定客户端工具（tool_id）→ 工具配置执行（api 直连 / opencode 本地）
    # 2) 旧数据无工具但 engine=api → Agent 自身 api_key/base_url 直连
    # 3) 其余 → opencode 本地（system_prompt 拼 prompt 头部，单段）
    from flowhub_api.services.crypto import decrypt_secret
    override_tool = None
    if provider_override:
        candidates = (await session.execute(
            select(AgentTool).where(AgentTool.provider == provider_override, AgentTool.status == "active")
        )).scalars().all()
        override_tool = next((tool for tool in candidates if model_override in (tool.models or [])), None)
        if override_tool is None:
            raise BizError(BizCode.VALIDATION, "任务调用指定的 Provider 或模型未配置")

    if override_tool:
        if override_tool.engine == "api":
            run = await run_chat_completion(
                override_tool.base_url, decrypt_secret(override_tool.api_key), model_override,
                system_prompt=agent.system_prompt or agent.desc, user_prompt=final_prompt,
            )
        else:
            if agent.system_prompt:
                final_prompt = f"{agent.system_prompt}\n\n{final_prompt}"
            run = await run_opencode(f"{override_tool.provider}/{model_override}", final_prompt)
    elif agent.tool_id:
        tool = await session.get(AgentTool, agent.tool_id)
        if tool and tool.status == "active":
            # Agent 级模型优先（创建时可选模型覆盖工具默认模型）
            eff_model = agent.model or tool.model
            if tool.engine == "api":
                run = await run_chat_completion(
                    tool.base_url, decrypt_secret(tool.api_key), eff_model,
                    system_prompt=agent.system_prompt or agent.desc,   # Agent 系统提示词（类型带出）
                    user_prompt=final_prompt,
                )
            else:
                model = f"{tool.provider.lower()}/{eff_model}" if tool.provider and eff_model else (eff_model or "")
                if agent.system_prompt:
                    final_prompt = f"{agent.system_prompt}\n\n{final_prompt}"
                run = await run_opencode(model, final_prompt)
        else:
            raise BizError(BizCode.NOT_FOUND, "Agent 绑定的客户端工具不存在或未启用")
    elif agent.engine == "api":
        api_key = decrypt_secret(agent.api_key)
        run = await run_chat_completion(
            agent.base_url, api_key, agent.model or "",
            system_prompt=agent.system_prompt or agent.desc,   # 旧数据 desc 兜底
            user_prompt=final_prompt,
        )
    else:
        if agent.system_prompt:
            final_prompt = f"{agent.system_prompt}\n\n{final_prompt}"
        run = await run_opencode(agent.model or "", final_prompt)

    # 审计：agent 执行（authorized=调用用户）
    audit = await AuditService(session).record(
        actor=agent.name, actor_type="agent", authorized=user.name,
        action=f"agent:{capability}", target=task.title if task else (agent.name),
        result="success" if run.ok else "failed",
        failure_reason=run.error if not run.ok else None,
        after={"mode": mode, "elapsed_ms": run.elapsed_ms},
    )
    await session.flush()

    result_payload = {"text": run.text, "raw": run.raw, "elapsed_ms": run.elapsed_ms} if run.ok else None

    if mode == "confirm":
        # 触发式预创建（Agent 自动节点）：确认请求已在 advance 时创建，此处只回填执行结果
        if task is not None:
            existing_req = (await session.execute(
                select(AgentConfirmRequest).where(
                    AgentConfirmRequest.task_id == task.id,
                    AgentConfirmRequest.status == "pending",
                )
            )).scalar_one_or_none()
            if existing_req is not None:
                existing_req.result = result_payload
                inv = await session.get(AgentInvocation, existing_req.invocation_id) if existing_req.invocation_id else None
                if inv is not None:
                    inv.result = result_payload
                    inv.status = "draft"
                    inv.error = run.error
                    inv.elapsed_ms = run.elapsed_ms
                sg = (await session.execute(
                    select(AgentSuggestion).where(AgentSuggestion.invocation_id == existing_req.invocation_id)
                )).scalar_one_or_none()
                if sg is not None:
                    sg.body = run.text if run.ok else (run.error or "Agent 执行失败")
                    sg.status = "suggestion"
                    sg.data = result_payload
                return {"invocation": inv, "confirm_request": existing_req, "suggestion": sg, "mode": mode}
        inv = await _record_invocation(
            session, agent, task, capability, mode, final_prompt, result_payload, "draft",
            user.name, elapsed_ms=run.elapsed_ms, error=run.error, action=action,
            node_id=node_id, audit_id=audit.id if audit else None,
        )
        # 确认请求（带过期）
        expire_at = fmt_iso(datetime.now(timezone.utc) + timedelta(minutes=expire_minutes))
        req = AgentConfirmRequest(
            id=f"cf{uuid4().hex[:8]}", agent_id=agent.id, invocation_id=inv.id,
            task_id=task.id if task else None, wi_id=task.wi_id if task else None,
            node_id=node_id or (task.node_id if task else ""),
            action=action or capability, capability=capability,
            op_scope=op_scope or capability, authorized_user=user.name,
            status="pending", prompt=final_prompt, result=result_payload,
            expire_at=expire_at, created_at=now_short(),
        )
        session.add(req)
        await session.flush()
        suggestion = AgentSuggestion(
            id=f"sg{uuid4().hex[:8]}", agent_id=agent.id, invocation_id=inv.id,
            task_id=task.id if task else None, wi_id=task.wi_id if task else None,
            node_id=node_id or (task.node_id if task else ""),
            title=action or f"{capability} 产出", body=run.text if run.ok else (run.error or "无产出"),
            status="suggestion", time=now_short(), data=result_payload,
        )
        session.add(suggestion)
        await session.flush()
        return {"invocation": inv, "confirm_request": req, "suggestion": suggestion, "mode": mode}

    # direct：执行成功 → 建议（未自动生效）
    inv = await _record_invocation(
        session, agent, task, capability, mode, final_prompt, result_payload,
        "success" if run.ok else "failed", user.name, elapsed_ms=run.elapsed_ms,
        error=run.error, action=action, node_id=node_id, audit_id=audit.id if audit else None,
    )
    suggestion = AgentSuggestion(
        id=f"sg{uuid4().hex[:8]}", agent_id=agent.id, invocation_id=inv.id,
        task_id=task.id if task else None, wi_id=task.wi_id if task else None,
        node_id=node_id or (task.node_id if task else ""),
        title=action or f"{capability} 产出", body=run.text if run.ok else (run.error or "无产出"),
        status="suggestion" if run.ok else "rejected", time=now_short(), data=result_payload,
    )
    session.add(suggestion)
    await session.flush()

    # 更新 Agent KPI
    agent.calls += 1
    agent.avg_ms = round((agent.avg_ms * (agent.calls - 1) + run.elapsed_ms) / agent.calls) if agent.calls else run.elapsed_ms
    if run.ok:
        agent.success_rate = round((agent.success_rate * (agent.calls - 1) + 1) / agent.calls * 100, 1) if agent.calls else 100.0
    agent.updated = now_short()

    return {"invocation": inv, "suggestion": suggestion, "mode": mode}


async def trigger_node_invocation(
    agent_id: str, task_id: str, node_cfg: dict, user_name: str,
    session: AsyncSession | None = None,
) -> dict | None:
    """工作流「Agent 自动/协助」入口：同步预创建确认请求（立即可见、可批准），
    后台异步执行 Agent 调用并填充结果（不阻塞流转响应）。

    session 传入时复用调用方会话（确认请求与任务同一事务落库）。
    """
    own = session is None
    try:
        if own:
            async with SessionFactory() as session:
                req = await _precreate_confirm(session, agent_id, task_id, node_cfg, user_name)
        else:
            req = await _precreate_confirm(session, agent_id, task_id, node_cfg, user_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent 预创建确认请求失败 task=%s agent=%s: %s", task_id, agent_id, exc)
        return None
    if req is not None:
        asyncio.create_task(_run_invoke(agent_id, task_id, node_cfg, user_name))
    return req


async def _precreate_confirm(
    session: AsyncSession, agent_id: str, task_id: str, node_cfg: dict, user_name: str,
) -> dict | None:
    """同步创建确认请求骨架（result 暂空，后台执行后填充）。防重：已有 pending 请求则复用。"""
    from flowhub_api.models import AgentInvocation, AgentSuggestion

    agent = await session.get(Agent, agent_id)
    task = await session.get(TaskItem, task_id)
    if agent is None or task is None or agent.status != "active":
        return None
    existing = (await session.execute(
        select(AgentConfirmRequest).where(
            AgentConfirmRequest.task_id == task_id, AgentConfirmRequest.status == "pending",
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    action = f"节点「{node_cfg.get('label', '')}」自动产出"
    inv = AgentInvocation(
        id=f"inv{uuid4().hex[:8]}", agent_id=agent.id,
        task_id=task.id, wi_id=task.wi_id, node_id=node_cfg.get("id", ""),
        capability="generate_content", mode="confirm", action=action, prompt="（Agent 执行中…）",
        result=None, status="draft", authorized_user=user_name,
        created_at=now_short(),
    )
    req = AgentConfirmRequest(
        id=f"cf{uuid4().hex[:8]}", agent_id=agent.id, invocation_id=inv.id,
        task_id=task.id, wi_id=task.wi_id, node_id=node_cfg.get("id", ""),
        action=action, capability="generate_content", op_scope="generate_content",
        authorized_user=user_name, status="pending",
        prompt="Agent 自动节点产出，等待人工批准后生效", result=None,
        expire_at=fmt_iso(datetime.now(timezone.utc) + timedelta(minutes=120)),
        created_at=now_short(),
    )
    sg = AgentSuggestion(
        id=f"sg{uuid4().hex[:8]}", agent_id=agent.id, invocation_id=inv.id,
        task_id=task.id, wi_id=task.wi_id, node_id=node_cfg.get("id", ""),
        title=action, body="Agent 执行中…", status="suggestion", time=now_short(),
    )
    session.add_all([inv, req, sg])
    await session.commit()
    return req


async def _run_invoke(agent_id: str, task_id: str, node_cfg: dict, user_name: str) -> None:
    """后台执行 Agent 调用（独立会话），结果回填到已预创建的确认请求/建议。"""
    try:
        async with SessionFactory() as session:
            agent = await session.get(Agent, agent_id)
            task = await session.get(TaskItem, task_id)
            if agent is None or task is None:
                return
            user = User(id="system", name=user_name, account=user_name)
            caps = (node_cfg or {}).get("agent") or {}
            await invoke_agent(
                session, agent, "generate_content", "根据当前节点上下文生成建议/产出物。",
                user, action=f"节点「{node_cfg.get('label', '')}」自动产出",
                task=task, node_id=node_cfg.get("id", ""), node_cfg=node_cfg,
                node_caps=caps.get("caps"),
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent 后台执行失败 task=%s agent=%s: %s", task_id, agent_id, exc)
