# FlowHub 系统测试计划（Test Plan）

| 项目 | 内容 |
|---|---|
| 被测系统 | FlowHub 流程协同平台（前后端分离） |
| 被测版本 | backend 0.1.0 / frontend 0.0.0（工作区 HEAD，2026-08-22） |
| 测试类型 | 系统测试（System Test）：接口功能 / 权限安全 / 流程闭环 / 构建冒烟 / 性能冒烟 |
| 测试时间 | 2026-08-22 |
| 测试执行 | pytest 8.x（后端）+ TypeScript/Vite 构建检查（前端）+ 并发冒烟（性能） |

---

## 1. 被测对象与架构

```
frontend/  Vite 7 + React 19 + TS + Tailwind 3 + shadcn/ui（纯 mock 前端已对接后端 API）
backend/   FastAPI + SQLAlchemy 2.0(async) + PostgreSQL + Redis + MinIO
           ├─ src/flowhub_api/
           │   ├─ routes/   14 个路由模块（auth/org/matrix/projects/templates/workitems/
           │   │             tasks/documents/agents/notifications/audits/dashboard/search/access_keys）
           │   ├─ services/ workflow 引擎、agent_runner、auth、audit、crypto、mcp_server
           │   ├─ authz/    RBAC 权限判定（9 角色 × 34 权限点，防自锁）
           │   ├─ models/   23 张表的 ORM 模型
           │   └─ seed/     幂等初始化（骨架 + 可选演示数据 FLOWHUB_SEED_DEMO=1）
           └─ docs/         领域模型 / REST API 契约 / 认证权限 / 工作流 / Agent / 审计文档
```

- 统一响应契约：`{ code, message, data }`；错误码 0/40001/40101/40301/40302/40401/40901/40902/42201/42301/50000
- 鉴权：`Authorization: Bearer <JWT>`（HS256，默认 720 分钟过期）
- RBAC：多角色权限点并集；写操作强制审计；防自锁（system_admin 不可降权 user_manage）

## 2. 测试范围

| 范围 | 内容 | 策略 |
|---|---|---|
| 后端接口 | 14 个路由模块全部端点（约 60 个路由） | pytest + FastAPI TestClient（真实 PostgreSQL） |
| 业务闭环 | 项目→模板→工作项→任务流转（认领/转办/退回/提交推进/闭环） | 端到端接口流 |
| 权限安全 | 未认证/伪 token/越权/防自锁/待审批登录/敏感信息泄露 | 专项用例 |
| 参数健壮性 | 缺参/非法枚举/重复提交/不存在资源/去重拦截 | 边界用例 |
| 前端 | TypeScript 编译、生产构建、ESLint、产物可访问性 | npm run build / lint / 静态冒烟 |
| 性能 | 关键读接口并发冒烟（p50/p95/p99） | 并发脚本 |
| 兼容性 | OpenAPI 契约完整性（关键路径存在性） | 契约断言 |

**不纳入本轮**：真实钉钉/企微免登（未接入，返回占位提示）、真实模型调用（Agent invoke 为边界验证）、MinIO 文件流（环境受限时降级仅元数据）、压测（仅冒烟）。

## 3. 测试环境

| 项 | 配置 |
|---|---|
| OS | macOS（darwin） |
| Python | 3.13（backend/.venv，项目自带） |
| PostgreSQL | 127.0.0.1:5432（测试库 `flowhub_test`，独立于开发库） |
| Redis / MinIO | 192.168.21.4（惰性连接，不可达自动降级） |
| 数据准备 | 每次会话 DROP SCHEMA 重置 + seed 演示数据（9 角色/12 用户/2 模板/9 项目/8 工作项/7 任务/10 文档/8 Agent/12 通知/14 审计） |
| 测试账号 | admin/Admin@123456（bootstrap）；zhang.wei、liting、sunlin、wufan/Demo@1234（seed） |

## 4. 测试用例设计（按模块）

| 模块 | 重点用例 | 用例数 |
|---|---|---|
| 基础健康 | / /health /docs /openapi.json /mcp/sse | 5 |
| 认证 Auth | 登录成败/锁定423/停用403/注册/审批/改密/me/SSO占位 | 18 |
| 组织 Org | 概览/列表过滤/用户 CRUD/解锁/角色解析/同步 | 12 |
| 权限矩阵 Matrix | 角色 CRUD/复制/防自锁/成员分配 | 11 |
| 项目 Project | CRUD/编码唯一/模板绑定/归档只读/取消 | 12 |
| 模板与画布 | 模板池/版本补齐/画布读写/发布校验 6 规则 | 13 |
| 工作项 | 列表过滤/详情/发起流程/标题去重/未绑定拦截 | 10 |
| 任务 | 列表/详情/上下文/候选处理人/认领/转办/退回/提交推进/闭环/幂等 | 18 |
| 文档 | 列表分页/上传校验链(扩展名/MIME/穿越)/短时链接/删除恢复 | 10 |
| Agent | 列表KPI/能力边界/注册密钥一次性/绑定/状态/调用分流/access keys | 15 |
| 通知 | 列表/已读/重试 | 5 |
| 审计 | 分页/过滤/导出（导出记审计）/权限 | 6 |
| 看板+搜索 | 概览结构/权限/搜索 | 6 |
| 安全专项 | 认证绕过/越权/敏感泄露/XSS 探测/CORS | 12 |
| **合计** | | **≈ 153**（实际执行 200，含参数化分支） |

### 优先级划分
- P0（阻断发布）：认证、权限、流程闭环、去重/幂等、防自锁
- P1（核心功能）：CRUD、流转动作、文档校验链、审计
- P2（体验/边界）：过滤分页、占位端点、提示语

## 5. 准入 / 准出标准

**准入**：代码可导入启动；PostgreSQL 可达；测试库可重置；seed 幂等。

**准出**：
- 用例执行率 100%；P0 缺陷 0 未关闭
- 通过率 ≥ 95%（缺陷标记 xfail 单列统计，不计入失败）
- 遗留缺陷全部记录缺陷清单，P1 及以上必须有规避方案

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 测试库与开发库混用 | 数据污染 | 独立 `flowhub_test` + 会话级 DROP SCHEMA |
| MinIO/Redis 不可达 | 文档存储链路无法全测 | 惰性降级验证 + 元数据链路覆盖 |
| BizError 默认状态码偏差 | 契约不一致（BUG-A） | 断言业务码精确 + 状态码放宽，缺陷单列 |
| 前端无单测框架 | 组件行为覆盖不足 | 以构建/lint/产物冒烟 + 接口闭环间接覆盖 |
| 性能数据受 DEBUG=true 影响 | 绝对值偏高 | 仅作相对冒烟，标注环境 |

---

*本计划随测试执行结果回填为《FlowHub 系统测试报告》。*
