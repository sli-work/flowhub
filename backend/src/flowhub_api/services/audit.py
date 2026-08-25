"""审计服务：所有写操作必须写审计（PRD §12 / docs/06）。"""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.models import AuditRow


class AuditService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        actor: str,
        actor_type: str = "user",  # user | agent | system
        authorized: str | None = None,
        action: str,
        target: str,
        result: str = "success",  # success | failed | denied
        ip: str | None = None,
        request_id: str | None = None,
        before: dict | None = None,
        after: dict | None = None,
        failure_reason: str | None = None,
    ) -> AuditRow:
        row = AuditRow(
            id=f"aud_{uuid4().hex[:12]}",
            time=datetime.now(UTC).strftime("%m-%d %H:%M:%S"),
            actor=actor,
            actor_type=actor_type,
            authorized=authorized or "—",
            action=action,
            target=target,
            result=result,
            req_id=request_id or f"req_{uuid4().hex[:6]}",
            ip=ip or "127.0.0.1",
            before=before,
            after=after,
            failure_reason=failure_reason,
        )
        self.session.add(row)
        return row
