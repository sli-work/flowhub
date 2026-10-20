"""Scoped automation job inspection, retry and explicit acceptance backfill."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import ExpertDeployment, ExpertRun, TaskItem, User, WorkflowInstance
from flowhub_api.models.tool import ToolAutomationJob
from flowhub_api.services.agent_context import can_read_completed_task
from flowhub_api.services.audit import AuditService
from flowhub_api.services.document_archive import authorize_archive
from flowhub_api.services.tool_automation import schedule_automation
from flowhub_api.services.workflow import WorkflowService

router = APIRouter(prefix="/api/v1", tags=["tool-automation"])


def job_brief(job):
    return {"id": job.id, "taskId": job.task_id, "runId": job.run_id,
            "event": (job.rule_json or {}).get("event"), "status": job.status,
            "attempts": job.attempts, "error": job.error, "result": job.result_json,
            "createdAt": job.created_at, "finishedAt": job.finished_at}


async def authorize_action(session, task, user):
    try:
        await authorize_archive(session, task, user)
    except ValueError as exc:
        raise BizError(BizCode.PERM_DENIED, str(exc), http_status=403) from None


@router.get("/tool-automation-jobs")
async def list_jobs(session: Annotated[AsyncSession, Depends(get_db)],
                    user: Annotated[User, Depends(get_current_user)],
                    task_id: str = "", page_size: int = Query(50, ge=1, le=100)):
    stmt = select(ToolAutomationJob).order_by(ToolAutomationJob.created_at.desc())
    if task_id:
        stmt = stmt.where(ToolAutomationJob.task_id == task_id)
    jobs = (await session.execute(stmt.limit(page_size))).scalars().all()
    visible = []
    for job in jobs:
        task = await session.get(TaskItem, job.task_id)
        if task is not None and await can_read_completed_task(session, user, task):
            visible.append(job_brief(job))
    return ok({"items": visible})


@router.post("/tool-automation-jobs/{job_id}/retry")
async def retry_job(job_id: str, session: Annotated[AsyncSession, Depends(get_db)],
                    user: Annotated[User, Depends(get_current_user)]):
    job = (await session.execute(select(ToolAutomationJob).where(
        ToolAutomationJob.id == job_id).with_for_update())).scalar_one_or_none()
    if job is None:
        raise BizError(BizCode.NOT_FOUND, "自动化任务不存在")
    task = await session.get(TaskItem, job.task_id)
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    await authorize_action(session, task, user)
    if job.status != "failed":
        raise BizError(BizCode.DUPLICATE_OPERATION, "仅失败的自动化任务可以重试")
    # Retain original identity and frozen payload; retry never upgrades authority.
    job.status, job.error, job.finished_at = "queued", "", ""
    job.lease_until = None
    await AuditService(session).record(actor=user.name, action="tool:automation_retry", target=job.id)
    await session.commit()
    return ok({"job": job_brief(job)}, "已安排重试")


class BackfillReq(BaseModel):
    generate_summary: bool = True


@router.post("/tasks/{task_id}/automation/backfill")
async def backfill(task_id: str, body: BackfillReq,
                   session: Annotated[AsyncSession, Depends(get_db)],
                   user: Annotated[User, Depends(get_current_user)]):
    task = (await session.execute(select(TaskItem).where(
        TaskItem.id == task_id).with_for_update())).scalar_one_or_none()
    if task is None:
        raise BizError(BizCode.NOT_FOUND, "任务不存在")
    await authorize_action(session, task, user)
    service = WorkflowService(session)
    project, template = await service.resolve_template_for_task(task)
    instance = (await session.execute(select(WorkflowInstance).where(
        WorkflowInstance.work_item_id == task.wi_id))).scalar_one_or_none()
    cfg = await service._node_cfg_of(template, task.node_id, instance.version) if template and instance else None
    if not project or not cfg or not ((cfg.get("automation") or {}).get("rules")):
        raise BizError(BizCode.VALIDATION, "当前节点未配置自动化规则")
    deployment = await session.get(ExpertDeployment, (cfg.get("expert") or {}).get("expertDeploymentId", ""))
    if deployment is None or deployment.status != "active":
        raise BizError(BizCode.VALIDATION, "当前节点 Expert Deployment 不可用")
    key = f"acceptance-backfill:{task.id}:{instance.version}:{deployment.expert_version_id}"
    try:
        jobs = await schedule_automation(session, task=task, event="node_entered", actor=user,
            expert_version_id=deployment.expert_version_id, backfill_key=key)
    except ValueError as exc:
        raise BizError(BizCode.VALIDATION, str(exc), http_status=400) from None
    run = None
    if body.generate_summary:
        runs = (await session.execute(select(ExpertRun).where(ExpertRun.task_id == task.id)
                                      .order_by(ExpertRun.started_at.desc()))).scalars().all()
        run = next((candidate for candidate in runs if (candidate.config_snapshot or {}).get("automationBackfillKey") == key), None)
        if run is None:
            if any(candidate.status in {"queued", "running", "interrupted"} for candidate in runs):
                raise BizError(BizCode.LOCKED, "当前 Expert 仍在运行，请完成后再补执行")
            from flowhub_api.services.expert_runtime import schedule_deployment_run
            run = await schedule_deployment_run(session, deployment.id,
                await service.build_task_brief(task, template), user, task_id=task.id,
                completion={"automatic": False}, quality_mode="fast", generation_retries=1, unbounded_output=True)
            run.config_snapshot = {**run.config_snapshot, "automationBackfillKey": key}
            task.expert_pending = True
    await AuditService(session).record(actor=user.name, action="tool:automation_backfill", target=task.id,
        after={"jobIds": [job.id for job in jobs], "runId": run.id if run else None})
    await session.commit()
    return ok({"jobs": [job_brief(job) for job in jobs],
               "run": {"id": run.id, "status": run.status} if run else None}, "验收归档补执行已排队")
