# FlowHub Backend（已搭建骨架）

FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL + Redis + MinIO。接口契约见 [docs/](./docs/)。

## 快速开始

```bash
# 1. 复制配置并填写连接信息（PostgreSQL / Redis / MinIO）
cp .env.example .env

# 2. 安装依赖（已装过可跳过）
python -m venv .venv
.venv/bin/pip install -e .

# 3. 启动（首次启动自动建表 + seed：9 角色 / 34 权限点 / 12 用户 / 3 模板）
.venv/bin/uvicorn flowhub_api.main:app --port 8000
```

- Swagger 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>
- 演示账号（seed 用户）：`zhang.wei` / `Demo@1234` 等 12 人

## 已实现（对齐 docs/02）

| 模块    | 前缀                    | 端点                                     |
| ----- | --------------------- | -------------------------------------- |
| 认证    | /api/v1/auth          | 登录 / 免登占位 / 注册 / 改密 / me               |
| 组织    | /api/v1/org           | 概览 / 用户 CRUD / 角色技能分配 / 解锁 / 角色解析 / 同步 |
| 权限矩阵  | /api/v1/matrix        | 角色 CRUD / 权限调整 / 成员分配 / 防自锁            |
| 项目    | /api/v1/projects      | 列表 / 详情 / 创建 / 更新（模板绑定+节点处理人）/ 归档 / 取消 |
| 模板    | /api/v1/templates     | 模板池 / 版本 / 画布读取/保存 / 发布校验              |
| 工作项   | /api/v1/work-items    | 列表 / 详情 / 新建（发起流程，标题去重）                |
| 任务    | /api/v1/tasks         | 列表 / 详情 / 候选处理人（绑定解析）/ 提交/退回/转办/认领     |
| Agent | /api/v1/agents        | 列表+KPI / 能力边界 / 注册（密钥一次性）/ 绑定 / 状态     |
| 文档    | /api/v1/documents     | 列表分页 / 上传校验链 / 短时链接 / 删除恢复             |
| 通知    | /api/v1/notifications | 列表 / 已读 / 重试                           |
| 审计    | /api/v1/audits        | 分页查询 / 导出（导出记审计）                       |
| 看板    | /api/v1/dashboard     | 聚合概览                                   |
| 搜索    | /api/v1/search        | 工作项 / 任务 / 文档                          |

## 待接入（连接信息）

- [x] `.env` 填写 PostgreSQL 连接（当前启动自动降级跳过建表/seed）
- [x] `.env` 填写 Redis（惰性连接，已封装客户端）
- [x] `.env` 填写 MinIO（惰性连接，上传自动入库）
- [ ] 钉钉 / 企微免登验签（`/auth/sso/verify` 当前为演示映射）
