"""看板路由（docs/02 §十二）：基于持久化业务数据的聚合概览。"""
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import ok
from flowhub_api.db.session import get_db
from flowhub_api.models import AuditRow, TaskItem, User, WorkItem, WorkflowInstance

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def _audit_day(value: str, *, year: int) -> date | None:
    """解析审计记录的 ``MM-DD HH:MM[:SS]`` 日期，脏数据不计入趋势。"""
    try:
        return datetime.strptime(f"{year}-{value[:5]}", "%Y-%m-%d").date()
    except ValueError:
        return None


@router.get("/overview")
async def overview(
    session: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    auth = build_authorizer(user)
    auth.require("dashboard:read")
    running = (await session.execute(select(WorkflowInstance).where(WorkflowInstance.state == "running"))).scalars().all().__len__()
    closed = (await session.execute(select(WorkItem).where(WorkItem.status == "closed"))).scalars().all().__len__()
    overdue = (await session.execute(select(TaskItem).where(TaskItem.overdue == True))).scalars().all().__len__()  # noqa: E712
    agent_pending = (await session.execute(select(TaskItem).where(TaskItem.agent_pending == True))).scalars().all().__len__()  # noqa: E712

    type_rows = (await session.execute(
        select(WorkItem.type, func.count()).group_by(WorkItem.type)
    )).all()
    type_split = [
        {"name": {"requirement": "需求流程", "issue": "问题流程", "change": "其他"}.get(t, t), "value": c}
        for t, c in type_rows
    ]
    # 超时 Top（按 overdue 任务）
    timeout_tasks = (await session.execute(
        select(TaskItem).where(TaskItem.overdue == True).limit(5)  # noqa: E712
    )).scalars().all()
    timeout_top = [{"name": f"[{t.project}] {t.wi_id} · {t.node}", "v": "超时"} for t in timeout_tasks]

    # 部门负载：按用户部门聚合进行中任务
    users = (await session.execute(select(User))).scalars().all()
    tasks_all = (await session.execute(
        select(TaskItem).where(TaskItem.status.notin_(["completed", "cancelled"]))
    )).scalars().all()
    dept_of = {u.name: (u.dept.split(" / ")[0] if u.dept else "未分配") for u in users}
    dept_load_map: dict[str, int] = {}
    for t in tasks_all:
        d = dept_of.get(t.assignee, "未分配")
        dept_load_map[d] = dept_load_map.get(d, 0) + 1
    dept_load = [{"name": k, "v": v} for k, v in sorted(dept_load_map.items(), key=lambda x: -x[1])]

    # 节点热度：按任务节点聚合
    node_heat_map: dict[str, int] = {}
    for t in tasks_all:
        node_heat_map[t.node] = node_heat_map.get(t.node, 0) + 1
    node_heat = [{"name": k, "v": v} for k, v in sorted(node_heat_map.items(), key=lambda x: -x[1])[:5]]

    # 近 7 日活动：审计记录是所有工作流转/操作的持久化事实来源，
    # 不再以 due 日期或固定数组伪造趋势。审计时间尚未包含年份，按当前 UTC 年解析。
    today = datetime.now(UTC).date()
    window_start = today - timedelta(days=6)
    previous_start = window_start - timedelta(days=7)
    audit_rows = (await session.execute(select(AuditRow.time))).scalars().all()
    daily_counts: Counter[date] = Counter()
    previous_count = 0
    for time in audit_rows:
        day = _audit_day(time, year=today.year)
        if day is None:
            continue
        if window_start <= day <= today:
            daily_counts[day] += 1
        elif previous_start <= day < window_start:
            previous_count += 1
    weekly = [
        {"d": (window_start + timedelta(days=offset)).strftime("%m-%d"),
         "v": daily_counts[window_start + timedelta(days=offset)]}
        for offset in range(7)
    ]

    return ok({
        "kpis": [
            {"label": "运行中流程", "value": running},
            {"label": "已关闭工作项", "value": closed},
            {"label": "超时任务", "value": overdue},
            {"label": "Agent 待确认", "value": agent_pending},
        ],
        "type_split": type_split,
        "timeout_top": timeout_top,
        "weekly": weekly,
        "weekly_label": "近 7 日活动",
        "weekly_total": sum(day["v"] for day in weekly),
        "weekly_previous_total": previous_count,
        "dept_load": dept_load,
        "node_heat": node_heat,
    })
