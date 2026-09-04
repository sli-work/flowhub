"""Compatibility helpers for task split lineage."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import AuditRow


def historical_split_parent_ids(audits: Iterable[dict], child_ids: set[str]) -> dict[str, str]:
    """Recover direct parent links recorded by old ``task:split`` audit entries.

    Earlier releases did not persist ``parent_task_id`` but did record the parent
    in ``target`` and child IDs in ``after.children``.  This deliberately repairs
    only unambiguous direct links; it never guesses relationships from ordering.
    """
    links: dict[str, str] = {}
    for audit in audits:
        target = str(audit.get("target") or "")
        parent_id, separator, _ = target.partition(" · ")
        children = (audit.get("after") or {}).get("children")
        if not separator or not parent_id or not isinstance(children, list):
            continue
        for child_id in children:
            if isinstance(child_id, str) and child_id in child_ids:
                links[child_id] = parent_id
    return links


async def historical_split_parent_ids_for_tasks(session: AsyncSession, task_ids: set[str]) -> dict[str, str]:
    """Return audit-derived parent IDs for legacy tasks without mutating data."""
    if not task_ids:
        return {}
    rows = (await session.execute(
        select(AuditRow.target, AuditRow.after).where(
            AuditRow.action == "task:split", AuditRow.result == "success",
        )
    )).all()
    return historical_split_parent_ids(
        ({"target": target, "after": after} for target, after in rows), task_ids,
    )
