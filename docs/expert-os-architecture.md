# FlowHub Expert OS Architecture

## Status

Proposed architecture. This document replaces the product direction of the current Agent management experience. It is an implementation contract for the Expert OS migration, not a statement that all components already exist.

## Goals

- Replace the Agent management product surface with an Expert Workbench.
- Run internal and custom Experts through LangGraph.
- Support versioned Expert Skills that can use FlowHub native tools and approved external MCP tools.
- Keep FlowHub system-operation capability available in LangGraph conversations even when no Expert is selected.
- Preserve FlowHub as an MCP server for external agents.
- Keep the external FlowHub-operation Skill as a downloadable page artifact, separate from internal Expert Skills.
- Retain existing workflow confirmation, audit, task, document, and provider concepts during migration, then move their ownership into the new model.

## Non-Goals

- Multi-tenant isolation is out of scope for the first implementation. New models must avoid assumptions that preclude a future `tenant_id` boundary.
- Expert Marketplace, Skill Marketplace, and MCP Marketplace are out of scope.
- Automatic high-risk business mutations are out of scope. Write and critical tools remain policy-gated and approval-aware.
- External agents do not receive internal Expert Skill definitions or Expert runtime privileges.

## Terms

| Term | Meaning |
|---|---|
| Expert | A versioned business identity and policy package: prompt, execution settings, Skills, allowed tools, knowledge and memory policy. |
| Expert Deployment | A runnable published Expert instance bound to workflow nodes and metrics. |
| Expert Skill | A versioned internal capability package used by a LangGraph Expert. It can define instructions, schemas, subgraphs, policy limits, and allowed tools. |
| FlowHub Operation Skill | A downloadable Markdown instruction template for an external agent to operate FlowHub through its public MCP server. It is not an Expert Skill and is not stored in the Expert Skill registry. |
| FlowHub Native Tool | An in-process adapter to FlowHub domain services. It is used by internal LangGraph runs, not through a loopback HTTP/MCP call. |
| External MCP Tool | A discovered tool from a registered outbound MCP server. It can only be used after explicit Tool, Skill, and Expert approval. |
| Policy | The rule set that constrains an Expert, Skill, Tool, task, project, node, document, and approval state. |

## Product Surfaces

### Expert Workbench

The existing Agent management page is replaced with these surfaces:

```text
Expert Workbench
|- Experts
|  |- Built-in Experts
|  |- Custom Experts
|  |- Draft, test, publish, suspend, archive
|  |- Version comparison
|  `- Deployments and workflow bindings
|- Expert Skills
|  |- Skill definitions
|  |- Versioning, testing, publish, deprecation
|  `- Expert bindings
|- MCP Registry
|  |- FlowHub native tool catalog
|  |- Outbound MCP servers
|  |- Discovery, health, credentials, Tool approval
|  `- Expert and Skill tool bindings
|- Providers
|  `- OpenCode and API provider/model connections
|- Knowledge
|  `- Collections, documents, chunks, retrieval sources
|- Memory
|  `- Expert, task, and run namespaces; retention and audit
`- Runtime Center
   |- LangGraph runs and traces
   |- Tool calls
   |- Approval queue
   `- Retry and failure inspection
```

### LangGraph Chat Workbench

A dedicated interactive page lets a user converse directly with LangGraph:

```text
LangGraph Chat Workbench
|- Session list
|- Conversation and citations
|- Composer
|- Expert selector
|- Expert Skill selector
|- Model Provider and model selector
|- Available tool view
|- Tool call timeline
`- Approval panel
```

The session starts with FlowHub native system-operation tools. The user may add an Expert and its published Skills. External MCP tools are only added when the selected Expert/Skill version explicitly authorizes them.

## Domain Model

### Expert Definition and Deployment

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

An ExpertVersion is immutable after publication. A Deployment always resolves an explicit published version so run traces remain reproducible.

### Internal Expert Skills

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

An Expert Skill is the canonical internal unit for professional instructions, tool contracts, optional LangGraph subgraphs, and policy limits.

### MCP Registry and Tool Catalog

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

`direction=native` represents FlowHub domain service adapters. `direction=inbound` represents the FlowHub MCP server exposed to external agents. `direction=outbound` represents an external MCP server used by an internal Expert.

### Knowledge and Memory

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

The initial retrieval implementation uses PostgreSQL with pgvector. Retrieval results must carry a source locator into LangGraph state and user-visible citations.

### Runtime and Approval

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

## LangGraph Runtime

### Base State

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

### Standard Graph

```text
START
  -> resolve session and authorization context
  -> resolve ExpertVersion and SkillVersions, if selected
  -> load FlowHub native default tools
  -> load Expert/Skill-approved external MCP tools
  -> build task and workflow context
  -> retrieve knowledge and memory
  -> model planning node
  -> tool router
       -> no tool: generate result
       -> allowed read/generate tool: invoke and return to planner
       -> write_draft tool: persist draft and return to planner
       -> write_commit/critical tool: create ApprovalRequest, interrupt
  -> persist result, trace, audit, and memory
END
```

### Approval Resume

For write-commit and critical operations:

```text
tool node
  -> policy requires approval
  -> persist ToolInvocation and ApprovalRequest
  -> LangGraph interrupt()
  -> user approves or rejects
  -> approve: Command(resume=approval payload)
  -> execute the approved native/MCP tool exactly once
  -> write audit and finish the run
```

Approval is an execution boundary. It is not merely a status label attached to a suggestion.

## Tool Model

### FlowHub Native Tools

Internal Experts invoke FlowHub through in-process domain-service adapters, never through FlowHub's own HTTP or SSE endpoint.

Initial catalog:

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

The tool adapter must call the same domain service and validation path used by user-facing FlowHub operations. It must not bypass project, task, node, document, or workflow rules.

### External MCP Tools

An outbound MCP server is registered, credentialed, health checked, and discovered before any Tool is available to an Expert.

```text
register server
  -> validate transport and credential reference
  -> health check
  -> discover tools
  -> create/update McpTool records as discovered
  -> administrator marks tools approved and assigns risk levels
  -> published ExpertVersion/SkillVersion binds explicit tools
```

An Expert never gets all tools from a registered server by default.

### FlowHub Inbound MCP

FlowHub continues to expose its external MCP server for external agents. Its public tools are separately cataloged as `direction=inbound`. Existing user access keys remain the credential type for external agents.

## Skill Separation

### Internal Expert Skill

Internal Expert Skills are persisted, versioned, tested, published, and attached to ExpertVersions. They execute inside FlowHub LangGraph Runtime.

### External FlowHub Operation Skill

The external FlowHub operation Skill remains a downloadable Markdown file from the External Access page:

```text
External agent
  -> downloads flowhub-operation-skill.md
  -> configures FlowHub inbound MCP endpoint and access key
  -> operates only within its user-scoped FlowHub access
```

It is intentionally not stored as a SkillVersion, cannot be attached to an Expert, and does not grant internal Expert privileges.

## Authorization

Every internal Tool invocation computes effective permission as:

```text
user RBAC
  ∩ ExpertVersion policy
  ∩ SkillVersion policy
  ∩ Tool policy
  ∩ project and workflow-node scope
  ∩ task data scope
  ∩ document sensitivity policy
  ∩ approval state
```

Risk policy:

| Risk | Example | Runtime behavior |
|---|---|---|
| read | Read a task, document, or work item | Execute directly |
| generate | Produce a report or recommendation | Execute directly; trace output |
| write_draft | Build a form or document draft | Persist draft; do not change formal business state |
| write_commit | Commit a form, upload document, create subtask | Interrupt; require user approval; execute after resume |
| critical | Submit, return, transfer, pause, resume, close | Default deny; only an explicit policy and approval can allow execution |

Audits record the Expert Deployment as actor and the requesting or workflow-authorized user as `authorized_user`, along with Expert/Skill versions, Tool, trace ID, and approval linkage.

## Workflow Migration

The workflow canvas replaces its current Agent binding with an Expert Deployment binding:

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

Migration mapping:

| Existing entity | Target entity |
|---|---|
| AgentType | Expert and first ExpertVersion |
| Agent | ExpertDeployment |
| AgentTool | ModelProviderConnection / execution profile |
| AgentCapability | Expert policy or deployment policy override |
| AgentInvocation | LangGraphRun |
| AgentConfirmRequest | ApprovalRequest |
| AgentSuggestion | Expert output/draft record |
| Canvas `agent.agentId` | Canvas `expert.deploymentId` |

Old tables remain read-only during migration verification. The old API and page are removed only after data reconciliation and workflow binding migration complete.

## Chat Workbench Behavior

An unconfigured LangGraph chat session has FlowHub native system-operation tools by default. Therefore a user can ask FlowHub questions or perform permitted FlowHub operations without selecting an Expert.

Selecting an Expert adds:

- published Expert prompt and output contract;
- exact published Expert Skills;
- explicit Skill/Expert MCP tool bindings;
- selected knowledge collections;
- Expert and task memory policy.

The unresolved product decision is whether a chat session permits multiple Experts. The initial implementation should constrain each session to zero or one Expert Deployment plus multiple Skills until a multi-Expert delegation, conflict resolution, and policy merge contract is approved.

## Delivery Phases

### Phase 0: Contracts and Migration Plan

- Final data dictionary, status transitions, API contracts, policy matrix, migration mapping, rollback conditions.

### Phase 1: Expert Registry and Deployments

- Expert, ExpertVersion, ExpertDeployment, Provider execution profiles, publish lifecycle, and replacement Workbench shell.

### Phase 2: Expert Skill Registry

- Skill, SkillVersion, bindings, validation, testing, publishing, deprecation, and version freezing.

### Phase 3: MCP Registry

- Native catalog, outbound server registration, health checks, discovery, Tool approval, Tool bindings, and Tool invocation audit.

### Phase 4: LangGraph Runtime

- Base graph, Provider adapters, Expert/Skill loading, Tool nodes, approval interrupt/resume, run persistence, and trace UI.

### Phase 5: Knowledge and Memory

- pgvector indexing, source citations, knowledge policies, memory namespaces, retention, deletion, and audit.

### Phase 6: Workflow Cutover and Retirement

- Canvas migration, historical data reconciliation, old Agent API/page retirement, monitoring, rollback rehearsal.

## Acceptance Criteria

The system is ready to replace Agent management only when:

- An administrator can create, test, publish, suspend, and archive an Expert.
- A custom Expert can bind immutable published Expert Skill versions.
- An Expert Skill can bind explicitly approved FlowHub native and external MCP tools.
- A direct chat session can use permitted FlowHub native tools without selecting an Expert.
- A selected Expert adds only its published prompt, Skill versions, knowledge/memory policy, and approved tools.
- A write-commit or critical Tool invocation pauses the LangGraph run and requires a durable approval before execution.
- Approval resumes the exact interrupted LangGraph run once and records a linked audit trail.
- Every run exposes its Expert/Skill/Tool versions, authorization user, trace ID, and outputs.
- Existing workflow nodes bind Expert Deployments instead of Agent IDs.
- The downloadable external FlowHub operation Skill remains available and works only through the inbound MCP access-key boundary.
