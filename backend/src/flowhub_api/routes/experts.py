"""Expert Runtime REST API: versioned configuration, deployments, runs, and approvals."""
from datetime import UTC, datetime
import asyncio
import json
import logging
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import Expert, ExpertApproval, ExpertChatMessage, ExpertChatSession, ExpertDeployment, ExpertRun, ExpertRunEvent, ExpertSkill, ExpertVersion, LlmProvider, LlmProviderModel, McpServer, McpTool, User
from flowhub_api.schemas.api import ApprovalDecisionReq, DeploymentReq, ExpertChatMessageReq, ExpertChatSessionRenameReq, ExpertChatSessionReq, ExpertCreateReq, ExpertRunReq, ExpertVersionReq, McpServerReq, McpToolUpdateReq, ProviderReq, ProviderTestReq
from flowhub_api.services.audit import AuditService
from flowhub_api.services.crypto import encrypt_secret
from flowhub_api.services.repo_mirror import schedule_mirror_build
from flowhub_api.services.expert_runtime import build_session_history, chat_file_entry, compact_session_async, context_budget_bytes, execute_run, new_id, now_iso, resume_approved_run, run_native_flowhub_chat, save_chat_output_document, session_history_preview, validate_version

router = APIRouter(prefix="/api/v1", tags=["experts"])


def _tool_brief(tool: McpTool) -> dict:
    return {"id": tool.id, "serverId": tool.server_id, "name": tool.name, "description": tool.description, "inputSchema": tool.input_schema, "risk": tool.risk, "approval": tool.approval, "status": tool.status, "enabled": tool.enabled, "updated": tool.updated_at}


def _server_brief(server: McpServer, tools: list[McpTool]) -> dict:
    return {"id": server.id, "name": server.name, "description": server.description, "direction": server.direction, "transport": server.transport, "endpoint": server.endpoint, "authType": server.auth_type, "status": server.status, "health": server.health, "tools": [_tool_brief(tool) for tool in tools], "approvedTools": sum(tool.status == "approved" for tool in tools), "createdAt": server.created_at, "updatedAt": server.updated_at}


@router.get("/mcp-servers")
async def list_mcp_servers(session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)]):
    servers = (await session.execute(select(McpServer).where(McpServer.deleted.is_(False)).order_by(McpServer.updated_at.desc()))).scalars().all()
    tools = (await session.execute(select(McpTool))).scalars().all()
    return ok({"items": [_server_brief(server, [tool for tool in tools if tool.server_id == server.id]) for server in servers]})


@router.post("/mcp-servers")
async def create_mcp_server(body: McpServerReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    now = now_iso()
    server = McpServer(id=new_id("mcp"), name=body.name.strip(), description=body.description.strip(), direction=body.direction, transport=body.transport, endpoint=body.endpoint.strip(), auth_type=body.auth_type, credentials=encrypt_secret(body.credentials) if body.credentials else "", status="unhealthy", health="未检测", created_by=user.id, created_at=now, updated_at=now)
    session.add(server)
    tools = []
    for item in body.tools:
        tool = McpTool(id=new_id("mct"), server_id=server.id, name=item.name.strip(), description=item.description, input_schema=item.input_schema, risk=item.risk, approval=item.approval, status="discovered", enabled=True, created_at=now, updated_at=now)
        session.add(tool)
        tools.append(tool)
    await AuditService(session).record(actor=user.name, action="mcp:server_create", target=server.name, result="success")
    await session.commit()
    return ok({"server": _server_brief(server, tools)}, "MCP Server 已创建")


@router.patch("/mcp-tools/{tool_id}")
async def update_mcp_tool(tool_id: str, body: McpToolUpdateReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    tool = await session.get(McpTool, tool_id)
    if not tool:
        raise BizError(BizCode.NOT_FOUND, "MCP Tool 不存在")
    for field in ("status", "enabled", "risk", "approval"):
        value = getattr(body, field)
        if value is not None:
            setattr(tool, field, value)
    tool.updated_at = now_iso()
    await session.commit()
    return ok({"tool": _tool_brief(tool)}, "MCP Tool 已更新")


@router.patch("/mcp-servers/{server_id}")
async def update_mcp_server(server_id: str, body: McpServerReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    server = await session.get(McpServer, server_id)
    if not server or server.deleted:
        raise BizError(BizCode.NOT_FOUND, "MCP Server 不存在")
    server.name, server.description, server.direction = body.name.strip(), body.description.strip(), body.direction
    server.transport, server.endpoint, server.auth_type = body.transport, body.endpoint.strip(), body.auth_type
    if body.credentials:
        server.credentials = encrypt_secret(body.credentials)
    server.updated_at = now_iso()
    await session.commit()
    tools = (await session.execute(select(McpTool).where(McpTool.server_id == server.id))).scalars().all()
    return ok({"server": _server_brief(server, tools)}, "MCP Server 已更新")


@router.delete("/mcp-servers/{server_id}")
async def delete_mcp_server(server_id: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    server = await session.get(McpServer, server_id)
    if not server or server.deleted:
        raise BizError(BizCode.NOT_FOUND, "MCP Server 不存在")
    server.deleted, server.status, server.updated_at = True, "disabled", now_iso()
    await session.execute(update(McpTool).where(McpTool.server_id == server_id).values(enabled=False, status="disabled", updated_at=server.updated_at))
    await AuditService(session).record(actor=user.name, action="mcp:server_delete", target=server.name, result="success")
    await session.commit()
    return ok({"id": server_id}, "MCP Server 已删除")


def _skill_brief(skill: ExpertSkill) -> dict:
    return {"id": skill.id, "name": skill.name, "slug": skill.slug, "description": skill.description, "status": skill.status, "version": skill.version, "owner": skill.owner_id, "tools": 0, "boundExperts": 0, "subgraph": False, "updated": skill.updated_at, "packageType": skill.package_type, "filename": skill.filename, "sizeBytes": skill.size_bytes}


def _validate_skill_archive(filename: str, data: bytes) -> str:
    import io
    import posixpath
    import tarfile
    import zipfile

    lower = filename.lower()
    if lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                total = 0
                for info in infos:
                    normalized = posixpath.normpath(info.filename.replace("\\", "/"))
                    if normalized.startswith("../") or normalized in {"..", "."} or normalized.startswith("/"):
                        raise BizError(BizCode.VALIDATION, "压缩包包含不安全路径")
                    total += info.file_size
                if len(infos) > 1000 or total > 100 * 1024 * 1024:
                    raise BizError(BizCode.VALIDATION, "压缩包内容超限")
                if not any(posixpath.basename(info.filename) == "SKILL.md" for info in infos):
                    raise BizError(BizCode.VALIDATION, "Skill 包必须包含 SKILL.md")
        except zipfile.BadZipFile as exc:
            raise BizError(BizCode.VALIDATION, "ZIP 文件损坏或格式无效") from exc
        return "zip"
    if lower.endswith((".tar", ".tar.gz", ".tgz")):
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
                members = archive.getmembers()
                total = 0
                for member in members:
                    normalized = posixpath.normpath(member.name.replace("\\", "/"))
                    if normalized.startswith("../") or normalized in {"..", "."} or normalized.startswith("/") or member.issym() or member.islnk():
                        raise BizError(BizCode.VALIDATION, "tar 包包含不安全路径或链接")
                    total += member.size
                if len(members) > 1000 or total > 100 * 1024 * 1024:
                    raise BizError(BizCode.VALIDATION, "压缩包内容超限")
                if not any(posixpath.basename(member.name) == "SKILL.md" for member in members):
                    raise BizError(BizCode.VALIDATION, "Skill 包必须包含 SKILL.md")
        except tarfile.ReadError as exc:
            raise BizError(BizCode.VALIDATION, "tar 文件损坏或格式无效") from exc
        return "tar"
    raise BizError(BizCode.VALIDATION, "仅支持 .tar、.tar.gz、.tgz 和 .zip")


@router.get("/expert-skills")
async def list_expert_skills(session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)]):
    rows = (await session.execute(select(ExpertSkill).where(ExpertSkill.deleted.is_(False)).order_by(ExpertSkill.updated_at.desc()))).scalars().all()
    return ok({"items": [_skill_brief(row) for row in rows]})


@router.post("/expert-skills/upload")
async def upload_expert_skill(file: Annotated[UploadFile, File(...)], user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)], name: Annotated[str, Form()] = "", version: Annotated[str, Form()] = "v1.0.0", description: Annotated[str, Form()] = ""):
    build_authorizer(user).require("expert:resource_manage")
    filename = file.filename or ""
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise BizError(BizCode.VALIDATION, "Skill 包过大，上限 50MB")
    package_type = _validate_skill_archive(filename, data)
    import re
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.(tar\.gz|tgz|tar|zip)$", "", stem, flags=re.IGNORECASE)
    skill_name = name.strip() or stem or "未命名 Skill"
    slug = re.sub(r"[^a-z0-9-]+", "-", skill_name.lower()).strip("-") or f"skill-{new_id('')[:8]}"
    if await session.scalar(select(ExpertSkill).where(ExpertSkill.slug == slug, ExpertSkill.deleted.is_(False))):
        slug = f"{slug}-{new_id('')[:6]}"
    object_name = f"expert-skills/{new_id('skill')}/{filename}"
    from flowhub_api.clients.minio import get_minio
    from flowhub_api.core.config import get_settings
    minio = get_minio()
    if minio:
        minio.put_object(get_settings().minio_bucket, object_name, __import__("io").BytesIO(data), len(data))
    now = now_iso()
    skill = ExpertSkill(id=new_id("skill"), name=skill_name, slug=slug, description=description.strip(), version=version.strip() or "v1.0.0", package_type=package_type, filename=filename, size_bytes=len(data), object_name=object_name if minio else None, owner_id=user.id, created_at=now, updated_at=now)
    session.add(skill)
    await AuditService(session).record(actor=user.name, action="expert:skill_upload", target=skill.name, result="success")
    await session.commit()
    return ok({"skill": _skill_brief(skill)}, "Skill 包上传成功")


@router.delete("/expert-skills/{skill_id}")
async def delete_expert_skill(skill_id: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    skill = await session.get(ExpertSkill, skill_id)
    if not skill or skill.deleted:
        raise BizError(BizCode.NOT_FOUND, "Skill 不存在")
    skill.deleted = True
    skill.status = "archived"
    skill.updated_at = now_iso()
    await AuditService(session).record(actor=user.name, action="expert:skill_delete", target=skill.name, result="success")
    await session.commit()
    return ok({"id": skill_id}, "Skill 已删除（历史版本仍保留）")


async def resolve_model_id(session: AsyncSession, provider_or_model_id: str, model_name: str) -> str | None:
    direct = await session.get(LlmProviderModel, provider_or_model_id) if provider_or_model_id else None
    if direct and (not model_name or direct.model == model_name):
        return direct.id
    provider = await session.get(LlmProvider, provider_or_model_id) if provider_or_model_id else None
    if provider:
        statement = select(LlmProviderModel).where(LlmProviderModel.provider_id == provider.id, LlmProviderModel.enabled.is_(True))
        if model_name:
            statement = statement.where(LlmProviderModel.model == model_name)
        resolved = (await session.execute(statement)).scalars().first()
        return resolved.id if resolved else None
    return None


def expert_brief(expert: Expert, deployments: int = 0, version: ExpertVersion | None = None) -> dict:
    return {"id": expert.id, "name": expert.name, "slug": expert.slug, "kind": expert.kind, "status": expert.status, "description": expert.description, "ownerId": expert.owner_id, "owner": expert.owner_id, "currentVersionId": expert.current_version_id, "version": version.version if version else "v0.1", "skills": version.skills if version else [], "deployments": deployments, "calls": 0, "successRate": 0, "lastRun": "尚未运行", "updated": expert.updated_at, "updatedAt": expert.updated_at}


def version_brief(version: ExpertVersion) -> dict:
    return {"id": version.id, "expertId": version.expert_id, "version": version.version, "status": version.status, "systemPrompt": version.system_prompt, "providerModelId": version.provider_model_id, "skills": version.skills, "knowledgeBaseIds": version.knowledge_base_ids, "toolPolicies": version.tool_policies, "validation": version.validation, "testedAt": version.tested_at, "publishedAt": version.published_at}


@router.get("/providers")
async def list_providers(session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)]):
    providers = (await session.execute(select(LlmProvider))).scalars().all()
    models = (await session.execute(select(LlmProviderModel))).scalars().all()
    return ok({"items": [{"id": item.id, "name": item.name, "provider": item.name, "engine": "api", "baseUrl": item.base_url, "credential": "configured" if item.credential_configured else "missing", "status": item.status, "maxContextTokens": item.max_context_tokens, "latency": "未检测", "models": [model.model for model in models if model.provider_id == item.id and model.enabled], "modelEntries": [{"id": model.id, "model": model.model} for model in models if model.provider_id == item.id and model.enabled]} for item in providers]})


@router.post("/providers")
async def create_provider(body: ProviderReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    provider = LlmProvider(id=new_id("llp"), name=body.name, base_url=body.base_url, api_key=encrypt_secret(body.api_key) if body.api_key else "", credential_configured=bool(body.api_key), status="healthy" if body.api_key else "degraded", max_context_tokens=body.max_context_tokens, created_by=user.id, created_at=now_iso())
    session.add(provider)
    created_models: list[LlmProviderModel] = []
    for model in body.models:
        created_model = LlmProviderModel(id=new_id("lpm"), provider_id=provider.id, model=model, label=model)
        created_models.append(created_model)
        session.add(created_model)
    await session.commit()
    return ok({"provider": {"id": provider.id, "name": provider.name, "models": [{"id": model.id, "model": model.model} for model in created_models]}}, "Provider 已创建")


@router.post("/providers/test")
async def test_provider(body: ProviderTestReq, user: Annotated[User, Depends(get_current_user)]):
    """Validate an OpenAI-compatible Provider before a credential is persisted."""
    build_authorizer(user).require("expert:resource_manage")
    from langchain_openai import ChatOpenAI

    started = perf_counter()
    try:
        llm = ChatOpenAI(model=body.model, base_url=body.base_url, api_key=body.api_key, temperature=0, timeout=15)
        await llm.ainvoke([("user", "Reply with pong.")])
        return ok({"ok": True, "latencyMs": round((perf_counter() - started) * 1000)}, "Provider 连接成功")
    except Exception as exc:  # noqa: BLE001
        return ok({"ok": False, "latencyMs": round((perf_counter() - started) * 1000), "error": f"{type(exc).__name__}"}, "Provider 连接失败")


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:resource_manage")
    provider = await session.get(LlmProvider, provider_id)
    if not provider:
        raise BizError(BizCode.NOT_FOUND, "Provider 不存在")
    provider.name = body.name
    provider.base_url = body.base_url
    provider.max_context_tokens = body.max_context_tokens
    if body.api_key:
        provider.api_key = encrypt_secret(body.api_key)
        provider.credential_configured = True
        provider.status = "healthy"
    existing = (await session.execute(select(LlmProviderModel).where(LlmProviderModel.provider_id == provider_id))).scalars().all()
    requested = {model.strip() for model in body.models if model.strip()}
    for model in existing:
        model.enabled = model.model in requested
    existing_names = {model.model for model in existing}
    for model_name in requested - existing_names:
        session.add(LlmProviderModel(id=new_id("lpm"), provider_id=provider_id, model=model_name, label=model_name))
    await session.commit()
    return ok({"provider": {"id": provider.id, "name": provider.name}}, "Provider 已更新")


@router.get("/experts")
async def list_experts(session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)], status: str = ""):
    statement = select(Expert)
    if status:
        statement = statement.where(Expert.status == status)
    experts = (await session.execute(statement.order_by(Expert.updated_at.desc()))).scalars().all()
    deployments = (await session.execute(select(ExpertDeployment))).scalars().all()
    versions = (await session.execute(select(ExpertVersion))).scalars().all()
    current_versions = {version.expert_id: version for version in versions if version.status in {"draft", "testing", "published"}}
    return ok({"items": [expert_brief(item, sum(1 for deployment in deployments if deployment.expert_id == item.id), current_versions.get(item.id)) for item in experts]})


@router.post("/experts")
async def create_expert(body: ExpertCreateReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:create")
    if (await session.execute(select(Expert).where(Expert.slug == body.slug))).scalar_one_or_none():
        raise BizError(BizCode.DUPLICATE_OPERATION, "Slug 已存在")
    expert = Expert(id=new_id("exp"), name=body.name, slug=body.slug, description=body.description, owner_id=user.id, created_at=now_iso(), updated_at=now_iso())
    provider_model_id = await resolve_model_id(session, body.provider_model_id, body.model)
    version = ExpertVersion(id=new_id("ev"), expert_id=expert.id, version="v0.1", system_prompt=body.system_prompt, provider_model_id=provider_model_id, skills=body.skills, knowledge_base_ids=body.knowledge_base_ids, tool_policies=body.tool_policies, policy_json=body.tool_policies, output_contract_json={}, execution_profile_json={}, knowledge_policy_json={}, memory_policy_json={}, checksum="", published_by="", created_by=user.id, created_at=now_iso())
    expert.current_version_id = version.id
    session.add_all([expert, version])
    await AuditService(session).record(actor=user.name, action="expert:create", target=expert.name, result="success")
    await session.commit()
    return ok({"expert": expert_brief(expert, version=version), "version": version_brief(version)}, "Expert 草稿已创建")


@router.get("/experts/{expert_id}")
async def get_expert(expert_id: str, session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)]):
    expert = await session.get(Expert, expert_id)
    if not expert:
        raise BizError(BizCode.NOT_FOUND, "Expert 不存在")
    versions = (await session.execute(select(ExpertVersion).where(ExpertVersion.expert_id == expert_id))).scalars().all()
    deployments = (await session.execute(select(ExpertDeployment).where(ExpertDeployment.expert_id == expert_id))).scalars().all()
    current = next((item for item in versions if item.id == expert.current_version_id), versions[-1] if versions else None)
    return ok({"expert": expert_brief(expert, len(deployments), current), "versions": [version_brief(item) for item in versions], "deployments": [{"id": item.id, "name": item.name, "versionId": item.expert_version_id, "expertVersion": current.version if current and current.id == item.expert_version_id else "", "environment": item.environment, "alias": item.alias, "status": item.status} for item in deployments]})


@router.patch("/experts/{expert_id}")
async def update_expert(expert_id: str, body: ExpertVersionReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:update")
    expert = await session.get(Expert, expert_id)
    if not expert or expert.kind == "builtin":
        raise BizError(BizCode.NOT_FOUND, "Expert 不存在或不可编辑")
    current = await session.get(ExpertVersion, expert.current_version_id) if expert.current_version_id else None
    if current and current.status == "published":
        nums = [int(item.version[1:]) for item in (await session.execute(select(ExpertVersion.version).where(ExpertVersion.expert_id == expert_id))).scalars().all() if item.startswith("v") and item[1:].replace(".", "", 1).isdigit()]
        version = ExpertVersion(id=new_id("ev"), expert_id=expert_id, version=f"v{(max(nums) + 0.1 if nums else 0.1):.1f}", output_contract_json={}, execution_profile_json={}, knowledge_policy_json={}, memory_policy_json={}, policy_json={}, checksum="", published_by="", created_by=user.id, created_at=now_iso())
        session.add(version)
        expert.current_version_id = version.id
        current = version
    if current is None:
        raise BizError(BizCode.NOT_FOUND, "Expert 当前版本不存在")
    expert.description = body.description
    expert.updated_at = now_iso()
    current.system_prompt, current.provider_model_id = body.system_prompt, await resolve_model_id(session, body.provider_model_id, body.model)
    current.skills, current.knowledge_base_ids, current.tool_policies = body.skills, body.knowledge_base_ids, body.tool_policies
    current.status, current.tested_at = "draft", ""
    await session.commit()
    return ok({"expert": expert_brief(expert, version=current), "version": version_brief(current)}, "Expert 草稿已保存")


@router.delete("/experts/{expert_id}")
async def delete_expert(expert_id: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:delete")
    expert = await session.get(Expert, expert_id)
    if not expert or expert.kind != "custom" or expert.status != "draft":
        raise BizError(BizCode.FORBIDDEN, "仅可删除自建草稿")
    if (await session.execute(select(ExpertDeployment).where(ExpertDeployment.expert_id == expert_id))).scalars().first():
        raise BizError(BizCode.FORBIDDEN, "已有 Deployment 的 Expert 不可删除")
    await session.delete(expert)
    await session.commit()
    return ok({"id": expert_id}, "Expert 草稿已删除")


@router.post("/experts/{expert_id}/duplicate")
async def duplicate_expert(expert_id: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:create")
    source = await session.get(Expert, expert_id)
    if not source:
        raise BizError(BizCode.NOT_FOUND, "Expert 不存在")
    version = await session.get(ExpertVersion, source.current_version_id) if source.current_version_id else None
    if not version:
        raise BizError(BizCode.NOT_FOUND, "Expert Version 不存在")
    slug = f"{source.slug}-copy"
    suffix = 2
    while (await session.execute(select(Expert).where(Expert.slug == slug))).scalar_one_or_none():
        slug = f"{source.slug}-copy-{suffix}"
        suffix += 1
    expert = Expert(id=new_id("exp"), name=f"{source.name} 副本", slug=slug, kind="custom", status="draft", description=source.description, owner_id=user.id, created_at=now_iso(), updated_at=now_iso())
    copied = ExpertVersion(id=new_id("ev"), expert_id=expert.id, version="v0.1", system_prompt=version.system_prompt, provider_model_id=version.provider_model_id, skills=version.skills, knowledge_base_ids=version.knowledge_base_ids, tool_policies=version.tool_policies, output_contract_json={}, execution_profile_json={}, knowledge_policy_json={}, memory_policy_json={}, policy_json=version.tool_policies, checksum="", published_by="", created_by=user.id, created_at=now_iso())
    expert.current_version_id = copied.id
    session.add_all([expert, copied])
    await session.commit()
    return ok({"expert": expert_brief(expert, version=copied), "version": version_brief(copied)}, "Expert 草稿副本已创建")


@router.post("/experts/{expert_id}/versions/{version_id}/test")
async def test_expert(expert_id: str, version_id: str, body: ExpertRunReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:run")
    version = await session.get(ExpertVersion, version_id)
    if not version or version.expert_id != expert_id:
        raise BizError(BizCode.NOT_FOUND, "Expert Version 不存在")
    blockers = await validate_version(session, version)
    version.validation = {"ok": not blockers, "blockers": blockers}
    if blockers:
        await session.commit()
        raise BizError(BizCode.FLOW_VALIDATE, "；".join(blockers), http_status=422)
    run = ExpertRun(id=new_id("run"), expert_id=expert_id, expert_version_id=version.id, requested_by=user.id, status="interrupted" if body.write_intent else "running", input=body.prompt, trace_id=new_id("trace"), started_at=now_iso())
    session.add(run)
    await session.flush()
    await execute_run(session, run, version, user)
    version.tested_at = now_iso()
    version.status = "testing"
    await session.commit()
    return ok({"run": run_brief(run)}, "测试运行已创建")


def run_brief(run: ExpertRun) -> dict:
    return {"id": run.id, "session": run.input[:64], "expert": run.expert_id, "version": run.expert_version_id, "deployment": run.deployment_id or "—", "expertId": run.expert_id, "versionId": run.expert_version_id, "deploymentId": run.deployment_id, "status": run.status, "input": run.input, "output": run.output, "traceId": run.trace_id, "error": run.error, "duration": "—", "started": run.started_at, "startedAt": run.started_at, "finishedAt": run.finished_at, "events": []}


@router.post("/experts/{expert_id}/versions/{version_id}/publish")
async def publish_expert(expert_id: str, version_id: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:publish")
    expert, version = await session.get(Expert, expert_id), await session.get(ExpertVersion, version_id)
    if not expert or not version or version.expert_id != expert_id:
        raise BizError(BizCode.NOT_FOUND, "Expert 或版本不存在")
    blockers = await validate_version(session, version)
    if blockers or not version.tested_at:
        raise BizError(BizCode.FLOW_VALIDATE, "；".join(blockers or ["当前版本尚未通过测试"]), http_status=422)
    version.status, version.published_at, expert.status, expert.current_version_id, expert.updated_at = "published", now_iso(), "published", version.id, now_iso()
    await session.commit()
    return ok({"expert": expert_brief(expert, version=version), "version": version_brief(version)}, "Expert Version 已发布")


@router.post("/experts/{expert_id}/deployments")
async def deploy_expert(expert_id: str, body: DeploymentReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:deploy")
    expert = await session.get(Expert, expert_id)
    version = await session.get(ExpertVersion, expert.current_version_id) if expert and expert.current_version_id else None
    if not expert or expert.status != "published" or not version:
        raise BizError(BizCode.FLOW_VALIDATE, "仅已发布 Expert 可创建 Deployment", http_status=422)
    existing = (await session.execute(select(ExpertDeployment).where(ExpertDeployment.name == body.name))).scalars().first()
    if existing:
        raise BizError(BizCode.DUPLICATE_OPERATION, f"Deployment 名称已存在：{body.name}")
    deployment = ExpertDeployment(id=new_id("dep"), expert_id=expert.id, expert_version_id=version.id, name=body.name, environment=body.environment, alias=body.alias, status="active", created_by=user.id, created_at=now_iso())
    session.add(deployment)
    await session.commit()
    return ok({"deployment": {"id": deployment.id, "versionId": deployment.expert_version_id, "name": deployment.name}}, "Deployment 已创建")


@router.get("/expert-deployments")
async def list_deployments(session: Annotated[AsyncSession, Depends(get_db)], _: Annotated[User, Depends(get_current_user)]):
    rows = (await session.execute(select(ExpertDeployment).order_by(ExpertDeployment.created_at.desc()))).scalars().all()
    return ok({"items": [{"id": item.id, "expertId": item.expert_id, "name": item.name, "environment": item.environment, "alias": item.alias, "status": item.status, "versionId": item.expert_version_id} for item in rows]})


@router.post("/expert-deployments/{deployment_id}/{action}")
async def change_deployment_status(deployment_id: str, action: str, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:deploy")
    deployment = await session.get(ExpertDeployment, deployment_id)
    if not deployment or action not in {"suspend", "resume"}:
        raise BizError(BizCode.NOT_FOUND, "Deployment 或动作不存在")
    deployment.status = "suspended" if action == "suspend" else "active"
    await session.commit()
    return ok({"id": deployment.id, "status": deployment.status}, "Deployment 状态已更新")


@router.get("/expert-runs")
async def list_runs(session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)], status: str = ""):
    statement = select(ExpertRun).where(ExpertRun.requested_by == user.id)
    if status:
        statement = statement.where(ExpertRun.status == status)
    rows = (await session.execute(statement.order_by(ExpertRun.started_at.desc()))).scalars().all()
    return ok({"items": [run_brief(row) for row in rows]})


@router.post("/expert-runs")
async def create_run(body: ExpertRunReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    build_authorizer(user).require("expert:run")
    deployment = await session.get(ExpertDeployment, body.deployment_id) if body.deployment_id else None
    if not deployment:
        raise BizError(BizCode.VALIDATION, "必须指定有效的 Expert Deployment")
    version = await session.get(ExpertVersion, deployment.expert_version_id)
    if not version:
        raise BizError(BizCode.NOT_FOUND, "Deployment 版本不存在")
    run = ExpertRun(id=new_id("run"), expert_id=deployment.expert_id, expert_version_id=version.id, deployment_id=deployment.id, requested_by=user.id, status="interrupted" if body.write_intent else "running", input=body.prompt, trace_id=new_id("trace"), started_at=now_iso())
    session.add(run)
    await session.flush()
    await execute_run(session, run, version, user)
    await session.commit()
    return ok({"run": run_brief(run)}, "Expert Run 已创建")


def chat_session_brief(session: ExpertChatSession) -> dict:
    return {"id": session.id, "expertId": session.expert_id, "expertVersionId": session.expert_version_id, "deploymentId": session.deployment_id, "providerModelId": session.provider_model_id, "projectName": session.project_name, "title": session.title, "updated": session.updated_at}


def chat_message_brief(message: ExpertChatMessage) -> dict:
    return {"id": message.id, "role": message.role, "content": message.content, "time": message.created_at, "runId": message.run_id, "status": message.status, "toolTrace": message.tool_trace, "files": message.files}


@router.get("/expert-chat/sessions")
async def list_chat_sessions(session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    rows = (await session.execute(select(ExpertChatSession).where(ExpertChatSession.owner_id == user.id).order_by(ExpertChatSession.updated_at.desc()))).scalars().all()
    return ok({"items": [chat_session_brief(row) for row in rows]})


@router.post("/expert-chat/sessions")
async def create_chat_session(body: ExpertChatSessionReq, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    deployment = await session.get(ExpertDeployment, body.deployment_id) if body.deployment_id else None
    if body.deployment_id and (not deployment or deployment.status != "active"):
        raise BizError(BizCode.VALIDATION, "选择的 Expert Deployment 不可用")
    # 版本绑定（编辑器测试会话）：直接对某个版本（含草稿）多轮对话，无需 Deployment。
    # 测试模型必须显式给出，并且会话级覆盖版本默认模型（不改写版本自身配置）。
    bound_version = None
    selected_model = None
    if not deployment and body.version_id:
        bound_version = await session.get(ExpertVersion, body.version_id)
        if not bound_version:
            raise BizError(BizCode.NOT_FOUND, "绑定的 Expert Version 不存在")
        if not body.provider_model_id:
            raise BizError(BizCode.VALIDATION, "请选择用于测试的 Provider 模型", http_status=422)
        selected_model = await session.get(LlmProviderModel, body.provider_model_id)
        selected_provider = await session.get(LlmProvider, selected_model.provider_id) if selected_model else None
        if not selected_model or not selected_model.enabled or not selected_provider or selected_provider.status != "healthy" or not selected_provider.credential_configured:
            raise BizError(BizCode.FLOW_VALIDATE, "请选择健康且已配置凭据的测试 Provider 模型", http_status=422)
    elif body.provider_model_id:
        selected_model = await session.get(LlmProviderModel, body.provider_model_id)
        selected_provider = await session.get(LlmProvider, selected_model.provider_id) if selected_model else None
        if not selected_model:
            raise BizError(BizCode.NOT_FOUND, "默认对话模型不存在")
        if not selected_model.enabled or not selected_provider or selected_provider.status != "healthy" or not selected_provider.credential_configured:
            raise BizError(BizCode.FLOW_VALIDATE, "请选择健康且已配置凭据的 Provider 模型", http_status=422)
    row = ExpertChatSession(id=new_id("chat"), owner_id=user.id, expert_id=deployment.expert_id if deployment else (bound_version.expert_id if bound_version else None), expert_version_id=bound_version.id if bound_version else None, deployment_id=deployment.id if deployment else None, provider_model_id=body.provider_model_id or None, project_name=body.project_name or None, title=body.title[:160] or "新会话", created_at=now_iso(), updated_at=now_iso())
    session.add(row)
    await session.commit()
    if row.project_name:
        await schedule_mirror_build(row.project_name)  # 首次绑定时后台预构建仓库镜像，聊天请求不阻塞
    return ok({"session": chat_session_brief(row)}, "会话已创建")


@router.patch("/expert-chat/sessions/{session_id}")
async def rename_chat_session(session_id: str, body: ExpertChatSessionRenameReq, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    if body.title:
        chat.title = body.title.strip()
    if body.expert_id:
        deployment = (await session.execute(
            select(ExpertDeployment).where(
                ExpertDeployment.expert_id == body.expert_id, ExpertDeployment.status == "active")
        )).scalars().first()
        if not deployment:
            raise BizError(BizCode.VALIDATION, "所选 Expert 无活跃 Deployment，请先部署", http_status=409)
        chat.expert_id = body.expert_id
        chat.deployment_id = deployment.id
    elif "expert_id" in body.model_fields_set and not body.expert_id:
        chat.expert_id = None
        chat.deployment_id = None
    if "provider_model_id" in body.model_fields_set:
        if body.provider_model_id:
            selected_model = await session.get(LlmProviderModel, body.provider_model_id)
            selected_provider = await session.get(LlmProvider, selected_model.provider_id) if selected_model else None
            if not selected_model:
                raise BizError(BizCode.NOT_FOUND, "默认对话模型不存在")
            if not selected_model.enabled or not selected_provider or selected_provider.status != "healthy" or not selected_provider.credential_configured:
                raise BizError(BizCode.FLOW_VALIDATE, "请选择健康且已配置凭据的 Provider 模型", http_status=422)
        chat.provider_model_id = body.provider_model_id or None
    if "project_name" in body.model_fields_set:
        chat.project_name = body.project_name or None
    chat.updated_at = now_iso()
    await session.commit()
    if chat.project_name:
        await schedule_mirror_build(chat.project_name)  # 绑定/切换项目时后台预构建仓库镜像
    return ok({"session": chat_session_brief(chat)}, "会话已更新")


@router.get("/expert-chat/sessions/{session_id}/messages")
async def list_chat_messages(session_id: str, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    rows = (await session.execute(select(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id).order_by(ExpertChatMessage.sequence))).scalars().all()
    return ok({"items": [chat_message_brief(row) for row in rows]})


@router.post("/expert-chat/sessions/{session_id}/messages")
async def create_chat_message(session_id: str, body: ExpertChatMessageReq, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    # 绑定优先级：活跃 Deployment（已发布）> 直接绑定的版本（编辑器草稿测试，无需部署）
    deployment = await session.get(ExpertDeployment, chat.deployment_id) if chat.deployment_id else None
    bound_version: ExpertVersion | None = None
    if deployment:
        bound_version = await session.get(ExpertVersion, deployment.expert_version_id)
    elif chat.expert_version_id:
        bound_version = await session.get(ExpertVersion, chat.expert_version_id)
    existing = (await session.execute(select(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id).order_by(ExpertChatMessage.sequence.desc()))).scalars().first()
    sequence = (existing.sequence if existing else 0) + 1
    user_message = ExpertChatMessage(id=new_id("msg"), session_id=session_id, sequence=sequence, role="user", content=body.content, created_at=now_iso())
    session.add(user_message)
    # 组装会话历史（token 预算制）：预览不阻塞当前轮；超预算 → 后台异步压缩（Claude Code 式）
    history_rows = [(m.sequence, m.role, m.content) for m in (await session.execute(
        select(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id, ExpertChatMessage.sequence < sequence).order_by(ExpertChatMessage.sequence)
    )).scalars().all()]
    provider_max_tokens: int | None = None
    if chat.provider_model_id:
        _pm = await session.get(LlmProviderModel, chat.provider_model_id)
        if _pm:
            _pp = await session.get(LlmProvider, _pm.provider_id) if _pm.provider_id else None
            provider_max_tokens = _pp.max_context_tokens if _pp else None
    elif bound_version and bound_version.provider_model_id:
        _pm = await session.get(LlmProviderModel, bound_version.provider_model_id)
        if _pm:
            _pp = await session.get(LlmProvider, _pm.provider_id) if _pm.provider_id else None
            provider_max_tokens = _pp.max_context_tokens if _pp else None
    budget = context_budget_bytes(provider_max_tokens)
    history_text, needs_compact = session_history_preview(
        history_rows, summary=chat.compaction_summary, marker=chat.compaction_sequence, budget_bytes=budget)
    if needs_compact:
        import asyncio
        from flowhub_api.services.expert_runtime import compact_session_async
        asyncio.create_task(compact_session_async(session_id, provider_max_tokens))  # 后台压缩，不阻塞当前问答
    if bound_version:
        run = ExpertRun(id=new_id("run"), expert_id=bound_version.expert_id, expert_version_id=bound_version.id, deployment_id=deployment.id if deployment else None, session_id=session_id, requested_by=user.id, status="interrupted" if body.write_intent else "running", input=body.content, trace_id=new_id("trace"), started_at=now_iso())
        session.add(run)
        await session.flush()
        await execute_run(session, run, bound_version, user, history=history_text, provider_model_id=chat.provider_model_id, project_name=chat.project_name)
        assistant_text = "运行已中断，等待审批。" if run.status == "interrupted" else (run.output or run.error or "运行完成，无文本输出。")
        run_id, run_status = run.id, run.status
    else:
        assistant_text, tool_trace = await run_native_flowhub_chat(session, body.content, user, chat.provider_model_id, history=history_text, project_name=chat.project_name)
        run_id, run_status = None, "completed"
        ref = await save_chat_output_document(session, project_name=chat.project_name, output=assistant_text, user=user)
        if ref:
            chat_files = [ref]
    chat_files: list[dict] = []
    if bound_version:
        tool_trace = [{"tool": event.title, "status": event.status, "summary": event.payload} for event in (await session.execute(select(ExpertRunEvent).where(ExpertRunEvent.run_id == run.id).order_by(ExpertRunEvent.sequence))).scalars().all()]
        if run_status == "succeeded" and run.output:
            ref = await save_chat_output_document(session, project_name=chat.project_name, output=run.output, user=user)
            if ref:
                chat_files = [ref]
        if not deployment:
            # 草稿测试会话：真实执行即视为通过测试（与 /versions/{vid}/test 同语义），从而解锁发布。
            # 已发布 Deployment 固定的版本不受影响，不回写测试状态。
            blockers = await validate_version(session, bound_version)
            bound_version.validation = {"ok": not blockers, "blockers": blockers}
            bound_version.tested_at = now_iso()
            bound_version.status = "testing"
    assistant_message = ExpertChatMessage(id=new_id("msg"), session_id=session_id, run_id=run_id, sequence=sequence + 1, role="assistant", content=assistant_text, status=run_status, tool_trace=tool_trace, files=chat_files, created_at=now_iso())
    session.add(assistant_message)
    chat.updated_at = now_iso()
    # marker/摘要写回由后台 compact_session_async 负责（主请求不阻塞、不写回）
    if chat.title == "新会话":
        chat.title = body.content[:60]
    await session.commit()
    brief = chat_message_brief(assistant_message)
    brief["compacted"] = needs_compact  # 前端提示"早期上下文将自动压缩"
    return ok({"userMessage": chat_message_brief(user_message), "assistantMessage": brief, "run": run_brief(run) if bound_version else None}, "消息已处理")


@router.post("/expert-chat/sessions/{session_id}/messages/stream")
async def stream_chat_message(session_id: str, body: ExpertChatMessageReq, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    """流式消息端点：原生 FlowHub 会话逐 token；Expert/版本测试会话经 emitter 逐 token + 实时 trace。"""
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    # 绑定优先级与 create_chat_message 一致：活跃 Deployment > 直接绑定的版本（编辑器草稿测试）
    deployment = await session.get(ExpertDeployment, chat.deployment_id) if chat.deployment_id else None
    bound_version: ExpertVersion | None = None
    if deployment:
        bound_version = await session.get(ExpertVersion, deployment.expert_version_id)
    elif chat.expert_version_id:
        bound_version = await session.get(ExpertVersion, chat.expert_version_id)
    existing = (await session.execute(select(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id).order_by(ExpertChatMessage.sequence.desc()))).scalars().first()
    sequence = (existing.sequence if existing else 0) + 1
    user_message = ExpertChatMessage(id=new_id("msg"), session_id=session_id, sequence=sequence, role="user", content=body.content, created_at=now_iso())
    session.add(user_message)
    # 会话历史（token 预算制）与 create_chat_message 同源；超预算后台压缩
    history_rows = [(m.sequence, m.role, m.content) for m in (await session.execute(
        select(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id, ExpertChatMessage.sequence < sequence).order_by(ExpertChatMessage.sequence)
    )).scalars().all()]
    provider_max_tokens: int | None = None
    if chat.provider_model_id:
        _pm = await session.get(LlmProviderModel, chat.provider_model_id)
        _pp = await session.get(LlmProvider, _pm.provider_id) if _pm else None
        provider_max_tokens = _pp.max_context_tokens if _pp else None
    elif bound_version and bound_version.provider_model_id:
        _pm = await session.get(LlmProviderModel, bound_version.provider_model_id)
        _pp = await session.get(LlmProvider, _pm.provider_id) if _pm else None
        provider_max_tokens = _pp.max_context_tokens if _pp else None
    budget = context_budget_bytes(provider_max_tokens)
    history_text, needs_compact = session_history_preview(
        history_rows, summary=chat.compaction_summary, marker=chat.compaction_sequence, budget_bytes=budget)
    if needs_compact:
        asyncio.create_task(compact_session_async(session_id, provider_max_tokens))
    await session.commit()
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
    producer_cancelled = asyncio.Event()

    async def produce():
        from flowhub_api.db.session import SessionFactory

        async with SessionFactory() as worker_session:
            worker_chat = await worker_session.get(ExpertChatSession, session_id)
            worker_user = await worker_session.get(User, user.id)
            if not worker_chat or not worker_user:
                await queue.put(("error", {"message": "会话或用户不存在"}))
                await queue.put(None)
                return

            async def emit(event: str, payload: dict) -> None:
                if not producer_cancelled.is_set():
                    await queue.put((event, payload))

            try:
                if bound_version:
                    # Expert / 版本测试会话：LangGraph 图带 emitter 执行，token 与 trace 实时推送
                    worker_version = await worker_session.get(ExpertVersion, bound_version.id)
                    worker_deployment = await worker_session.get(ExpertDeployment, deployment.id) if deployment else None
                    run = ExpertRun(id=new_id("run"), expert_id=worker_version.expert_id, expert_version_id=worker_version.id, deployment_id=worker_deployment.id if worker_deployment else None, session_id=session_id, requested_by=worker_user.id, status="interrupted" if body.write_intent else "running", input=body.content, trace_id=new_id("trace"), started_at=now_iso())
                    worker_session.add(run)
                    await worker_session.flush()
                    await execute_run(worker_session, run, worker_version, worker_user, history=history_text, provider_model_id=worker_chat.provider_model_id, emitter=emit, project_name=worker_chat.project_name)
                    tool_trace = [{"tool": event.title, "status": event.status, "summary": event.payload} for event in (await worker_session.execute(select(ExpertRunEvent).where(ExpertRunEvent.run_id == run.id).order_by(ExpertRunEvent.sequence))).scalars().all()]
                    assistant_text = "运行已中断，等待审批。" if run.status == "interrupted" else (run.output or run.error or "运行完成，无文本输出。")
                    # 长文档型产出自动归档为项目文档，挂到消息 files（右侧产出文件栏可下载/预览）
                    chat_files: list[dict] = []
                    if run.status == "succeeded" and run.output:
                        ref = await save_chat_output_document(worker_session, project_name=worker_chat.project_name, output=run.output, user=worker_user)
                        if ref:
                            chat_files = [ref]
                            await emit("trace", {"kind": "tool", "tool": "flowhub.doc.save", "status": "succeeded", "summary": {"summary": f"产出已归档为文档：{ref['name']}"}})
                    if not worker_deployment:
                        # 草稿测试会话：真实执行即视为通过测试（与 create_chat_message 同语义）
                        blockers = await validate_version(worker_session, worker_version)
                        worker_version.validation = {"ok": not blockers, "blockers": blockers}
                        worker_version.tested_at = now_iso()
                        worker_version.status = "testing"
                    assistant_message = ExpertChatMessage(id=new_id("msg"), session_id=session_id, run_id=run.id, sequence=sequence + 1, role="assistant", content=assistant_text, status=run.status, tool_trace=tool_trace, files=chat_files, created_at=now_iso())
                    worker_session.add(assistant_message)
                    worker_chat.updated_at = now_iso()
                    if worker_chat.title == "新会话":
                        worker_chat.title = body.content[:60]
                    await worker_session.commit()
                    done_payload: dict = {"message": chat_message_brief(assistant_message)}
                    if run.status != "interrupted":
                        done_payload["run"] = run_brief(run)
                    await queue.put(("done", done_payload))
                else:
                    streamed = False

                    async def on_token(token: str) -> None:
                        nonlocal streamed
                        streamed = True
                        await queue.put(("token", {"text": token}))

                    assistant_text, trace = await run_native_flowhub_chat(worker_session, body.content, worker_user, worker_chat.provider_model_id, history=history_text, on_token=on_token, on_trace=lambda item: emit("trace", item), project_name=worker_chat.project_name)
                    if not streamed:
                        await queue.put(("token", {"text": assistant_text}))
                    # 长文档型产出自动归档为项目文档，挂到消息 files（右侧产出文件栏可下载/预览）
                    chat_files = []
                    ref = await save_chat_output_document(worker_session, project_name=worker_chat.project_name, output=assistant_text, user=worker_user)
                    if ref:
                        chat_files = [ref]
                        await emit("trace", {"kind": "tool", "tool": "flowhub.doc.save", "status": "succeeded", "summary": {"summary": f"产出已归档为文档：{ref['name']}"}})
                    assistant_message = ExpertChatMessage(id=new_id("msg"), session_id=session_id, sequence=sequence + 1, role="assistant", content=assistant_text, status="completed", tool_trace=trace, files=chat_files, created_at=now_iso())
                    worker_session.add(assistant_message)
                    worker_chat.updated_at = now_iso()
                    if worker_chat.title == "新会话":
                        worker_chat.title = body.content[:60]
                    await worker_session.commit()
                    await queue.put(("done", {"message": chat_message_brief(assistant_message)}))
            except asyncio.CancelledError:
                await worker_session.rollback()
                raise
            except Exception as exc:
                await worker_session.rollback()
                logging.exception("聊天消息处理失败（session=%s）：生成失败也要落库留痕，避免历史断档", session_id)
                # 失败留痕：assistant 失败消息落库，刷新后历史可见（用户消息已先行提交，不能只剩半截对话）
                try:
                    failed = ExpertChatMessage(
                        id=new_id("msg"), session_id=session_id, sequence=sequence + 1,
                        role="assistant", content=f"生成失败：{type(exc).__name__}：{exc}",
                        status="failed", tool_trace=[], files=[], created_at=now_iso(),
                    )
                    worker_session.add(failed)
                    await worker_session.commit()
                except Exception:  # noqa: BLE001
                    await worker_session.rollback()
                if not producer_cancelled.is_set():
                    await queue.put(("error", {"message": f"消息处理失败：{type(exc).__name__}"}))
            finally:
                if not producer_cancelled.is_set():
                    await queue.put(None)

    async def events():
        producer = asyncio.create_task(produce())
        try:
            yield "event: trace\ndata: {\"kind\":\"model\",\"tool\":\"FlowHub 助手\",\"status\":\"running\",\"summary\":\"正在准备上下文\"}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                event, payload = item
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            # 正常收尾：producer 已 put None，等待其自然退出即可（不得 cancel，否则响应缺终止块）
            await asyncio.gather(producer, return_exceptions=True)
        except asyncio.CancelledError:
            # 客户端断开：主动终止 producer 并传播取消
            producer_cancelled.set()
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
            raise

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/expert-chat/sessions/{session_id}/messages")
async def clear_chat_messages(session_id: str, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    """清空会话消息（保留会话与绑定）。"""
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    await session.execute(delete(ExpertChatMessage).where(ExpertChatMessage.session_id == session_id))
    chat.updated_at = now_iso()
    await AuditService(session).record(actor=user.name, action="chat:clear", target=f"会话 {session_id[:12]}", result="success")
    await session.commit()
    return ok({"sessionId": session_id}, "会话消息已清空")


@router.delete("/expert-chat/sessions/{session_id}")
async def delete_chat_session(session_id: str, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    """删除会话（级联删除消息与 Run 记录保留）。"""
    chat = await session.get(ExpertChatSession, session_id)
    if not chat or chat.owner_id != user.id:
        raise BizError(BizCode.NOT_FOUND, "会话不存在")
    await session.delete(chat)
    await AuditService(session).record(actor=user.name, action="chat:delete", target=f"会话 {session_id[:12]}", result="success")
    await session.commit()
    return ok({"sessionId": session_id}, "会话已删除")


@router.get("/expert-runs/{run_id}")
async def get_run(run_id: str, session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]):
    run = await session.get(ExpertRun, run_id)
    if not run or run.requested_by != user.id:
        raise BizError(BizCode.NOT_FOUND, "Run 不存在")
    events = (await session.execute(select(ExpertRunEvent).where(ExpertRunEvent.run_id == run_id).order_by(ExpertRunEvent.sequence))).scalars().all()
    return ok({"run": run_brief(run), "events": [{"id": item.id, "kind": item.kind, "status": item.status, "title": item.title, "payload": item.payload, "durationMs": item.duration_ms} for item in events]})


@router.get("/expert-approvals")
async def list_approvals(session: Annotated[AsyncSession, Depends(get_db)], user: Annotated[User, Depends(get_current_user)], status: str = "pending"):
    statement = select(ExpertApproval)
    if status != "all":
        statement = statement.where(ExpertApproval.status == status)
    rows = (await session.execute(statement.order_by(ExpertApproval.created_at.desc()))).scalars().all()
    current = now_iso()
    # 过期待处理的审批单对外呈现为 expired，避免队列里仍渲染可点击的批准按钮
    normalized = [{"id": item.id, "runId": item.run_id, "action": f"执行 {item.tool_name}", "tool": item.tool_name, "expert": "", "risk": item.risk, "scope": item.scope, "requester": "", "requested": item.created_at, "expires": item.expires_at, "expiresAt": item.expires_at, "status": "expired" if item.status == "pending" and item.expires_at < current else item.status} for item in rows]
    return ok({"items": normalized})

@router.post("/expert-approvals/{approval_id}/{decision}")
async def decide_approval(approval_id: str, decision: str, body: ApprovalDecisionReq, user: Annotated[User, Depends(get_current_user)], session: Annotated[AsyncSession, Depends(get_db)]):
    if decision not in {"approve", "reject"}:
        raise BizError(BizCode.VALIDATION, "未知审批动作")
    build_authorizer(user).require("expert:approve")
    approval = await session.get(ExpertApproval, approval_id)
    if not approval or approval.status != "pending":
        raise BizError(BizCode.DUPLICATE_OPERATION, "审批不存在或已处理")
    if approval.expires_at < now_iso():
        approval.status = "expired"
        await session.commit()
        raise BizError(BizCode.DUPLICATE_OPERATION, "审批已过期")
    approval.status, approval.decided_by, approval.decision_note, approval.decided_at = ("approved" if decision == "approve" else "rejected"), user.id, body.note, now_iso()
    run = await session.get(ExpertRun, approval.run_id)
    if decision == "approve":
        await resume_approved_run(session, approval, user)
        # Expert 自动节点闭环：审批通过后继续生成的产出回填节点表单并自动流转；
        # 审批后模型生成失败 → 任务回退人工兜底
        if run is not None and run.task_id:
            from flowhub_api.models import TaskItem
            from flowhub_api.services.workflow import WorkflowService

            task = await session.get(TaskItem, run.task_id)
            if task is not None and task.status == "pending_confirmation":
                if run.status == "succeeded":
                    service = WorkflowService(session)
                    project, tpl = await service.resolve_template_for_task(task)
                    cfg = (await service._node_cfg_of(tpl, task.node_id) or {}) if project and tpl else {}
                    if cfg.get("handler") == "Expert 自动":
                        await service.ai_autosubmit(task, project, tpl, cfg, run, user)
                else:
                    task.status = "assigned"
    elif run:
        run.status, run.finished_at = "cancelled", now_iso()
    await AuditService(session).record(actor=user.name, action=f"expert:approval_{decision}", target=approval.id, result="success")
    if run and run.session_id:
        message = (await session.execute(select(ExpertChatMessage).where(ExpertChatMessage.run_id == run.id))).scalars().first()
        if message:
            message.status = run.status
            message.content = "审批已通过，运行已恢复完成。" if decision == "approve" else "审批已拒绝，运行已终止。"
    await session.commit()
    return ok({"approval": {"id": approval.id, "status": approval.status}}, "审批已处理")
