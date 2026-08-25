"""全局搜索路由（docs/02 §十三）：工作项 / 任务 / 文档。"""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.core.response import ok
from flowhub_api.db.session import get_db
from flowhub_api.models import DocItem, TaskItem, User, WorkItem

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("")
async def search(
    q: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    kw = f"%{q}%"
    items: list[dict] = []
    wis = (await session.execute(select(WorkItem).where(WorkItem.title.like(kw)).limit(5))).scalars().all()
    for w in wis:
        items.append({"type": "workitem", "title": f"{w.id} · {w.title}", "desc": f"工作项 · {w.project}", "to": "workitem"})
    tasks = (await session.execute(select(TaskItem).where(TaskItem.title.like(kw)).limit(5))).scalars().all()
    for t in tasks:
        items.append({"type": "task", "title": f"{t.id} · {t.title}", "desc": f"任务 · 处理人 {t.assignee}", "to": "tasks"})
    docs = (await session.execute(select(DocItem).where(DocItem.name.like(kw)).limit(5))).scalars().all()
    for d in docs:
        items.append({"type": "document", "title": d.name, "desc": f"文档 · {d.project}", "to": "docs"})
    return ok({"items": items})
