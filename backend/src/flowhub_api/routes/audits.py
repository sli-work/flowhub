"""审计路由（docs/06 §三）：分页查询 / 导出（导出本身记审计）。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import ok
from flowhub_api.db.session import get_db
from flowhub_api.models import AuditRow, User
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])


def _brief(a: AuditRow) -> dict:
    return {
        "time": a.time, "actor": a.actor, "actorType": a.actor_type,
        "authorized": a.authorized, "action": a.action, "target": a.target,
        "result": a.result, "reqId": a.req_id, "ip": a.ip,
    }


@router.get("")
async def list_audits(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    action: str = "", result: str = "", actor_type: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(5, ge=1, le=100),
):
    auth = build_authorizer(user)
    auth.require("audit:read")
    stmt = select(AuditRow)
    if action:
        stmt = stmt.where(AuditRow.action.contains(action))
    if result:
        stmt = stmt.where(AuditRow.result == result)
    if actor_type:
        stmt = stmt.where(AuditRow.actor_type == actor_type)
    total = len((await session.execute(stmt)).scalars().all())
    rows = (await session.execute(stmt.order_by(AuditRow.time.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return ok({"items": [_brief(a) for a in rows], "total": total, "page": page, "page_size": page_size})


@router.post("/export")
async def export_audits(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """导出审计：导出本身记录一条新审计（docs/06 §3.3）。"""
    auth = build_authorizer(user)
    auth.require("audit:export")
    await AuditService(session).record(
        actor=user.name, action="audit:export", target="审计导出", result="success",
    )
    await session.commit()
    return ok(message="导出将记录一次新的审计事件")
