# Expert OS UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the confirmed Expert OS visual prototype, rename the conversation workspace to AiChat, and make every new page reachable through the existing FlowHub frontend shell using typed local mock data and clickable demo interactions.

**Architecture:** Extend the existing `PageId`/`useApp().navigate` state model instead of adding a router. Keep existing pages and shared layout intact, add a focused Expert OS mock-data module and reusable presentation components, then group the new resource pages into a small number of page modules so each center remains visually consistent without creating a single monolith.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind CSS, Lucide React, Recharts, existing FlowHub shared components and app store.

## Global Constraints

- The conversation workspace is user-facing **AiChat**; retain LangGraph as an underlying runtime concept only.
- This phase uses local mock data and does not change backend models or require new APIs.
- Preserve existing routes/pages and the existing dark fixed sidebar/content theme.
- Use native interactive elements and accessible labels; dynamic runtime state uses `aria-live="polite"`.
- Do not expose internal Expert Skill content on the external MCP / Skill download page.
- `npm run build` must pass; run `npm run lint` and document pre-existing failures separately.

## File Map

- Modify: `frontend/src/types/index.ts` - add Expert OS page IDs and typed UI domain models.
- Modify: `frontend/src/store/app-store.tsx` - keep new page navigation and selected resource state available to pages.
- Modify: `frontend/src/components/layout.tsx` - replace the visible Agent-oriented navigation with Expert OS groups and AiChat title mappings while retaining legacy entries.
- Create: `frontend/src/data/expert-os-mock.ts` - deterministic mock Experts, Skills, MCP, Providers, Knowledge, Memory, Runs, Approvals, sessions, and helper labels.
- Create: `frontend/src/components/expert-os.tsx` - shared status badges, metric strip, filters, resource table, detail drawer, runtime events, and empty state.
- Create: `frontend/src/pages/expert-os-overview.tsx` - OS overview dashboard.
- Create: `frontend/src/pages/aichat.tsx` - AiChat three-column workspace and local runtime interactions.
- Create: `frontend/src/pages/expert-center.tsx` - Expert list and detail drawer.
- Create: `frontend/src/pages/expert-resources.tsx` - Skill, MCP, Provider, Knowledge, and Memory centers using a shared resource-center pattern.
- Create: `frontend/src/pages/runtime-center.tsx` - Runs, approvals, and trace/tool-call detail views.
- Create: `frontend/src/pages/external-tools.tsx` - external MCP / operation Skill download boundary page.
- Modify: `frontend/src/App.tsx` - render new pages for new `PageId` values.
- Modify: `frontend/src/index.css` - only add small semantic utility classes needed by the new control-plane surfaces.

### Task 1: Extend Navigation Types

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/store/app-store.tsx`
- Modify: `frontend/src/components/layout.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- New `PageId` values: `os-overview`, `aichat`, `expert-center`, `skill-center`, `mcp-center`, `provider-center`, `knowledge`, `memory`, `runtime-center`, `approvals`, `external-tools`.
- Existing `navigate(page: PageId)` remains the navigation interface.

- [ ] **Step 1: Inspect existing `PageId` and page switch before editing**

Run:

```bash
rg "type PageId|PageId|page ===|switch \(page\)" frontend/src/types frontend/src/App.tsx frontend/src/components/layout.tsx
```

Expected: all current page IDs and render branches are identified before adding new values.

- [ ] **Step 2: Add the new page IDs without removing legacy IDs**

Add the new literal values to the existing `PageId` union. Keep `agents`, `channel`, `audit`, and all existing workflow pages unchanged.

- [ ] **Step 3: Add the Expert OS navigation groups and title mappings**

Add visible sidebar items for OS 总览, AiChat, Expert 中心, Expert Skill, MCP 中心, Provider, 知识库, Memory, 运行中心, 审批队列, and MCP / Skill 下载. Update role visibility so the existing roles that can see `agents` can see the new prototype pages.

- [ ] **Step 4: Render placeholder branches in `App.tsx`**

Import the new page components as they are created, or use temporary text branches until Task 4. Every new `PageId` must resolve to a component rather than a blank screen.

- [ ] **Step 5: Run the type checker**

Run: `npm run build` from `frontend`.

Expected: no missing `PageId` index errors. Any component-not-found errors are expected only if this task is executed before Task 4 and must be resolved before moving on.

### Task 2: Add Typed Mock Data

**Files:**
- Modify: `frontend/src/types/index.ts`
- Create: `frontend/src/data/expert-os-mock.ts`

**Interfaces:**
- Export `ExpertRecord`, `SkillRecord`, `McpServerRecord`, `McpToolRecord`, `ProviderRecord`, `KnowledgeBaseRecord`, `MemoryRecord`, `RunRecord`, `ApprovalRecord`, `ChatSession`, `ChatMessage`, and `RuntimeEvent`.
- Export deterministic arrays: `mockExperts`, `mockSkills`, `mockMcpServers`, `mockProviders`, `mockKnowledgeBases`, `mockMemoryEntries`, `mockRuns`, `mockApprovals`, `mockChatSessions`.

- [ ] **Step 1: Define lifecycle unions and record types**

Model the documented statuses exactly: Expert `draft | testing | published | suspended | archived`; Skill `draft | testing | published | archived`; MCP `active | unhealthy | disabled`; Run `queued | running | interrupted | succeeded | failed | cancelled`; Approval `pending | approved | rejected | expired`.

- [ ] **Step 2: Add representative fixtures**

Include at least one built-in Expert, custom Expert, testing Expert, published Skill, deprecated Skill version, unhealthy MCP server, failed Run, interrupted Run, pending Approval, traceable Knowledge citation, and all provider states needed by the UI.

- [ ] **Step 3: Add helper label and tone functions**

Export pure helpers such as `statusLabel(status: string)` and `statusTone(status: string)` so pages do not duplicate lifecycle mappings.

- [ ] **Step 4: Type-check the mock module**

Run: `npx tsc --noEmit -p tsconfig.app.json` from `frontend`.

Expected: PASS.

### Task 3: Build Shared Expert OS Components

**Files:**
- Create: `frontend/src/components/expert-os.tsx`
- Modify: `frontend/src/index.css` only if a utility cannot be expressed with existing Tailwind classes.

**Interfaces:**
- `OsStatusBadge({ status, label?, dot? })`
- `MetricStrip({ items })`
- `FilterBar({ searchPlaceholder, query, onQueryChange, children })`
- `ResourceTable<T>({ columns, rows, rowKey, onRowClick?, emptyLabel? })`
- `DetailDrawer({ open, title, eyebrow?, onClose, children })`
- `RuntimeEvent({ event, expanded, onToggle })`

- [ ] **Step 1: Implement `OsStatusBadge` with semantic tones**

Use a native `span`, visible label, optional status dot, and stable color mapping for lifecycle, runtime, health, approval, and risk statuses.

- [ ] **Step 2: Implement `MetricStrip` and `FilterBar`**

Metric items must wrap on narrow screens. Search input must have a visible or screen-reader label. Filter controls remain native selects/buttons.

- [ ] **Step 3: Implement `ResourceTable`**

Use a real `<table>` on desktop and allow horizontal scrolling inside its own region on narrow widths. Row actions must be buttons and must not trigger row selection accidentally.

- [ ] **Step 4: Implement `DetailDrawer`**

Use `role="dialog"`, `aria-modal="true"`, labelled heading, Escape close, and focus restoration to the opener when practical. Add a mobile full-width layout.

- [ ] **Step 5: Implement `RuntimeEvent`**

Support tool call, knowledge citation, approval, and trace event variants. Dynamic status is announced via `aria-live="polite"`.

- [ ] **Step 6: Run `npm run build`**

Expected: PASS with the new shared module imported by no pages yet or by temporary smoke usage.

### Task 4: Implement Overview, Expert Center, and Resource Centers

**Files:**
- Create: `frontend/src/pages/expert-os-overview.tsx`
- Create: `frontend/src/pages/expert-center.tsx`
- Create: `frontend/src/pages/expert-resources.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Pages consume mock arrays and shared components from Tasks 2-3.
- Page-level row selection is local state; navigation uses `useApp().navigate`.

- [ ] **Step 1: Build OS overview**

Render the header, metric strip, Recharts activity trend, attention list, recent runs, Expert ranking, and runtime health grid. Include an `打开 AiChat` button that navigates to `aichat`.

- [ ] **Step 2: Build Expert center**

Render filters for lifecycle/kind, Expert cards or table rows, bound Skill/deployment counts, and a detail drawer with tabs for Overview, Versions, Skills, Tools, Deployments, Policy, and Activity.

- [ ] **Step 3: Build the shared resource-center view**

Use a typed `ResourceCenterKind` prop to render Skill, MCP, Provider, Knowledge, or Memory data while keeping each page's title, tabs, columns, and detail content semantically distinct.

- [ ] **Step 4: Wire each center to its own `PageId`**

Ensure `skill-center`, `mcp-center`, `provider-center`, `knowledge`, and `memory` render the correct center and never fall back to the old Agent page.

- [ ] **Step 5: Verify desktop and narrow layouts in build**

Run: `npm run build`.

Expected: PASS and all resource pages compile with no implicit-any errors.

### Task 5: Implement Runtime Center, Approvals, and External Tools

**Files:**
- Create: `frontend/src/pages/runtime-center.tsx`
- Create: `frontend/src/pages/external-tools.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Runtime pages consume `mockRuns`, `mockApprovals`, and `RuntimeEvent`.
- Approval actions update local state and show a toast stating `已更新本地演示状态`.

- [ ] **Step 1: Build Runs table and trace drawer**

Render status metrics, status/Expert/time filters, run rows, and a trace drawer with the standard LangGraph stages from the spec.

- [ ] **Step 2: Build approval queue**

Render pending approvals first, display risk and expiry prominently, and provide approve/reject buttons. After an action, move the item out of pending in local state and add a resolved visual state.

- [ ] **Step 3: Build external MCP / Skill download page**

Show FlowHub operation Skill and MCP onboarding cards only. Include explicit boundary copy that internal Expert Skills and Expert Runtime permissions are not exposed.

- [ ] **Step 4: Verify action semantics**

Keyboard activate every approval and download action. Confirm button labels are explicit and no internal Skill instructions are rendered in this page.

### Task 6: Implement AiChat

**Files:**
- Create: `frontend/src/pages/aichat.tsx`
- Modify: `frontend/src/components/layout.tsx` if the title/breadcrumb needs a final adjustment.

**Interfaces:**
- AiChat owns local `activeSession`, `selectedExpert`, `selectedSkill`, `draft`, `expandedEventIds`, and `approvalState`.
- Uses `mockChatSessions`, `mockExperts`, `mockSkills`, `mockRuns`, and shared `RuntimeEvent`.

- [ ] **Step 1: Implement the three-column desktop shell**

Left session rail, center conversation, and right runtime configuration rail. Keep the composer in the center column and prevent the side rails from creating horizontal overflow.

- [ ] **Step 2: Implement Expert/Skill/Provider selectors**

Use labelled native selects. No Expert selection displays FlowHub Native Tool. Selecting an Expert updates the visible version, Skills, tools, knowledge, and memory policy summary.

- [ ] **Step 3: Implement message submission**

On submit, append the user message, add a deterministic running tool event, then resolve to a deterministic assistant message after a short timeout. Disable the send button for empty input and support Enter to send with Shift+Enter for a newline.

- [ ] **Step 4: Implement expandable runtime events and citations**

Tool calls expose risk, input, output, duration, and audit link. Knowledge citations expose source locator and a button navigating to `knowledge`.

- [ ] **Step 5: Implement approval interruption**

When the mock session contains a pending approval, render a blocking approval panel with action, scope, expiry, approve, and reject. Update the local panel status and announce the result.

- [ ] **Step 6: Add responsive rail toggles**

On narrow screens, expose buttons labelled `打开会话列表` and `打开运行配置`; render the respective rail as an accessible overlay/panel. Respect reduced motion by avoiding mandatory animated transitions.

- [ ] **Step 7: Run build and lint**

Run:

```bash
npm run build
npm run lint
```

Expected: build passes. Lint either passes or reports only documented pre-existing issues.

### Task 7: Final Integration And Verification

**Files:**
- Modify: `frontend/src/App.tsx` only for final branch cleanup.
- Modify: `frontend/src/components/layout.tsx` only for final navigation/title cleanup.
- Modify: `frontend/src/index.css` only for verified visual defects.

- [ ] **Step 1: Search for stale user-facing LangGraph workspace labels**

Run:

```bash
rg "LangGraph 对话工作台|对话工作台|Agent 管理" frontend/src
```

Expected: only backend/architecture terminology or intentionally retained legacy Agent API/page references remain; the new conversation entry is labelled AiChat.

- [ ] **Step 2: Run the production build**

Run: `npm run build` from `frontend`.

Expected: PASS.

- [ ] **Step 3: Run lint**

Run: `npm run lint` from `frontend`.

Expected: PASS or a concise list of pre-existing failures.

- [ ] **Step 4: Inspect the final diff and worktree**

Run:

```bash
```

Expected: only the Expert OS UI files and the approved spec/plan are changed.
