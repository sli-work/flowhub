"""PostgreSQL transactional jobs, short leases and fenced result commits."""
import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update

from flowhub_api.db.session import SessionFactory
from flowhub_api.models.expert import ExpertJob, ExpertRun, ExpertVersion
from flowhub_api.models import TaskItem, User, WorkflowInstance

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20


class LeaseLost(RuntimeError):
    """This execution no longer owns the right to publish results."""


@dataclass(frozen=True)
class Claim:
    job_id: str
    run_id: str
    generation: int
    token: str
    attempts: int


async def claim_next() -> Claim | None:
    async with SessionFactory.begin() as session:
        job = (await session.execute(select(ExpertJob).where(or_(
            ExpertJob.status == "queued",
            and_(ExpertJob.status == "running", ExpertJob.lease_until < func.now()),
        )).order_by(ExpertJob.created_at, ExpertJob.id).with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.claim_token = uuid4().hex
        job.lease_until = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
        job.attempts += 1
        return Claim(job.id, job.run_id, job.generation, job.claim_token, job.attempts)


def owned(claim: Claim):
    return and_(ExpertJob.id == claim.job_id, ExpertJob.claim_token == claim.token,
                ExpertJob.status == "running", ExpertJob.lease_until > func.now())


async def check_lease(claim: Claim) -> None:
    async with SessionFactory() as session:
        if (await session.execute(select(ExpertJob.id).where(owned(claim)))).scalar_one_or_none() is None:
            raise LeaseLost("Expert job lease expired or reassigned")


async def heartbeat(claim: Claim, runner: asyncio.Task) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            async with SessionFactory.begin() as session:
                result = await session.execute(update(ExpertJob).where(owned(claim)).values(
                    lease_until=datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)))
                if result.rowcount != 1:
                    runner.cancel()
                    return
        except Exception:
            logger.exception("Expert heartbeat failed job=%s", claim.job_id)
            runner.cancel()
            return


async def complete_workflow(session, run: ExpertRun, completion: dict) -> None:
    """Caller holds the job and task locks until the completion commit."""
    if not run.task_id or run.status == "interrupted":
        return
    task = (await session.execute(select(TaskItem).where(TaskItem.id == run.task_id).with_for_update())).scalar_one_or_none()
    if task is None:
        return
    task.expert_pending = False
    if not completion.get("automatic") or task.status != "pending_confirmation":
        return
    if run.status == "succeeded" and int(completion.get("auto_depth", 0)) < 5:
        from flowhub_api.services.workflow import WorkflowService
        from flowhub_api.core.response import BizError
        actor = await session.get(User, run.requested_by)
        if actor is not None and not actor.deleted:
            try:
                # Roll back partial adoption on validation failure, not the completed run.
                async with session.begin_nested():
                    service = WorkflowService(session)
                    project, template = await service.resolve_template_for_task(task)
                    instance = (await session.execute(select(WorkflowInstance).where(WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
                    cfg = await service._node_cfg_of(template, task.node_id, instance.version if instance else None) if template else {}
                    if project and template and (cfg or {}).get("handler") == "Expert 自动":
                        result = await service.ai_autosubmit(task, project, template, cfg, run, actor,
                                                            auto_depth=int(completion.get("auto_depth", 0)) + 1)
                        if not result.get("blocked"):
                            return
            except BizError:
                logger.info("Expert automatic completion requires human review run=%s", run.id)
    # A blocked automatic result must be visible to a human assignee.
    task = await session.get(TaskItem, run.task_id)
    if task is not None and task.status == "pending_confirmation":
        task.status = "assigned"


async def execute_claim(claim: Claim) -> None:
    from flowhub_api.services.expert_runtime import add_event, execute_run

    async def publish_progress(event: str, payload: dict) -> None:
        """Persist graph progress in a separate short transaction for task polling."""
        if event == "token":
            return
        if event == "draft_start":
            kind, status = "model", "running"
            title = str(payload.get("summary") or "正在调用模型生成草稿")
        else:
            kind = str(payload.get("kind") or "runtime")
            status = str(payload.get("status") or "running")
            title = str(payload.get("tool") or payload.get("summary") or "Expert 正在执行")
        details = {key: value for key, value in payload.items() if key not in {"kind", "status", "tool", "durationMs"}}
        async with SessionFactory.begin() as event_session:
            current = await event_session.get(ExpertRun, claim.run_id)
            if current is None or current.execution_generation != claim.generation:
                return
            await add_event(event_session, claim.run_id, 1, kind, status, title, details,
                            duration_ms=int(payload.get("durationMs") or 0))
    async with SessionFactory() as session:
        run = await session.get(ExpertRun, claim.run_id)
        if run is None or run.execution_generation != claim.generation:
            raise LeaseLost("Run execution generation changed")
        version = await session.get(ExpertVersion, run.expert_version_id)
        actor = await session.get(User, run.requested_by)
        if version is None or actor is None or actor.deleted:
            run.status, run.error = "failed", "运行版本或发起人已不可用"
        elif not run.config_snapshot:
            run.status, run.error = "failed", "历史运行缺少可靠配置快照，请人工重新生成"
        elif run.status not in {"succeeded", "failed", "interrupted"}:
            run.status = "running"
            await publish_progress("trace", {"kind": "queue", "tool": "Worker 已领取任务", "status": "running", "summary": "正在准备运行环境"})
            await execute_run(session, run, version, actor,
                              emitter=publish_progress, lease_guard=lambda: check_lease(claim), resume_checkpoint=claim.attempts > 1)
        # Lock only during final database writes. A stale worker cannot commit.
        with session.no_autoflush:
            job = (await session.execute(select(ExpertJob).where(owned(claim)).with_for_update())).scalar_one_or_none()
        if job is None:
            await session.rollback()
            raise LeaseLost("Lost lease before result commit")
        await session.flush()
        await complete_workflow(session, run, job.completion or {})
        job.status = "waiting" if run.status == "interrupted" else "completed"
        job.finished_at = datetime.now(UTC).isoformat()
        job.lease_until = None
        await session.commit()


async def cleanup_one_object() -> None:
    from flowhub_api.models.expert import ExpertObjectCleanup
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings
    client = get_minio()
    if client is None:
        return
    async with SessionFactory.begin() as session:
        cleanup = (await session.execute(select(ExpertObjectCleanup).order_by(ExpertObjectCleanup.created_at)
                   .with_for_update(skip_locked=True).limit(1))).scalar_one_or_none()
        if cleanup is None:
            return
        await asyncio.to_thread(client.remove_object, get_settings().minio_bucket, cleanup.object_name)
        await session.delete(cleanup)


async def worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            claim = await claim_next()
            if claim is None:
                await cleanup_one_object()
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)
                continue
            runner = asyncio.create_task(execute_claim(claim))
            pulse = asyncio.create_task(heartbeat(claim, runner))
            try:
                await runner
            except (LeaseLost, asyncio.CancelledError):
                if stop.is_set():
                    raise
                logger.warning("Expert execution lost lease job=%s", claim.job_id)
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Expert worker failed; expired lease will allow recovery")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=1)


async def start_workers() -> tuple[asyncio.Event, list[asyncio.Task]]:
    stop = asyncio.Event()
    return stop, [asyncio.create_task(worker(stop)) for _ in range(2)]


async def stop_workers(state) -> None:
    stop, workers = state
    stop.set()
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
