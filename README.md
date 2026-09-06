# FlowHub

FlowHub 是一个面向企业流程协作的前后端分离应用。它将项目、流程模板、工作项、任务、文档和可配置的 Expert Agent 汇集在同一工作台，支持 AI 生成、人工审批与可追溯的自动流转。

```
flowhub/
├── frontend/   # Vite + React 19 + TypeScript 前端
├── backend/    # FastAPI + SQLAlchemy + PostgreSQL 后端
└── docs/       # Expert 架构、交互设计与质量评测说明
```

## 主要能力

- 项目、流程模板、工作项与任务的全流程协作，含权限、处理人解析、通知和审计。
- Expert Agent 的版本、部署、运行记录、人工审批与结构化结果采纳。
- 流式 Expert Chat：会话互斥、长对话摘要压缩、模型上下文预算和可选的产出文件保存。
- Expert Run 的持久化调度、短租约心跳、重启恢复和幂等的自动流转，避免重复提交任务。
- 代码仓库镜像及受限只读工具调用；代码类回答必须基于仓库证据并附带定位引用。
- 模型级上下文/输出上限，以及覆盖完整长输出的质量核验与人工复核兜底。

## 快速开始

### 1. 启动后端

后端依赖 PostgreSQL、Redis 和 MinIO。复制配置后填写连接信息：

```bash
cd backend
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/uvicorn flowhub_api.main:app --reload --port 8000
```

服务启动后可访问：

- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

首次启动会自动建表并写入演示数据。详细接口和环境配置见 [backend/README.md](backend/README.md) 与 [backend/docs/README.md](backend/docs/README.md)。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认开发地址为 <http://127.0.0.1:5173>。如后端不在默认地址，请按实际部署配置前端 API 地址。

## 常用验证命令

```bash
# 后端测试
cd backend && .venv/bin/pytest

# 前端测试、类型检查与生产构建
cd frontend && npm test
cd frontend && npm run build
```

与 Expert 准确性有关的发布门槛、固定样本和指标说明见 [docs/langgraph-accuracy-evals.md](docs/langgraph-accuracy-evals.md)。

## 文档导航

- [后端接口与领域文档](backend/docs/README.md)
- [Expert OS 架构](docs/expert-os-architecture.md)
- [Expert Run 抽屉交互设计](docs/expert-run-drawer-design.md)
- [LangGraph 准确性回归评测](docs/langgraph-accuracy-evals.md)
- [测试计划](TEST_PLAN.md)
