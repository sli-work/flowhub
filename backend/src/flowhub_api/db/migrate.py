"""幂等迁移：既有表加列（无 alembic 前的轻量方案）。

新表由 `Base.metadata.create_all` 自动创建；本脚本只负责对**已有表**补列，
保证重复启动不报错（检查 information_schema 后仅补缺失列）。
"""
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# 表 → 需要保证存在的列（SQL 类型 / 默认值）
_AGENT_COLUMNS: dict[str, str] = {
    "engine": "VARCHAR(16) DEFAULT 'opencode'",
    "provider": "VARCHAR(32) DEFAULT ''",
    "model": "VARCHAR(64) DEFAULT ''",
    "agent_type": "VARCHAR(32) DEFAULT ''",
    "secret_hash": "VARCHAR(128) DEFAULT ''",
    "api_key": "VARCHAR(512) DEFAULT ''",
    "base_url": "VARCHAR(255) DEFAULT ''",
    "system_prompt": "TEXT DEFAULT ''",
    "tool_id": "VARCHAR(32) DEFAULT ''",
}


async def _existing_columns(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"
    ), {"t": table})
    return {r[0] for r in rows}


async def migrate(conn: AsyncConnection) -> None:
    """在 create_all 之后、seed 之前调用。已有表补列：agents 引擎列 + agent_models.desc。"""
    existing = await _existing_columns(conn, "agents")
    for col, ddl in _AGENT_COLUMNS.items():
        if col in existing:
            continue
        await conn.execute(text(f'ALTER TABLE agents ADD COLUMN "{col}" {ddl}'))
        logger.info("migrate: agents.%s 已补充", col)
    # agent_models.desc：模型描述（可视化创建 Agent 时展示）
    if "desc" not in await _existing_columns(conn, "agent_models"):
        await conn.execute(text('ALTER TABLE agent_models ADD COLUMN "desc" VARCHAR(255) DEFAULT \'\''))
        logger.info("migrate: agent_models.desc 已补充")
    # agent_models.base_url：模型默认 OpenAI 兼容 endpoint
    if "base_url" not in await _existing_columns(conn, "agent_models"):
        await conn.execute(text('ALTER TABLE agent_models ADD COLUMN "base_url" VARCHAR(255) DEFAULT \'\''))
        logger.info("migrate: agent_models.base_url 已补充")
    # agent_types.system_prompt：类型默认系统提示词模板
    if "system_prompt" not in await _existing_columns(conn, "agent_types"):
        await conn.execute(text('ALTER TABLE agent_types ADD COLUMN "system_prompt" TEXT DEFAULT \'\''))
        logger.info("migrate: agent_types.system_prompt 已补充")
    type_cols = await _existing_columns(conn, "agent_types")
    for col, ddl in {"provider": "VARCHAR(32) DEFAULT ''", "model": "VARCHAR(64) DEFAULT ''"}.items():
        if col not in type_cols:
            await conn.execute(text(f'ALTER TABLE agent_types ADD COLUMN "{col}" {ddl}'))
            logger.info("migrate: agent_types.%s 已补充", col)
    if "models" not in await _existing_columns(conn, "agent_tools"):
        await conn.execute(text("ALTER TABLE agent_tools ADD COLUMN \"models\" JSONB DEFAULT '[]'"))
        logger.info("migrate: agent_tools.models 已补充")
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
