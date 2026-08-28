# Expert OS UI Design

## 1. Goal

Upgrade the existing FlowHub frontend from an Agent management interface into a coherent Expert OS visual prototype. The prototype uses local mock data and clickable UI states, while preserving the existing React, Vite, Tailwind, Lucide, and shared layout conventions.

The conversation workspace is named **AiChat** everywhere in the product. The architecture document's term "LangGraph 对话工作台" is treated as the underlying runtime concept, not a user-facing page name.

## 2. Scope

This phase is visual-first. It does not change backend models or require new APIs. Pages render realistic local mock data and expose enough interactions to validate information architecture and product behavior.

### Included pages

- Expert OS 总览
- AiChat
- Expert 中心
- Expert 详情
- Expert 版本与 Deployment
- Expert Skill 中心
- Skill 编辑 / 版本详情
- MCP 中心
- MCP Server / Tool 详情
- Provider 中心
- 知识库
- Memory
- 运行中心
- 审批队列
- Trace / Tool Call 详情
- FlowHub MCP / Skill 下载
- Existing tasks, projects, documents, audit, organization, permissions, channels, and workflow pages remain reachable.

### Explicitly excluded

- Backend schema or API migration
- Real LangGraph execution
- Real MCP discovery, health checks, or credentials
- Marketplace, multi-tenant isolation, multi-Expert collaboration
- Automatic execution of high-risk actions

## 3. Information Architecture

The primary navigation is regrouped as follows:

```text
FlowHub Expert OS
├── 工作台
│   ├── OS 总览
│   ├── AiChat
│   └── 我的任务
├── 构建
│   ├── Expert 中心
│   ├── Expert Skill
│   ├── MCP 中心
│   └── Provider
├── 上下文
│   ├── 知识库
│   └── Memory
├── 运行治理
│   ├── 运行中心
│   ├── 审批队列
│   └── 审计中心
└── 对外能力
    └── MCP / Skill 下载
```

The existing sidebar remains the shell, but its Resource and System groups are replaced or extended with the groups above. Existing non-Expert pages are retained under secondary navigation or existing routes so the prototype does not hide current capabilities.

## 4. Visual Direction

### Product tone

Calm, technical, auditable, and operational. The interface should feel like an internal control plane used repeatedly by engineering, operations, and business administrators rather than a consumer chat product or marketing landing page.

### Color semantics

- FlowHub blue: primary actions, active navigation, selected controls, links
- Expert violet: Expert identity, versions, skills, specialization
- Teal / emerald: healthy tools, successful runs, published resources
- Amber: pending approval, testing state, degraded health, warnings
- Red: failed runs, revoked resources, critical actions
- Slate: neutral infrastructure and metadata

Existing CSS variables and Tailwind colors remain the source of truth. New colors are added only as semantic classes or CSS variables when the existing palette cannot express a state.

### Layout principles

- Keep the existing dark fixed sidebar and light/dark content themes.
- Use `page-container`, `PageHeader`, `SectionCard`, `KpiCard`, `Badge`, and existing button patterns before adding primitives.
- Prefer dense two-column and three-column workspaces over large decorative cards.
- Avoid nested cards, oversized hero headings, purple gradients, and filler illustrations.
- Every page's first viewport must show its purpose, primary action, and current operational state.

## 5. Shared UI Model

### Shared components

Add a focused Expert OS visual layer rather than duplicating styles in every page:

- `OsStatusBadge`: maps lifecycle and runtime statuses to tone, label, and optional dot.
- `ResourceHeader`: title, description, breadcrumb context, primary and secondary actions.
- `ResourceTable`: compact table with filters, status, ownership, updated time, and row actions.
- `MetricStrip`: horizontal operational metrics for counts, success rate, latency, and pending work.
- `DetailDrawer`: right-side detail panel for resources and trace nodes.
- `FilterBar`: search, status, type, and scope controls with accessible labels.
- `RuntimeEvent`: reusable tool-call, knowledge-citation, approval, and trace event presentation.
- `EmptyState`: meaningful empty, unavailable, and mock-only states.

All interactive elements use native buttons, links, inputs, or selects. Icon-only buttons receive accessible labels. Dynamic runtime and approval status uses `aria-live="polite"`; error states use `role="alert"` where appropriate.

## 6. Page Designs

### 6.1 OS 总览

Purpose: answer "is the Expert OS healthy and what needs attention?"

First viewport:

- Page header with `Expert OS 总览`, time range selector, and `打开 AiChat` action.
- Metric strip: active Experts, published Skills, healthy MCP Tools, running Runs, pending Approvals.
- Main activity chart for runs and tool calls.
- Attention column with pending approvals, failed runs, unhealthy servers, and deprecated versions.

Lower sections:

- Recent runs table
- Expert adoption / invocation ranking
- Runtime health by layer: Expert, Skill, MCP, Provider, Knowledge, Memory

### 6.2 AiChat

Purpose: directly operate FlowHub capabilities through a single Expert or no Expert.

Desktop layout:

- Left rail: new chat, session search, pinned/recent sessions, status marker, session timestamp.
- Center: session header with Expert/version and provider; message stream; expandable runtime events; citations; approval interruption; composer with attachment and send controls.
- Right rail: Expert selector, Skill selector, Provider/Model selector, allowed tools, permission scope, approval state, memory and knowledge toggles.

Interaction states:

- No Expert selected: show `FlowHub Native Tool` as the active capability base.
- Expert selected: show Expert version, bound Skills, approved external MCP Tools, knowledge base, and memory policy.
- Tool call expanded: show tool name, risk level, input summary, output summary, duration, and audit link.
- Approval interrupted: show an explicit blocking panel with requested action, scope, expiry, approve, and reject controls.
- Send message: append a user message and a mock running event, then resolve to a deterministic assistant response.

Responsive behavior:

- Below desktop width, left and right rails become toggleable sheets/panels.
- The composer remains fixed to the bottom of the conversation column.
- Tool details remain readable without horizontal scrolling.

### 6.3 Expert 中心

Purpose: manage built-in and custom Experts.

- Header actions: create Expert, import draft, open AiChat.
- Metric strip: total, published, testing, active deployments, invocation success rate.
- Tabs: all, built-in, custom, draft, testing, published, suspended, archived.
- Expert table/card hybrid showing identity, owner, current version, bound Skills, deployment count, last run, status, and actions.
- Selecting a row opens `ExpertDetail` drawer.

### 6.4 Expert 详情

Purpose: inspect the business identity and immutable runtime composition.

- Identity header: name, slug, kind, owner, status, current published version.
- Tabs: Overview, Versions, Skills, Tools, Knowledge, Memory, Deployments, Policy, Activity.
- Overview shows system prompt preview, output contract, execution profile, and policy summary.
- Versions show immutable snapshots with compare and publish/deprecate affordances.
- Deployments show lifecycle state, bound workflow nodes, provider override, and recent runtime metrics.
- Destructive actions use confirmation dialogs and visible audit consequences.

### 6.5 Expert Skill 中心

Purpose: manage versioned internal professional capability packages.

- Table with Skill name, description, current version, status, subgraph indicator, tool count, bound Expert count, owner, and updated time.
- Tabs for draft, testing, published, archived.
- Detail drawer includes instructions, input/output schemas, subgraph preview, policy restrictions, and Expert bindings.
- Create/edit uses a multi-section configuration surface, but remains mock-only in this phase.

### 6.6 MCP 中心

Purpose: distinguish Native, Inbound, and Outbound MCP capabilities.

- Top segmented control: FlowHub Native Tools, External MCP Servers, Inbound FlowHub MCP.
- Server health summary: active, unhealthy, disabled, approval-required tools.
- Server rows show direction, transport, endpoint/reference, last health check, tool count, and credential state.
- Tool detail shows risk level, approval policy, discovered/approved status, input/output schema summaries, and bindings.

### 6.7 Provider 中心

Purpose: manage OpenCode and API Provider/model configuration.

- Provider cards show engine, provider name, base URL, credential state, model count, default override support, and health.
- Model table shows model, modality, context window, latency, availability, and assigned Experts.
- Provider edit drawer uses labeled fields and an API key masked state; no secrets are persisted.

### 6.8 知识库

Purpose: manage collections and traceable source documents.

- Collection list with document count, indexed chunks, retrieval health, policy, sensitivity, and last indexed time.
- Collection detail shows document table, chunk/index status, source locator, and retrieval test panel.
- Citations in AiChat link back to a mock source locator in this page.

### 6.9 Memory

Purpose: inspect and govern memory entries by namespace.

- Namespace tabs: Expert, Task, Run.
- Filters for owner, sensitivity, status, expiry, and source.
- Entry detail shows content preview, metadata, retention/expiry, sensitivity, and audit history.
- Provide clear controls for archive and delete in mock state, with warning copy.

### 6.10 运行中心

Purpose: monitor LangGraph Runs and diagnose execution.

- Metric strip: queued, running, interrupted, succeeded, failed, cancelled.
- Runs table: run ID, session, Expert/version, deployment, status, duration, trace ID, started time.
- Filter by status, Expert, tool, task, and time range.
- Run detail drawer shows a vertical trace timeline: context resolution, skill loading, retrieval, model planning, tool route, approval interrupt, persistence.

### 6.11 审批队列

Purpose: make high-risk runtime actions explicit and actionable.

- Pending approval list with risk color, requesting Expert, tool, action, scope, requester, expiry, and requested time.
- Detail panel shows exact requested payload summary, policy reason, affected objects, and audit preview.
- Approve/reject updates local mock state and adds a resolved event to the activity feed.

### 6.12 审计中心

The existing audit page remains the durable event ledger, but its visual language is aligned with Expert OS. Add filters for run ID, Expert, Skill, Tool, approval, actor, and outcome. Keep request IDs and trace IDs copyable.

### 6.13 MCP / Skill 下载

Purpose: preserve external-agent onboarding without exposing internal Expert Skills.

- Explain the boundary between external FlowHub Operation Skills and internal Expert Skills.
- Download cards for FlowHub MCP connection instructions, Markdown operation Skills, access key setup, and example clients.
- Show scope and limitations prominently: task data access only, no internal Expert Runtime access.

## 7. Mock Data And State

Mock data lives in a dedicated frontend module and is typed around the architecture concepts. It includes enough variation to demonstrate every lifecycle state and at least one approval interruption, failed run, unhealthy MCP Server, deprecated version, and traceable citation.

Local state is page-scoped or stored in the existing app store only when it affects navigation or cross-page demos. The prototype must not pretend that mock mutations are persisted to the backend; toast messages should say `已更新本地演示状态` where needed.

## 8. Routing And Compatibility

Use the existing `PageId` and `useApp().navigate` model rather than introducing a router dependency. Add new page IDs for the Expert OS pages and update the sidebar title/breadcrumb map. Keep current page IDs working. Rename only the visible conversation label to `AiChat`; do not rename backend concepts or existing `agents` APIs in this phase.

## 9. Verification

- `npm run build` must pass.
- `npm run lint` must pass or any pre-existing failures must be documented separately.
- Check desktop and narrow viewport layouts for sidebar, AiChat rails, tables, drawers, and composer.
- Keyboard-check sidebar navigation, tabs, filters, drawers, approval actions, and AiChat composer.
- Confirm no internal Expert Skill content appears in the external download page.
