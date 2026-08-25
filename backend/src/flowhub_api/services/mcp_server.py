"""FlowHub MCP Server（外部 Agent 接入）。

- FastMCP（SSE 传输），挂载到 FastAPI：`app.mount("/api/v1/mcp", mcp_starlette_app())`
- 认证：纯 ASGI 中间件解析 `Authorization: Bearer <access_key>` → get_current_user_by_key
  → ContextVar 注入 User（FastAPI `Depends` 在 mounted app 内不可达；不用 BaseHTTPMiddleware
  避免缓冲破坏 SSE 长连接）。
- 数据可见边界（方案 A：已执行链全量）：当前+前序节点全量表单（build_task_context 复用）；
  后序已生成任务仅只读摘要；权限一律 can_read_task（管理员角色 or assignee==自己）。
"""
import contextvars
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from mcp.server.fastmcp import FastMCP

from flowhub_api.db.session import SessionFactory
from flowhub_api.models import DocItem, TaskItem, User, WorkItem
from flowhub_api.services.agent_runner import now_iso

_current_user: contextvars.ContextVar[User] = contextvars.ContextVar("flowhub_mcp_user")

_ADMIN_ROLES = ("system_admin", "organization_admin")

_INSTRUCTIONS = """FlowHub 流程协同平台外部接入服务。

权限边界（重要）：
- 所有数据访问均受权限控制：任务处理人（assignee）或系统/组织管理员可读；
- 已执行链（当前节点 + 之前节点）可获取完整表单数据（get_task_context）；
- 后续节点（尚未执行）只提供只读摘要（状态/处理人/截止，不含表单明细）。

常用工作流：
1. list_my_tasks —— 查看我当前有哪些任务；
2. get_task <task_id> —— 查看某任务详情与当前节点表单；
3. get_task_context <task_id> —— 获取完整上下文（含前序节点与文档）；
4. get_downstream_summary <task_id> —— 查看该任务后续节点摘要；
5. get_work_item / list_documents —— 工作项与关联文档概览。
"""

mcp = FastMCP("FlowHub", instructions=_INSTRUCTIONS)


# ---------- 工具（全部以 access key → User 认证，权限复用 can_read_task） ----------

def _is_admin(user: User) -> bool:
    return any(r.id in _ADMIN_ROLES for r in user.roles)


def _task_summary(t: TaskItem) -> dict:
    return {
        "id": t.id, "title": t.title, "project": t.project, "node": t.node,
        "node_id": t.node_id, "status": t.status, "assignee": t.assignee,
        "due": t.due, "priority": t.priority, "agentPending": t.agent_pending,
    }


async def _can_read_task(session: AsyncSession, user: User, task: TaskItem) -> bool:
    from flowhub_api.services.agent_context import can_read_task
    return can_read_task(user, task)


@mcp.tool()
async def list_my_tasks(status: str = "") -> list[dict]:
    """列出当前用户可见的任务（管理员全量；普通用户仅自己负责的）。status 可选：assigned/accepted/in_progress/pending_confirmation/submitted/completed 等。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        stmt = select(TaskItem)
        if not _is_admin(user):
            stmt = stmt.where(TaskItem.assignee == user.name)
        if status:
            stmt = stmt.where(TaskItem.status == status)
        rows = (await session.execute(stmt.order_by(TaskItem.id.desc()).limit(100))).scalars().all()
        return [_task_summary(t) for t in rows]


@mcp.tool()
async def get_task(task_id: str) -> dict:
    """获取任务详情（含当前节点表单 form_values）；无权限返回错误。"""
    user = _current_user.get()
    async with SessionFactory() as session:
        task = await session.get(TaskItem, task_id)
        if task is None:
            return {"error": f"任务 {task_id} 不存在"}
        if not await _can_read_task(session, user, task):
            return {"error": "无权限读取该任务（仅任务处理人或系统/组织管理员可读）"}
        wi = await session.get(WorkItem, task.wi_id)
        return {
            **_task_summary(task),
            "formValues": task.form_values or {},
            "workItem": {"id": wi.id if wi else "", "title": wi.title if wi else "", "status": wi.status if wi else ""},
        }


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
        if not _is_admin(user) and not any(t.assignee == user.name for t in tasks):
            return {"error": "无权限读取该工作项（仅涉及的任务处理人或系统/组织管理员可读）"}
        return {
            "id": wi.id, "title": wi.title, "type": wi.type, "project": wi.project,
            "status": wi.status, "priority": wi.priority, "assignee": wi.assignee,
            "creator": wi.creator, "due": wi.due, "labels": wi.labels,
            "startValues": wi.start_values or {},
            "tasks": [_task_summary(t) for t in tasks],
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
            select(TaskItem).where(TaskItem.wi_id == wi_id).limit(1)
        )).scalars().all()
        if not _is_admin(user) and not any(t.assignee == user.name for t in tasks):
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
        return [_task_summary(t) for t in rows]


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
