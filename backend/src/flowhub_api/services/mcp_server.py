"""FlowHub MCP Server（外部客户端接入）。

- FastMCP 同时提供旧 SSE 和 Streamable HTTP：前者保持既有客户端兼容，后者供
  Zed 等远程 MCP 客户端使用。
- 认证：纯 ASGI 中间件解析 `Authorization: Bearer <access_key>` → get_current_user_by_key
  → ContextVar 注入 User（FastAPI `Depends` 在 mounted app 内不可达；不用 BaseHTTPMiddleware
  避免缓冲破坏 SSE 长连接）。
- 数据可见边界（方案 A：已执行链全量）：当前+前序节点全量表单（build_task_context 复用）；
  后序已生成任务仅只读摘要；权限一律 can_read_task（管理员角色 or assignee==自己）。
"""
import contextvars
import json
from contextlib import asynccontextmanager
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from flowhub_api.db.session import SessionFactory
from flowhub_api.models import DocItem, TaskItem, User, WorkItem, WorkflowIssue
from flowhub_api.services.time import now_iso

_current_user: contextvars.ContextVar[User] = contextvars.ContextVar("flowhub_mcp_user")
_streamable_http_asgi_app: "_AuthASGI | None" = None

_ADMIN_ROLES = ("system_admin", "organization_admin")

_INSTRUCTIONS = """FlowHub 流程协同平台外部接入服务。

权限边界（重要）：
- 数据访问与任务提交均受权限控制：任务处理人（assignee）或系统/组织管理员；
- 已执行链（当前节点 + 之前节点）可获取完整表单数据（get_task_context）；
- 后续节点（尚未执行）只提供只读摘要（状态/处理人/截止，不含表单明细）。

常用工作流：
1. list_my_tasks —— 查看我当前有哪些任务；
2. get_task <task_id> —— 查看任务详情、待填表单 Schema 与验收标准；
3. get_task_context <task_id> —— 获取完整上下文（含前序节点与文档）；
4. create_document —— 把 Markdown 产出保存为文档（upload 字段用）；
5. submit_task <task_id> —— 填充表单并提交，自动流转到下一节点。
6. create_work_item —— 在有创建权限的项目中启动一个新的工作项流程。
"""

mcp = FastMCP(
    "FlowHub",
    instructions=_INSTRUCTIONS,
    # FastMCP 默认 host=127.0.0.1 会自动启用 DNS rebinding 保护，allowed_hosts 仅放行
    # 127.0.0.1/localhost；部署经 nginx 反向代理时 Host 为实际访问地址（如
    # 192.168.21.195:8088），会被中间件以 421 "Invalid Host header" 拒绝。
    # FlowHub 已有 nginx 层 + access key Bearer 认证，显式关闭该额外防护。
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    # 挂载到 /api/v1/mcp/http 后以根路径提供 Streamable HTTP，最终地址为
    # /api/v1/mcp/http/；避免与兼容保留的 /api/v1/mcp/sse 发生路由冲突。
    streamable_http_path="/",
)


# ---------- 工具（全部以 access key → User 认证，权限复用 can_read_task） ----------

def _is_admin(user: User) -> bool:
    return any(r.id in _ADMIN_ROLES for r in user.roles)


async def _task_summary(session: AsyncSession, t: TaskItem) -> dict:
    """任务摘要同时输出旧主处理人与节点完整处理人，避免多人绑定被单值字段遮蔽。"""
    from flowhub_api.services.workflow import WorkflowService

    recipients = await WorkflowService(session).resolve_task_recipients(t)
    return {
        "id": t.id, "title": t.title, "project": t.project, "node": t.node,
        "node_id": t.node_id, "status": t.status, "assignee": t.assignee,
        "assignees": [{"id": user.id, "name": user.name, "account": user.account, "dept": user.dept} for user in recipients],
        "due": t.due, "priority": t.priority, "expertPending": t.expert_pending,
    }


async def _can_read_task(session: AsyncSession, user: User, task: TaskItem) -> bool:
    from flowhub_api.services.agent_context import can_read_task
    return await can_read_task(session, user, task)


@mcp.tool()
async def list_my_tasks(status: str = "") -> list[dict]:
    """列出当前用户可见的任务（含所在多人节点的共同待办）。assignees 返回节点完整处理人；assignee 是兼容旧客户端的主处理人。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        stmt = select(TaskItem)
        if status:
            stmt = stmt.where(TaskItem.status == status)
        rows = (await session.execute(stmt.order_by(TaskItem.id.desc()).limit(100))).scalars().all()
        visible = rows if _is_admin(user) else [task for task in rows if await _can_read_task(session, user, task)]
        return [await _task_summary(session, task) for task in visible]


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """获取任务详情：当前节点表单值、表单 Schema（待填字段）、验收标准与已有勾选；无权限返回错误。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        if not await _can_read_task(session, user, task):
            return {"error": "无权限读取该任务（仅任务处理人或系统/组织管理员可读）"}
        wi = await session.get(WorkItem, task.wi_id)
        from flowhub_api.services.workflow import WorkflowService

        svc = WorkflowService(session)
        project, tpl = await svc.resolve_template_for_task(task)
        cfg: dict = {}
        if project is not None and tpl is not None:
            cfg = await svc._node_cfg_of(tpl, task.node_id) or {}
        from flowhub_api.services.task_issues import TaskIssueService
        issue_service = TaskIssueService(session)
        return {
            **await _task_summary(session, task),
            "formValues": task.form_values or {},
            "formSchema": cfg.get("schema") or [],
            "acceptance": (cfg.get("deliverable") or {}).get("acceptance") or [],
            "acceptanceChecks": task.acceptance_checks or {},
            "workItem": {"id": wi.id if wi else "", "title": wi.title if wi else "", "status": wi.status if wi else ""},
            "issues": [issue_service.brief(issue) for issue in await issue_service.issues_for_task(task)],
            "issueSummary": await issue_service.summary(task.wi_id, task.id),
        }


@mcp.tool()
async def create_document(name: str, content: str, wi_id: str = "") -> dict:
    """把 Markdown/文本内容保存为 FlowHub 文档（upload 类型表单字段的产出载体），返回 {id, name} 引用。
    将该引用数组放入 submit_task 的 form_values 对应字段即可完成附件填充。"""
    user = _current_user.get()
    from flowhub_api.services.expert_runtime import create_document_from_text

    async with SessionFactory() as session:
        wi = await session.get(WorkItem, wi_id) if wi_id else None
        if wi_id and wi is None:
            return {"error": f"工作项 {wi_id} 不存在"}
        ref = await create_document_from_text(
            session, wi_id=wi.id if wi else None, project=wi.project if wi else "未归档",
            name=name, content=content, uploader=user,
        )
        await session.commit()
        return dict(ref)


@mcp.tool()
async def create_work_item(
    project_id: str,
    template_id: str,
    start_values: dict,
    priority: Literal["P0", "P1", "P2", "P3"] | None = None,
    labels: list[str] | None = None,
) -> dict:
    """创建并启动工作项流程（会产生真实写入）。

    调用者必须具有 workflow_instance:create 权限，并且是起始节点处理人或管理员。
    project_id 和 template_id 必须是已启用项目中的有效模板绑定；start_values 必须至少含
    title，且应按起始节点的表单 schema 填写。priority 可选为 P0、P1、P2、P3；
    未传入时兼容从 start_values.priority 读取。失败时不会创建任何工作项。
    """
    user = _current_user.get()
    from flowhub_api.routes.notifications import publish_notification
    from flowhub_api.services.work_item_creation import create_work_item as create_work_item_service

    async with SessionFactory() as session:
        try:
            result = await create_work_item_service(
                session, user=user, project_id=project_id, template_id=template_id,
                start_values=start_values or {}, priority=priority, labels=labels or [],
            )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None) or str(exc)
            return {"error": f"创建失败：{detail}", "code": getattr(exc, "biz_code", 0)}

        for notification in result.get("notifications") or []:
            await publish_notification(notification)
        wi = result["item"]
        next_node = result.get("next_node") or {}
        next_tasks = result.get("tasks") or ([result["next_task"]] if result.get("next_task") else [])
        return {
            "created": True,
            "workItem": {
                "id": wi.id, "title": wi.title, "type": wi.type, "project": wi.project,
                "status": wi.status, "priority": wi.priority, "labels": wi.labels,
            },
            "instance": {"id": result["instance"].id, "currentNode": next_node},
            "startTask": await _task_summary(session, result["start_task"]),
            "nextTasks": [await _task_summary(session, task) for task in next_tasks],
            "closed": bool(result.get("closed")),
        }


@mcp.tool()
async def claim_task(task_id: str) -> dict:
    """认领任务（转办/待认领的任务：status assigned/transferred → accepted），仅任务处理人或管理员。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        if not await _can_read_task(session, user, task):
            return {"error": "无权限认领该任务（仅任务处理人或系统/组织管理员）"}
        if task.status not in ("assigned", "transferred"):
            return {"error": f"任务状态为 {task.status}，无需认领"}
        task.status = "accepted"
        task.assignee = user.name
        from flowhub_api.services.audit import AuditService

        await AuditService(session).record(actor=user.name, action="task:claim", target=f"{task.id} · {task.node}", result="success")
        await session.commit()
        return {"claimed": True, "task": await _task_summary(session, task)}


@mcp.tool()
async def submit_task(task_id: str, form_values: dict, acceptance_checks: dict | None = None) -> dict:
    """提交任务并流转到下一节点（仅任务处理人或系统/组织管理员）。

    - form_values：按节点 formSchema 的 key 组织；upload/file 字段传 create_document 返回的 [{id, name}] 引用数组；
      image 字段只能填人工通过图片上传组件创建的 [{id, name}] 引用，Expert 不生成图片内容；
    - acceptance_checks：节点配置了验收标准（acceptance）时必须逐项 {key: {"text": ..., "checked": true}}；
    - 校验失败/无权限返回 {"error": ...}；成功返回下一节点与处理人信息。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        if not await _can_read_task(session, user, task):
            return {"error": "无权限提交该任务（仅任务处理人或系统/组织管理员）"}
        if task.status in ("completed", "cancelled"):
            return {"error": "任务已处理，不可重复提交（幂等保护）"}
        if task.status == "pending_confirmation":
            return {"error": "Expert 正在自动处理该节点，完成后会自动流转"}
        try:
            if (task.source or "").startswith("issue:") and ":handling:" in (task.source or ""):
                from flowhub_api.services.task_issues import TaskIssueService
                from flowhub_api.routes.notifications import publish_notification

                issue, verification_task, notifications = await TaskIssueService(session).complete_handling(
                    task, form_values or {}, acceptance_checks or {}, user,
                )
                await session.commit()
                for notification in notifications:
                    await publish_notification(notification)
                return {"submitted": True, "task": await _task_summary(session, task), "issue": TaskIssueService.brief(issue),
                        "nextTaskId": verification_task.id, "nextNode": {"id": verification_task.node_id, "label": verification_task.node}}
            if (task.source or "").startswith("issue:") and ":verify:" in (task.source or ""):
                return {"error": "问题验证请调用 verify_task_issue"}
            from flowhub_api.services.workflow import WorkflowService
            from flowhub_api.services.task_issues import TaskIssueService

            await TaskIssueService(session).assert_origin_can_advance(task)
            result = await WorkflowService(session).submit_and_advance(task, form_values or {}, acceptance_checks or {}, user)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None) or str(exc)
            return {"error": f"提交失败：{detail}", "code": getattr(exc, "biz_code", 0)}
        return {
            "submitted": True,
            "task": await _task_summary(session, task),
            "nextNode": result.get("next_node"),
            "nextAssignees": result.get("next_assignees") or [],
            "nextTasks": [await _task_summary(session, task) for task in (result.get("tasks") or [])],
            "waitingJoin": bool(result.get("waiting_join")),
            "closed": bool(result.get("closed")),
        }


@mcp.tool()
async def create_task_issue(task_id: str, target_task_id: str, title: str, description: str,
                            priority: Literal["P0", "P1", "P2", "P3"] = "P2", blocking: bool | None = None) -> dict:
    """从任意当前任务创建局部返工问题，投递到本工作项已完成的前置任务。

    问题处理不会推动主流程；blocking=true 时，原任务在验证通过前不能提交。
    """
    user = _current_user.get()
    from flowhub_api.routes.notifications import publish_notification
    from flowhub_api.services.task_issues import TaskIssueService

    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return {"error": "任务不存在"}
        service = TaskIssueService(session)
        try:
            issue, notifications = await service.create(task, target_task_id=target_task_id, title=title,
                description=description, priority=priority, blocking=blocking, actor=user)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            return {"error": getattr(exc, "detail", None) or str(exc)}
        for notification in notifications:
            await publish_notification(notification)
        return {"created": True, "issue": service.brief(issue)}


@mcp.tool()
async def list_task_issues(task_id: str) -> list[dict]:
    """查询与当前任务关联的问题及阻断汇总。"""
    user = _current_user.get()
    from flowhub_api.services.task_issues import TaskIssueService

    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None or not await _can_read_task(session, user, task):
            return [{"error": "任务不存在或无权限"}]
        service = TaskIssueService(session)
        return [{"summary": await service.summary(task.wi_id, task.id), "items": [service.brief(issue) for issue in await service.issues_for_task(task)]}]


@mcp.tool()
async def verify_task_issue(issue_id: str, passed: bool, notes: str = "") -> dict:
    """提交问题回归结论。失败会自动创建下一轮前置节点处理任务。"""
    user = _current_user.get()
    from flowhub_api.models import WorkflowIssue
    from flowhub_api.routes.notifications import publish_notification
    from flowhub_api.services.task_issues import TaskIssueService

    async with SessionFactory() as session:
        issue = await session.get(WorkflowIssue, issue_id)
        if issue is None:
            return {"error": "问题不存在"}
        service = TaskIssueService(session)
        try:
            next_task, notifications = await service.verify(issue, passed, notes, user)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            return {"error": getattr(exc, "detail", None) or str(exc)}
        for notification in notifications:
            await publish_notification(notification)
        return {"verified": True, "issue": service.brief(issue), "nextTaskId": next_task.id if next_task else ""}


@mcp.tool()
async def get_task_context(task_id: str) -> str:
    """获取任务完整上下文（markdown）：工作项信息 + 当前节点 + 前序节点表单明细 + 关联文档；无权限抛错。"""
    user = _current_user.get()
    from flowhub_api.services.agent_context import build_task_context
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return "任务不存在"
        try:
            return await build_task_context(session, user, task, include_docs=True)
        except Exception as exc:  # noqa: BLE001
            return f"无权限或读取失败：{exc}"


@mcp.tool()
async def retrieve_attachment_evidence(task_id: str, query: str, exclude_chunk_seq: str = "", depth: int = 1) -> str:
    """按需检索任务附件证据片段（只读二次检索）。task_id: 任务 ID；query: 检索问题；exclude_chunk_seq: 已引用片段序号，逗号分隔；depth: 递归深度 1-3。返回带 [附件@文档:位置:片段号] 标注的证据片段。"""
    user = _current_user.get()
    from flowhub_api.services.attachment_evidence import retrieve_more_evidence

    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return "任务不存在"
        try:
            exclude = {int(part) for part in exclude_chunk_seq.split(",") if part.strip().isdigit()}
            result = await retrieve_more_evidence(session, task, user, query, exclude, depth)
        except Exception as exc:  # noqa: BLE001
            return f"检索失败：{exc}"
        if not result.injected:
            return "未检索到更多附件证据片段。"
        return "\n".join(
            f"[{c.seq}] [附件@{c.doc_name}:{c.location}:{c.seq}] {c.text[:300]}"
            for c in result.injected
        )


@mcp.tool()
async def get_work_item(wi_id: str) -> dict:
    """获取工作项信息与全部任务概览（不含表单明细）。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        wi = await session.get(WorkItem, wi_id)
        if wi is None:
            return {"error": f"工作项 {wi_id} 不存在"}
        tasks = (await session.execute(
            select(TaskItem).where(TaskItem.wi_id == wi_id).order_by(TaskItem.id.asc())
        )).scalars().all()
        if not _is_admin(user) and not any([await _can_read_task(session, user, task) for task in tasks]):
            return {"error": "无权限读取该工作项（仅涉及的任务处理人或系统/组织管理员可读）"}
        from flowhub_api.services.task_issues import TaskIssueService
        issue_service = TaskIssueService(session)
        return {
            "id": wi.id, "title": wi.title, "type": wi.type, "project": wi.project,
            "status": wi.status, "priority": wi.priority, "assignee": wi.assignee,
            "creator": wi.creator, "due": wi.due, "labels": wi.labels,
            "startValues": wi.start_values or {},
            "tasks": [await _task_summary(session, task) for task in tasks],
            "issues": [issue_service.brief(issue) for issue in (await session.execute(
                select(WorkflowIssue).where(WorkflowIssue.wi_id == wi_id).order_by(WorkflowIssue.created_at.desc())
            )).scalars().all()],
            "issueSummary": await issue_service.summary(wi_id),
        }


@mcp.tool()
async def list_documents(wi_id: str) -> list[dict]:
    """列出工作项关联文档元数据（不含正文）。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        wi = await session.get(WorkItem, wi_id)
        if wi is None:
            return [{"error": f"工作项 {wi_id} 不存在"}]
        tasks = (await session.execute(
            select(TaskItem).where(TaskItem.wi_id == wi_id)
        )).scalars().all()
        if not _is_admin(user) and not any([await _can_read_task(session, user, task) for task in tasks]):
            return [{"error": "无权限读取该工作项文档"}]
        docs = (await session.execute(
            select(DocItem).where(DocItem.wi == wi_id, DocItem.deleted == False)  # noqa: E712
        )).scalars().all()
        return [{
            "id": d.id, "name": d.name, "version": d.version, "level": d.level,
            "uploader": d.uploader, "size": d.size, "time": d.time, "kind": d.kind,
        } for d in docs]


@mcp.tool()
async def get_downstream_summary(task_id: str) -> list[dict]:
    """获取后续节点任务只读摘要（节点/状态/处理人/截止，不含表单明细）。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return [{"error": f"任务 {task_id} 不存在"}]
        if not await _can_read_task(session, user, task):
            return [{"error": "无权限读取该任务（仅任务处理人或系统/组织管理员可读）"}]
        rows = (await session.execute(
            select(TaskItem).where(
                TaskItem.wi_id == task.wi_id, TaskItem.id > task.id,
            ).order_by(TaskItem.id.asc())
        )).scalars().all()
        return [await _task_summary(session, task) for task in rows]


# ---------- ASGI 认证中间件 + Starlette app ----------

class _AuthASGI:
    """纯 ASGI 认证：Bearer access key → User（ContextVar）。不用 BaseHTTPMiddleware（避免缓冲破坏 SSE）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from flowhub_api.authz.api_key import get_current_user_by_key
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        try:
            async with SessionFactory() as session:
                user = await get_current_user_by_key(session, auth)
        except Exception:  # noqa: BLE001
            body = json.dumps({"code": 40101, "message": "access key 无效或缺失"}).encode()
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            ]})
            await send({"type": "http.response.body", "body": body})
            return
        token = _current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_user.reset(token)


def mcp_starlette_app():
    """返回带认证中间件的 Starlette app（SSE 端点：{origin}/api/v1/mcp/sse）。"""
    return _AuthASGI(mcp.sse_app())


def mcp_streamable_http_app():
    """返回带认证的 Streamable HTTP app（供 Zed：{origin}/api/v1/mcp/http/）。"""
    global _streamable_http_asgi_app
    if _streamable_http_asgi_app is None:
        _streamable_http_asgi_app = _AuthASGI(mcp.streamable_http_app())
    return _streamable_http_asgi_app


@asynccontextmanager
async def mcp_streamable_http_lifespan():
    """把 mounted Streamable HTTP 子应用的生命周期纳入主 FastAPI 应用。"""
    app = mcp_streamable_http_app().app
    async with app.router.lifespan_context(app):
        yield
