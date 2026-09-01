"""幂等迁移：既有表加列（无 alembic 前的轻量方案）。

新表由 `Base.metadata.create_all` 自动创建；本脚本只负责对**已有表**补列，
保证重复启动不报错（检查 information_schema 后仅补缺失列）。
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

async def _existing_columns(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
    ), {"t": table})
    return {r[0] for r in rows}


async def migrate(conn: AsyncConnection) -> None:
    """Apply compatibility migrations for active FlowHub tables only."""
    # Explicitly remove the retired Agent runtime tables. Expert Runtime owns
    # versions, runs, events, and approvals now; these tables have no consumers.
    for table in ("agent_confirm_requests", "agent_suggestions", "agent_invocations", "agent_capabilities", "agents", "agent_tools", "agent_models", "agent_types"):
        await conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    task_columns = await _existing_columns(conn, "tasks")
    if "agent_pending" in task_columns and "expert_pending" not in task_columns:
        await conn.execute(text('ALTER TABLE tasks RENAME COLUMN "agent_pending" TO "expert_pending"'))
        logger.info("migrate: retired task pending column renamed to expert_pending")
    # 子任务拆分与产出契约：父任务链接 / 子任务需求说明 / 验收勾选快照
    task_columns_to_add = {
        "parent_task_id": "VARCHAR(40)",
        "lineage_root_id": "VARCHAR(40)",
        "brief": "TEXT DEFAULT ''",
        "acceptance_checks": "JSON DEFAULT '{}'",
    }
    for column, ddl in task_columns_to_add.items():
        if task_columns and column not in task_columns:
            await conn.execute(text(f'ALTER TABLE tasks ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: tasks.%s added", column)
    # tasks.created_at：列表"最新创建"排序用（TaskItem 无任何时间列）；从 id 内嵌时间戳回填
    if task_columns and "created_at" not in task_columns:
        await conn.execute(text('ALTER TABLE tasks ADD COLUMN "created_at" VARCHAR(40) DEFAULT \'\''))
        await conn.execute(text(
            "UPDATE tasks SET created_at = to_char("
            "to_timestamp(substring(id from 3 for 14), 'YYYYMMDDHH24MISS'), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') "
            "WHERE created_at = '' AND id ~ '^T-[0-9]{20}-'"
        ))
        logger.info("migrate: tasks.created_at added & backfilled")
    expert_columns = await _existing_columns(conn, "experts")
    expert_columns_to_add = {
        "description": "TEXT DEFAULT ''",
        "owner_id": "VARCHAR(32)",
        "current_version_id": "VARCHAR(32)",
        "created_at": "VARCHAR(40) DEFAULT ''",
        "updated_at": "VARCHAR(40) DEFAULT ''",
    }
    for column, ddl in expert_columns_to_add.items():
        if column not in expert_columns:
            await conn.execute(text(f'ALTER TABLE experts ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: experts.%s added", column)
    version_columns = await _existing_columns(conn, "expert_versions")
    version_columns_to_add = {
        "provider_model_id": "VARCHAR(32)",
        "provider_snapshot": "JSON DEFAULT '{}'",
        "skills": "JSON DEFAULT '[]'",
        "knowledge_base_ids": "JSON DEFAULT '[]'",
        "tool_policies": "JSON DEFAULT '{}'",
        "validation": "JSON DEFAULT '{}'",
        "tested_at": "VARCHAR(40) DEFAULT ''",
        "created_by": "VARCHAR(32)",
        "created_at": "VARCHAR(40) DEFAULT ''",
    }
    for column, ddl in version_columns_to_add.items():
        if column not in version_columns:
            await conn.execute(text(f'ALTER TABLE expert_versions ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: expert_versions.%s added", column)
    deployment_columns = await _existing_columns(conn, "expert_deployments")
    for column, ddl in {
        "environment": "VARCHAR(16) DEFAULT 'test'",
        "alias": "VARCHAR(96) DEFAULT ''",
        "workflow_binding_json": "JSON NOT NULL DEFAULT '{}'",
        "provider_override_json": "JSON NOT NULL DEFAULT '{}'",
    }.items():
        if column not in deployment_columns:
            await conn.execute(text(f'ALTER TABLE expert_deployments ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: expert_deployments.%s added", column)
    provider_columns = await _existing_columns(conn, "llm_providers")
    if "max_context_tokens" not in provider_columns:
        await conn.execute(text('ALTER TABLE llm_providers ADD COLUMN "max_context_tokens" INTEGER DEFAULT 1000000'))
        logger.info("migrate: llm_providers.max_context_tokens added")
    chat_columns = await _existing_columns(conn, "expert_chat_sessions")
    if "provider_model_id" not in chat_columns:
        await conn.execute(text('ALTER TABLE expert_chat_sessions ADD COLUMN "provider_model_id" VARCHAR(32)'))
        logger.info("migrate: expert_chat_sessions.provider_model_id added")
    if "expert_version_id" not in chat_columns:
        await conn.execute(text('ALTER TABLE expert_chat_sessions ADD COLUMN "expert_version_id" VARCHAR(32)'))
        logger.info("migrate: expert_chat_sessions.expert_version_id added")
    if "compaction_sequence" not in chat_columns:
        await conn.execute(text('ALTER TABLE expert_chat_sessions ADD COLUMN "compaction_sequence" INTEGER DEFAULT 0'))
        logger.info("migrate: expert_chat_sessions.compaction_sequence added")
    if "compaction_summary" not in chat_columns:
        await conn.execute(text('ALTER TABLE expert_chat_sessions ADD COLUMN "compaction_summary" TEXT DEFAULT \'\''))
        logger.info("migrate: expert_chat_sessions.compaction_summary added")
    if "project_name" not in chat_columns:
        await conn.execute(text('ALTER TABLE expert_chat_sessions ADD COLUMN "project_name" VARCHAR(160)'))
        logger.info("migrate: expert_chat_sessions.project_name added")
    run_columns = await _existing_columns(conn, "expert_runs")
    if run_columns and "task_id" not in run_columns:
        await conn.execute(text('ALTER TABLE expert_runs ADD COLUMN "task_id" VARCHAR(40)'))
        logger.info("migrate: expert_runs.task_id added")
    message_columns = await _existing_columns(conn, "expert_chat_messages")
    if "tool_trace" not in message_columns:
        await conn.execute(text("ALTER TABLE expert_chat_messages ADD COLUMN \"tool_trace\" JSON DEFAULT '[]'"))
        logger.info("migrate: expert_chat_messages.tool_trace added")
    if "files" not in message_columns:
        await conn.execute(text("ALTER TABLE expert_chat_messages ADD COLUMN \"files\" JSON DEFAULT '[]'"))
        logger.info("migrate: expert_chat_messages.files added")
    key_columns = await _existing_columns(conn, "user_api_keys")
    if key_columns and "key_ciphertext" not in key_columns:
        await conn.execute(text('ALTER TABLE user_api_keys ADD COLUMN "key_ciphertext" VARCHAR(512) DEFAULT \'\''))
        logger.info("migrate: user_api_keys.key_ciphertext added")
    skill_columns = await _existing_columns(conn, "expert_skills")
    if skill_columns and "deleted" not in skill_columns:
        await conn.execute(text('ALTER TABLE expert_skills ADD COLUMN "deleted" BOOLEAN DEFAULT FALSE'))
        logger.info("migrate: expert_skills.deleted added")
    mcp_columns = await _existing_columns(conn, "mcp_servers")
    mcp_columns_to_add = {
        "description": "TEXT DEFAULT ''",
        "direction": "VARCHAR(16) DEFAULT 'outbound'",
        "transport": "VARCHAR(32) DEFAULT 'streamable-http'",
        "endpoint": "VARCHAR(512) DEFAULT ''",
        "auth_type": "VARCHAR(24) DEFAULT 'none'",
        "credentials": "VARCHAR(2048) DEFAULT ''",
        "status": "VARCHAR(16) DEFAULT 'unhealthy'",
        "health": "VARCHAR(64) DEFAULT '未检测'",
        "deleted": "BOOLEAN DEFAULT FALSE",
        "created_by": "VARCHAR(32) DEFAULT ''",
        "created_at": "VARCHAR(40) DEFAULT ''",
        "updated_at": "VARCHAR(40) DEFAULT ''",
    }
    for column, ddl in mcp_columns_to_add.items():
        if mcp_columns and column not in mcp_columns:
            await conn.execute(text(f'ALTER TABLE mcp_servers ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: mcp_servers.%s added", column)
    mcp_tool_columns = await _existing_columns(conn, "mcp_tools")
    mcp_tool_columns_to_add = {
        "server_id": "VARCHAR(32) DEFAULT ''",
        "name": "VARCHAR(160) DEFAULT ''",
        "description": "TEXT DEFAULT ''",
        "input_schema": "JSON DEFAULT '{}'",
        "risk": "VARCHAR(24) DEFAULT 'read'",
        "approval": "VARCHAR(24) DEFAULT 'none'",
        "status": "VARCHAR(24) DEFAULT 'discovered'",
        "enabled": "BOOLEAN DEFAULT TRUE",
        "created_at": "VARCHAR(40) DEFAULT ''",
        "updated_at": "VARCHAR(40) DEFAULT ''",
    }
    for column, ddl in mcp_tool_columns_to_add.items():
        if mcp_tool_columns and column not in mcp_tool_columns:
            await conn.execute(text(f'ALTER TABLE mcp_tools ADD COLUMN "{column}" {ddl}'))
            logger.info("migrate: mcp_tools.%s added", column)
    # notifications.wi_id / task_id：通知关联业务对象（点击通知跳转到对应工作项/任务）
    ntf_cols = await _existing_columns(conn, "notifications")
    if "wi_id" not in ntf_cols:
        await conn.execute(text('ALTER TABLE notifications ADD COLUMN "wi_id" VARCHAR(40) DEFAULT \'\''))
        logger.info("migrate: notifications.wi_id 已补充")
    if "task_id" not in ntf_cols:
        await conn.execute(text('ALTER TABLE notifications ADD COLUMN "task_id" VARCHAR(64) DEFAULT \'\''))
        logger.info("migrate: notifications.task_id 已补充")
    # work_items.title 不再全局唯一（去掉重复性校验）：移除唯一索引（保留普通标题搜索索引）
    title_idx = (await conn.execute(text(
        "SELECT indexdef FROM pg_indexes WHERE tablename='work_items' AND indexname='ix_work_items_title'"
    ))).first()
    if title_idx and "UNIQUE" in (title_idx[0] or "").upper():
        await conn.execute(text("DROP INDEX ix_work_items_title"))
        logger.info("migrate: work_items.title 唯一索引已移除（允许重复标题）")
    # instance_state 枚举增加 archived（项目归档 → 冻结进行中流程实例）
    has_archived = (await conn.execute(text(
        "SELECT 1 FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid "
        "WHERE t.typname = 'instance_state' AND e.enumlabel = 'archived'"
    ))).first()
    if has_archived is None:
        await conn.execute(text("ALTER TYPE instance_state ADD VALUE IF NOT EXISTS 'archived'"))
        logger.info("migrate: instance_state 枚举新增 archived")
    await conn.commit()
