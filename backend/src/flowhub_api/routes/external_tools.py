"""Downloadable external-agent artifacts for the FlowHub MCP surface."""
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession
from flowhub_api.db.session import get_db
from flowhub_api.models import UserApiKey
from flowhub_api.services.crypto import decrypt_secret
from flowhub_api.core.response import BizCode, BizError
from sqlalchemy import select

from flowhub_api.authz.authorizer import get_current_user
from flowhub_api.models import User

router = APIRouter(prefix="/api/v1/external-tools", tags=["external-tools"])


def _origin() -> str:
    from flowhub_api.core.config import get_settings

    return get_settings().public_base_url.rstrip("/") or "http://127.0.0.1:8000"


@router.get("/mcp-config")
async def download_mcp_config(_: Annotated[User, Depends(get_current_user)], key_id: str = "", session: AsyncSession = Depends(get_db)):
    access_key = "${FLOWHUB_ACCESS_KEY}"
    if key_id:
        key = await session.scalar(select(UserApiKey).where(UserApiKey.id == key_id, UserApiKey.user_id == _.id, UserApiKey.status == "active"))
        if not key or not key.key_ciphertext:
            raise BizError(BizCode.NOT_FOUND, "指定的 access key 不存在或不可用")
        access_key = decrypt_secret(key.key_ciphertext)
    content = (
        "{\n"
        "  \"mcpServers\": {\n"
        "    \"flowhub\": {\n"
        f"      \"url\": \"{_origin()}/api/v1/mcp/sse\",\n"
        "      \"headers\": {\n"
        f"        \"Authorization\": \"Bearer {access_key}\"\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )
    return Response(content, media_type="application/json", headers={"Content-Disposition": "attachment; filename=flowhub-mcp.json"})


@router.get("/skill-markdown")
async def download_skill_markdown(_: Annotated[User, Depends(get_current_user)]):
    content = f"""# FlowHub MCP 操作 Skill

使用 FlowHub MCP Server 查询当前用户被授权访问的任务、工作项和文档。

## Connection

- MCP SSE endpoint: `{_origin()}/api/v1/mcp/sse`
- Authentication: `Authorization: Bearer ${{FLOWHUB_ACCESS_KEY}}`
- Create and rotate access keys in FlowHub's Access Key management page. Do not put an access key into this file.

## Available Read Operations

- `list_my_tasks`: list the caller's tasks; administrators can see all tasks.
- `get_task`: read an authorized task and current-node form values.
- `get_task_context`: retrieve authorized workflow context and associated documents.
- `get_work_item`: read an authorized work item and task summary.
- `list_documents`: list authorized document metadata.
- `get_downstream_summary`: view downstream task summaries.

## Safety Rules

1. Treat all FlowHub data as permission-scoped.
2. Do not claim that a write was performed through this read-only MCP surface.
3. Ask the user before using data outside FlowHub.
"""
    return Response(content, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": "attachment; filename=flowhub-mcp-skill.md"})
