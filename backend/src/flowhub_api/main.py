"""FlowHub Backend 入口：装配路由 / 生命周期 seed / 统一响应与异常。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError, fail
from flowhub_api.db.migrate import migrate
from flowhub_api.db.session import SessionFactory, engine
from flowhub_api.models import Base
from flowhub_api.routes import (
    access_keys, audits, auth, dashboard, documents, matrix, notifications,
    org, projects, repos, search, tags, tasks, templates, workitems, experts, external_tools,
)
from flowhub_api.seed.init import seed_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("flowhub_api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动：建表 + 幂等 seed（连接信息未配置时优雅降级并提示）。"""
    settings = get_settings()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate(conn)
        async with SessionFactory() as session:
            await seed_all(session)
            from flowhub_api.services.runtime_config import load_runtime_config
            await load_runtime_config(session)
        from flowhub_api.services.expert_runtime import setup_checkpointer
        await setup_checkpointer()
        from flowhub_api.services.repo_mirror import schedule_all_mirror_builds
        await schedule_all_mirror_builds()  # 启动预热：为绑定仓库的后台任务自愈重建镜像（不阻塞）
        logger.info("数据库初始化完成（PostgreSQL 已连接）")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "数据库连接失败（%s）——请将 PostgreSQL 连接信息填入 backend/.env 后重启。"
            "Redis / MinIO 为惰性连接，不影响启动。", exc,
        )
    yield
    await engine.dispose()


app = FastAPI(
    title="FlowHub Backend API",
    version="0.1.0",
    description="FlowHub 流程协同平台后端（docs 见 backend/docs）",
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BizError)
async def biz_error_handler(_: Request, exc: BizError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.biz_code, exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=fail(BizCode.VALIDATION, str(exc.errors()[:2])))


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception):
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(status_code=500, content=fail(BizCode.INTERNAL, "内部错误"))


@app.get("/")
async def root():
    return {"name": "FlowHub Backend API", "docs": "/docs", "health": "/health"}


@app.get("/health")
async def health():
    return {"status": "ok"}


for r in (auth, org, projects, templates, workitems, tasks, matrix, access_keys, experts, external_tools, documents, notifications, audits, dashboard, search, tags):
    app.include_router(r.router)

# 代码仓库：连接管理 / 全局仓库列表 / 项目绑定（单模块三组前缀）
app.include_router(repos.router)
app.include_router(repos.repos_router)
app.include_router(repos.project_router)

# 外部 Agent 接入：MCP SSE server（/api/v1/mcp/sse，access key 认证）
from flowhub_api.services.mcp_server import mcp_starlette_app
app.mount("/api/v1/mcp", mcp_starlette_app())
