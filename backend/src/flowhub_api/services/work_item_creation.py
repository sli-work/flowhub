"""Shared, permissioned work-item creation for HTTP and external agents."""
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer
from flowhub_api.models import DocItem, TagItem, User
from flowhub_api.services.audit import AuditService
from flowhub_api.services.workflow import WorkflowService


def _attachment_ids(values: dict | None) -> set[str]:
    """Extract only stable document references from a start-form payload."""
    ids: set[str] = set()
    for value in (values or {}).values():
        entries: Iterable[object] = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.add(entry["id"])
    return ids


async def create_work_item(
    session: AsyncSession,
    *,
    user: User,
    project_id: str,
    template_id: str,
    start_values: dict,
    labels: list[str] | None = None,
) -> dict:
    """Create and start a work item using the same policy for every entry point.

    The caller is responsible for publishing returned notifications after this
    function commits, so transports do not need to depend on HTTP routes.
    """
    build_authorizer(user).require("workflow_instance:create")
    result = await WorkflowService(session).create_instance(
        project_id, template_id, start_values or {}, user,
    )
    wi = result["item"]
    requested_labels = labels or []
    if requested_labels:
        registered = set((await session.execute(
            select(TagItem.name).where(
                TagItem.name.in_(requested_labels), TagItem.deleted == False,  # noqa: E712
            )
        )).scalars().all())
        wi.labels = [label for label in dict.fromkeys(requested_labels) if label in registered]

    attachment_ids = _attachment_ids(start_values)
    if attachment_ids:
        docs = (await session.execute(
            select(DocItem).where(
                DocItem.id.in_(attachment_ids), DocItem.wi.is_(None),
                DocItem.project == wi.project, DocItem.uploader == user.name,
                DocItem.deleted == False,  # noqa: E712
            )
        )).scalars().all()
        for doc in docs:
            doc.wi = wi.id

    next_node = result.get("next_node") or {}
    await AuditService(session).record(
        actor=user.name,
        action="workflow_instance:create",
        target=f"{wi.id} · {wi.title}（{next_node.get('label', '')}）",
        result="success",
    )
    await session.commit()
    return result
