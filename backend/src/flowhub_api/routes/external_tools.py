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

使用 FlowHub MCP Server 查询并处理当前用户被授权的任务、工作项和文档。

## Connection

- MCP SSE endpoint: `{_origin()}/api/v1/mcp/sse`
- Authentication: `Authorization: Bearer ${{FLOWHUB_ACCESS_KEY}}`
- Create and rotate access keys in FlowHub's Access Key management page. Do not put an access key into this file.

## Read Operations

- `list_my_tasks`: list the caller's tasks; administrators can see all tasks.
- `get_task`: read an authorized task, current-node form values, **form schema** and acceptance checklist.
- `get_task_context`: retrieve authorized workflow context (previous-node forms + documents).
- `get_work_item`: read an authorized work item and task summary.
- `list_documents`: list authorized document metadata.
- `get_downstream_summary`: view downstream task summaries.

## Write Operations

- `create_work_item`: create and start a new work item in an authorized project/template binding.
- `claim_task`: claim a transferred/assigned task.
- `create_document`: save Markdown content as a FlowHub document (for upload-type form fields).
- `submit_task`: fill the node form and submit, advancing the workflow to the next node.

## Task Processing Workflow（处理任务）

1. `list_my_tasks` 找到 assigned/transferred 状态的任务（转办来的任务为 transferred）;
1.5. transferred 任务先 `claim_task <task_id>` 认领；
2. `get_task_context <task_id>` 获取完整上下文（工作项信息 + 前序节点表单明细 + 文档）；
3. `get_task <task_id>` 查看当前节点的 `formSchema`（待填字段：key/type/required）与 `acceptance`（验收标准）；
4. upload 类型字段：用 `create_document(name, content, wi_id)` 把 Markdown 产出保存为文档，拿到 `{{id, name}}` 引用；
5. `submit_task(task_id, form_values, acceptance_checks)` 提交：
   - `form_values` 按 formSchema 的 key 组织，upload 字段传 `[{{"id": ..., "name": ...}}]`；
   - `acceptance_checks` 逐项 `{{"<key>": {{"text": "<标准原文>", "checked": true}}}}`，验收标准必须全部勾选，否则校验失败；
6. 返回 `nextNode` / `nextAssignees` 表示已流转；`waitingJoin: true` 表示并行汇合等待其他分支。

## Work Item Creation（创建工作项）

1. 只有具有 `workflow_instance:create` 权限、且是起始节点处理人（或管理员）的调用者可创建；
2. 调用 `create_work_item(project_id, template_id, start_values, labels)`；项目必须启用且已绑定模板；
3. `start_values.title` 必填，其余字段应遵循模板起始节点的表单 Schema；
4. 这是会创建真实流程、任务和通知的写入操作。项目、模板或表单信息不明确时，先向用户确认，勿猜测默认值。

## Safety Rules

1. Treat all FlowHub data as permission-scoped: only the task assignee or admins may submit.
2. Fill every required schema field before calling `submit_task`; failed validation returns `{{"error": ...}}`.
3. Ask the user before submitting on their behalf when the form content is ambiguous.
"""
    return Response(content, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": "attachment; filename=flowhub-mcp-skill.md"})
