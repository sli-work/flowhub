# FlowHub Expert OS 架构方案

## 1. 文档状态

本文是 FlowHub 从当前 Agent 管理升级为 Expert OS 的目标架构与迁移契约。本文描述的是待实施的目标能力，不表示所有组件已经存在。

## 2. 目标与边界

### 2.1 建设目标

- 使用 Expert 工作台替代当前 Agent 管理产品界面。
- 内置 Expert 与自建 Expert 均使用 LangGraph Runtime 执行。
- Expert 支持绑定版本化的内部 Expert Skill。
- 内部 Expert Skill 支持调用 FlowHub 内置工具与已批准的外部 MCP Tool。
- 用户不选择 Expert 时，也可以通过 LangGraph 对话直接操作其有权限的 FlowHub 系统能力。
- FlowHub 继续作为 MCP Server 对外提供系统操作能力。
- 保留面向外部智能体的 FlowHub 操作 Skill 下载能力，但它不进入内部 Expert Skill Registry。
- 迁移期间复用现有 Provider、工作流、审批、审计、任务和文档基础能力，再逐步归属到新模型。

### 2.2 第一阶段不做的内容

- 暂不实现多租户隔离。后续添加 `tenant_id` 时，模型和查询不能形成不可迁移的耦合。
- 不实现 Expert、Skill、MCP Marketplace。
- 不允许高风险业务动作自动执行。写入和关键动作必须经过策略及审批。
- 外部智能体不能读取内部 Expert Skill，也不获得内部 Expert Runtime 的权限。
- 第一版聊天会话只允许选择零个或一个 Expert Deployment；多 Expert 协作在明确委派、冲突解决和权限合并策略后单独设计。

## 3. 核心概念

| 概念 | 定义 |
|---|---|
| Expert | 业务身份和能力组合，包含提示词、模型配置、Skill、可用工具、知识、记忆及策略。 |
| ExpertVersion | Expert 的不可变发布快照，用于运行复现和版本追溯。 |
| ExpertDeployment | 可运行的 Expert 实例，可绑定工作流节点并具有生命周期和运行指标。 |
| Expert Skill | 内部专业能力包，包含指令、输入输出约束、可选 LangGraph 子图、策略和可用工具。 |
| FlowHub 操作 Skill | 页面下载的 Markdown 指令，供外部智能体连接 FlowHub MCP。它不是内部 Expert Skill。 |
| FlowHub Native Tool | 内部 LangGraph 直接调用的 FlowHub 领域服务适配器，不通过回调自身 HTTP/MCP 接口。 |
| 外部 MCP Tool | 从已注册的出站 MCP Server 发现并经批准后可使用的 Tool。 |
| Policy | 对 Expert、Skill、Tool、任务、项目、节点、文档与审批状态进行约束的统一策略。 |

核心关系：

```text
Expert = 业务身份 + 策略 + 专业能力组合
Expert Skill = 专业指令 + 子图 + 工具约束
MCP Tool = 平台或外部系统能力
LangGraph = 编排、执行、中断与恢复运行时
Expert Deployment = 工作流或聊天中实际可运行的实例
```

## 4. 产品信息架构

### 4.1 Expert 工作台

当前“Agent 管理”页面替换为：

```text
Expert 工作台
├── Expert 中心
│   ├── 内置 Expert
│   ├── 自建 Expert
│   ├── 草稿、测试、发布、停用、归档
│   ├── 版本对比
│   └── Deployment 与工作流节点绑定
├── Expert Skill 中心
│   ├── Skill 定义
│   ├── 版本、测试、发布、弃用
│   └── Expert 绑定
├── MCP 中心
│   ├── FlowHub 内置工具目录
│   ├── 外部 MCP Server
│   ├── 工具发现、健康检查、凭据与审批
│   └── Expert/Skill Tool 绑定
├── Provider 中心
│   └── OpenCode 与 API Provider、模型配置
├── 知识库
│   └── 集合、文档、分块、检索来源
├── Memory
│   └── Expert、任务、运行级命名空间、留存与审计
└── 运行中心
    ├── LangGraph Run 与 Trace
    ├── Tool Call
    ├── 审批队列
    └── 重试与失败诊断
```

### 4.2 LangGraph 对话工作台

新增可直接与 LangGraph 对话的页面：

```text
LangGraph 对话工作台
├── 左侧：会话列表
├── 中间：对话、引用、工具调用过程
├── 右侧：运行配置
│   ├── Expert 选择器
│   ├── Expert Skill 选择器
│   ├── Provider / Model 选择器
│   ├── 当前可用工具
│   └── 当前权限与审批状态
└── 底部：消息输入与附件
```

未选择 Expert 的会话默认加载 FlowHub Native Tool。选择 Expert 后，在基础系统能力之上叠加 ExpertVersion、已发布 SkillVersion、批准的外部 MCP Tool、知识库和记忆策略。

## 5. 领域模型

### 5.1 Expert 与 Deployment

```text
Expert
  id
  slug
  name
  kind                 builtin | custom
  owner_user_id
  status               draft | testing | published | suspended | archived
  current_version_id
  created_at
  updated_at

ExpertVersion
  id
  expert_id
  version
  status               draft | testing | published | deprecated
  system_prompt
  output_contract_json
  execution_profile_id
  knowledge_policy_json
  memory_policy_json
  policy_json
  checksum
  published_by
  published_at

ExpertDeployment
  id
  expert_version_id
  name
  status               pending | active | suspended | revoked | archived
  workflow_binding_json
  provider_override_json
  created_by
  created_at
```

ExpertVersion 发布后不可修改。ExpertDeployment 必须指向一个明确的已发布版本，使运行记录可复现。

### 5.2 内部 Expert Skill

```text
Skill
  id
  slug
  name
  description
  owner_user_id
  status               draft | published | archived
  current_version_id

SkillVersion
  id
  skill_id
  version
  status               draft | testing | published | deprecated
  instructions
  input_schema_json
  output_schema_json
  subgraph_definition_json
  policy_json
  checksum
  published_by
  published_at

ExpertSkillBinding
  expert_version_id
  skill_version_id
  enabled
  sort_order
  configuration_json
```

内部 Expert Skill 是专业指令、工具契约、可选 LangGraph 子图和策略限制的唯一权威来源。

### 5.3 MCP Server 与 Tool Registry

```text
McpServer
  id
  slug
  name
  direction            native | inbound | outbound
  transport            in_process | sse | streamable_http | stdio
  endpoint
  credential_ref
  status               draft | active | unhealthy | disabled
  health_checked_at
  metadata_json

McpTool
  id
  mcp_server_id
  external_name
  display_name
  description
  input_schema_json
  output_schema_json
  risk_level           read | generate | write_draft | write_commit | critical
  approval_policy      none | required | administrator_only
  status               discovered | approved | disabled
  metadata_json

SkillMcpToolBinding
  skill_version_id
  mcp_tool_id
  enabled
  configuration_json

ExpertMcpToolBinding
  expert_version_id
  mcp_tool_id
  enabled
  configuration_json
```

方向定义：

- `native`：FlowHub 内部领域服务适配器。
- `inbound`：面向外部智能体暴露的 FlowHub MCP Server。
- `outbound`：供内部 Expert 调用的外部 MCP Server。

### 5.4 知识与 Memory

```text
KnowledgeBase
  id
  name
  description
  status
  policy_json

KnowledgeDocument
  id
  knowledge_base_id
  source_document_id
  status
  version
  metadata_json

KnowledgeChunk
  id
  knowledge_document_id
  ordinal
  content
  embedding
  source_locator
  sensitivity

MemoryEntry
  id
  namespace            expert:{id} | task:{id} | run:{id}
  owner_kind
  owner_id
  content
  metadata_json
  sensitivity
  expires_at
  status
  created_by
```

首版知识检索使用 PostgreSQL 和 pgvector。任何注入 LangGraph 状态的知识结果必须保留来源定位信息，并在对话和结果中可追溯引用。

### 5.5 对话、运行与审批

```text
LangGraphSession
  id
  user_id
  selected_expert_deployment_id nullable
  selected_skill_versions_json
  provider_override_json
  status
  created_at

LangGraphRun
  id
  session_id nullable
  expert_version_id nullable
  deployment_id nullable
  task_id nullable
  work_item_id nullable
  node_id nullable
  authorized_user_id
  graph_state_json
  status               queued | running | interrupted | succeeded | failed | cancelled
  trace_id
  started_at
  finished_at

ToolInvocation
  id
  run_id
  mcp_tool_id
  input_hash
  output_summary
  status
  approval_request_id nullable
  started_at
  finished_at

ApprovalRequest
  id
  run_id
  tool_invocation_id
  action
  requested_payload_json
  status               pending | approved | rejected | expired
  authorized_user_id
  expires_at
  decided_at
```

## 6. LangGraph Runtime

### 6.1 运行状态

```python
class ExpertRunState(TypedDict):
    run_id: str
    session_id: str | None
    authorized_user_id: str
    expert_version_id: str | None
    deployment_id: str | None
    task_id: str | None
    work_item_id: str | None
    node_id: str | None
    messages: list
    context: dict
    knowledge_hits: list
    memory_entries: list
    allowed_tools: list
    tool_calls: list
    pending_approval: dict | None
    result: dict | None
    trace_id: str
```

### 6.2 标准图

```text
START
  -> 解析会话和授权上下文
  -> 解析选中的 ExpertVersion 与 SkillVersion
  -> 加载 FlowHub 默认 Native Tool
  -> 加载 Expert/Skill 明确批准的外部 MCP Tool
  -> 组装任务和工作流上下文
  -> 检索知识与记忆
  -> 模型规划
  -> 工具路由
       -> 不需要工具：生成结果
       -> read/generate Tool：调用后返回模型规划
       -> write_draft Tool：保存草稿后返回模型规划
       -> write_commit/critical Tool：创建审批请求并中断
  -> 持久化结果、Trace、审计和 Memory
END
```

### 6.3 审批中断与恢复

```text
Tool Node
  -> Policy 判定需要审批
  -> 持久化 ToolInvocation 与 ApprovalRequest
  -> LangGraph interrupt()
  -> 用户批准或拒绝
  -> 批准：Command(resume=审批载荷)
  -> 精确执行一次获批 Tool
  -> 写入审计并结束 Run
```

审批是运行时执行边界，不能仅作为 Suggestion 的状态标记。

## 7. Tool 设计

### 7.1 FlowHub Native Tool

内部 Expert 通过进程内领域服务适配器操作 FlowHub，禁止通过调用自身 HTTP API 或自身 SSE MCP 接口绕行。

初始目录：

```text
flowhub.task.list
flowhub.task.get
flowhub.task.context.get
flowhub.work_item.get
flowhub.document.list
flowhub.document.read

flowhub.task_form.draft
flowhub.task_form.commit
flowhub.task_append.draft
flowhub.task_append.commit
flowhub.document.upload_draft
flowhub.document.upload_commit
flowhub.subtask.create_draft
flowhub.subtask.create_commit

flowhub.task.submit
flowhub.task.return
flowhub.task.transfer
flowhub.workflow.pause
flowhub.workflow.resume
flowhub.work_item.close
```

每个 Native Tool 必须复用用户操作对应的领域校验路径，不能绕过项目、任务、节点、文档和工作流限制。

### 7.2 外部 MCP Tool

外部 MCP Server 的接入流程：

```text
注册 Server
  -> 校验传输方式和凭据引用
  -> 健康检查
  -> Tool Discovery
  -> 写入发现的 McpTool
  -> 管理员批准 Tool 并设置风险等级
  -> 已发布 ExpertVersion / SkillVersion 显式绑定 Tool
```

注册 Server 不等于自动授予所有工具。Expert 必须显式绑定已批准的 Tool。

### 7.3 FlowHub 对外 MCP

FlowHub 保持现有 MCP Server，对外 Tool 以 `direction=inbound` 目录化。外部智能体继续使用用户级 access key 认证。

## 8. 两类 Skill 的边界

### 8.1 内部 Expert Skill

内部 Expert Skill 需要持久化、版本化、测试、发布，并且只在 FlowHub LangGraph Runtime 内执行。它可绑定 Native Tool 和已批准的外部 MCP Tool。

### 8.2 外部 FlowHub 操作 Skill

外部操作 Skill 保持页面下载模式：

```text
外部智能体
  -> 下载 flowhub-operation-skill.md
  -> 配置 FlowHub inbound MCP 地址和 access key
  -> 在当前用户的数据权限范围内操作 FlowHub
```

它不是 `SkillVersion`，不能绑定 Expert，也不授予外部智能体任何内部 Expert 特权。

## 9. 权限与风险策略

每次内部 Tool 调用的有效权限为：

```text
用户 RBAC
  ∩ ExpertVersion Policy
  ∩ SkillVersion Policy
  ∩ Tool Policy
  ∩ 项目与工作流节点范围
  ∩ 任务数据范围
  ∩ 文档敏感级别
  ∩ 当前审批状态
```

| 风险等级 | 示例 | 运行规则 |
|---|---|---|
| `read` | 读任务、文档、工作项 | 直接执行 |
| `generate` | 生成分析与建议 | 直接执行并保留 Trace |
| `write_draft` | 创建表单或文档草稿 | 保存草稿，不变更正式业务状态 |
| `write_commit` | 提交表单、上传文档、创建子任务 | 中断运行，人工批准后恢复执行 |
| `critical` | 提交、退回、转办、暂停、恢复、关闭 | 默认拒绝；仅显式 Policy 与审批可放行 |

审计至少记录 ExpertDeployment、授权用户、Expert/Skill 版本、Tool、Trace ID、审批关联和结果摘要。

## 10. 工作流迁移

工作流 Canvas 从 Agent 绑定迁移为 Expert Deployment 绑定：

```json
{
  "expert": {
    "deploymentId": "expd_123",
    "policyOverride": {
      "write_form": "confirm"
    }
  }
}
```

迁移映射：

| 现有实体 | 目标实体 |
|---|---|
| `AgentType` | `Expert` 与首个 `ExpertVersion` |
| `Agent` | `ExpertDeployment` |
| `AgentTool` | `ModelProviderConnection` 或执行配置 |
| `AgentCapability` | Expert Policy 或 Deployment Policy Override |
| `AgentInvocation` | `LangGraphRun` |
| `AgentConfirmRequest` | `ApprovalRequest` |
| `AgentSuggestion` | Expert 输出或草稿记录 |
| Canvas `agent.agentId` | Canvas `expert.deploymentId` |

旧表在迁移校验期间只读保留；完成运行对账和工作流绑定迁移后，才移除旧 Agent 页面和 API。

## 11. 聊天工作台规则

未选择 Expert 的 LangGraph Session 默认拥有 FlowHub Native Tool，因此用户可以直接查询或执行本人有权限的 FlowHub 操作。

选择 Expert 后，Session 额外加载：

- 已发布 ExpertVersion 的 Prompt 与输出契约；
- 指定版本的内部 Expert Skill；
- Expert/Skill 显式绑定的 MCP Tool；
- Expert 指定知识集合；
- Expert 和任务级 Memory Policy。

首版会话允许零个或一个 Expert Deployment，加多个 Skill。多 Expert 协同需在后续定义主 Expert、委派图、输出冲突与权限策略后实现。

## 12. 实施阶段

### Phase 0：契约与迁移

- 数据字典、状态机、API 契约、Policy 矩阵、迁移映射和回滚条件。

### Phase 1：Expert Registry 与 Deployment

- Expert、ExpertVersion、ExpertDeployment、执行配置、发布生命周期和工作台外壳。

### Phase 2：内部 Expert Skill Registry

- Skill、SkillVersion、绑定、测试、发布、弃用和版本冻结。

### Phase 3：MCP Registry

- Native Tool Catalog、出站 MCP Server、健康检查、Tool Discovery、Tool 批准、Tool 绑定和 Tool 调用审计。

### Phase 4：LangGraph Runtime

- 基础图、Provider Adapter、Expert/Skill 加载、Tool Node、审批 interrupt/resume、Run 持久化和 Trace UI。

### Phase 5：知识与 Memory

- pgvector 索引、引用、知识策略、Memory 命名空间、留存、删除与审计。

### Phase 6：工作流切换与清理

- Canvas 迁移、历史数据对账、旧 Agent API/页面下线、监控和回滚演练。

## 13. 验收标准

- 管理员可以创建、测试、发布、停用和归档 Expert。
- 自建 Expert 可以绑定不可变的已发布 Expert SkillVersion。
- Expert Skill 可以绑定 FlowHub Native Tool 与已批准的外部 MCP Tool。
- 未选择 Expert 的对话 Session 可以使用当前用户有权限的 FlowHub Native Tool。
- 选择 Expert 后只加载其冻结的 Prompt、Skill、知识/Memory Policy 和已批准 Tool。
- `write_commit` 和 `critical` Tool 会中断 LangGraph Run，并生成可追溯审批。
- 批准后只恢复一次对应 Run，并记录完整审计。
- 每个 Run 能查询 Expert/Skill/Tool 版本、授权用户、Trace ID 与结果。
- 工作流节点绑定 `ExpertDeployment`，不再绑定旧 Agent ID。
- 外部 FlowHub 操作 Skill 继续可从页面下载，并只能经 inbound MCP 的 access key 边界访问系统。
