# FlowHub 部署说明（Docker）

## 一、镜像内已内置 opencode

- 后端镜像（`backend/Dockerfile`）基于 `python:3.13-slim`，构建时通过官方脚本
  `curl -fsSL https://opencode.ai/install | bash` 安装 opencode CLI 到 `/root/.opencode/bin`。
- 容器内 `opencode` 可直接执行（`ENV PATH` 已导出），Agent「客户端工具」的 opencode
  引擎在容器内以 CLI 方式运行。

### opencode 认证（重要）

opencode 需要登录态或 API Key 才能调用模型。容器内两种方式：

1. **登录（交互式）**：`docker exec -it <backend容器> opencode login`，按提示选择 provider 登录。
2. **API Key（env）**：opencode 读取标准环境变量（如 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY`），
   在 compose 的 `backend.environment` 中注入即可。

> 注意：镜像构建时无任何凭据；首次调用模型前必须完成上述任一种认证。

## 二、一键启动

```bash
cd backend/deploy
cp .env.example .env      # 可选：自定义 SECRET_KEY / BOOTSTRAP_ADMIN_PASSWORD 等
docker compose up -d --build
```

- 依赖服务：PostgreSQL 16 / Redis 7 / MinIO（均带 healthcheck，backend 等待就绪后启动）
- 后端：http://localhost:8001 （health：`/health`）
- MinIO 控制台：http://localhost:9001 （flowhub / flowhub-minio）

## 三、初始化与验证

```bash
# 查看日志（首次启动自动建表 + seed：角色/模板/Agent 类型/opencode 工具）
docker compose logs -f backend

# 验证健康
curl http://localhost:8001/health

# 登录拿 token
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"account":"admin","password":"Admin@123456"}'
```

## 四、环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `SECRET_KEY` | JWT 签名密钥 | change-me（生产必改） |
| `AGENT_KEY_ENCRYPT_SECRET` | Agent API Key 加密密钥（Fernet 派生）；**变更后旧 Key 无法解密** | 空（回退 SECRET_KEY） |
| `CORS_ORIGINS` | 允许的前端来源 | http://localhost:5273,http://localhost:5173 |
| `PUBLIC_BASE_URL` | 用户及 MCP 客户端可访问的 FlowHub 根地址；用于 MCP JSON / Skill 下载，生产环境必须显式配置 | https://flowhub.example.com |
| `POSTGRES_*` | PostgreSQL 连接（compose 内指向服务名 postgres） | flowhub/flowhub@postgres:5432/flowhub |
| `REDIS_*` | Redis 连接 | redis:6379 |
| `MINIO_*` | MinIO 连接 | minio:9000 / flowhub / flowhub-minio |
| `BOOTSTRAP_ADMIN_*` | 引导管理员账号 | admin / Admin@123456 |
| `OPENCODE_TIMEOUT` | opencode 调用超时（秒） | 180 |

## 五、前端

前端为 Vite 构建产物，可由任意静态服务器托管（如 nginx），或本地 `npm run dev -- --port 5273` 开发调试。
后端 CORS 已默认放行 5273/5173。

## 六、说明

- 无 alembic 迁移：启动时 `create_all` 建新表 + `migrate.py` 幂等补列，重复启动安全。
- 演示数据（12 用户/项目/工作项等）默认关闭；如需开启设置环境变量 `FLOWHUB_SEED_DEMO=1`。
