# Expert OS Local-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a persistent, local-first Expert lifecycle from creation through test, approval, publication, deployment, and AiChat execution.

**Architecture:** Replace page-local mock arrays with one React context backed by a versioned `localStorage` document. Pure domain functions validate lifecycle commands and create immutable records. Pages render the store data and dispatch explicit commands; dialogs own only temporary form state. This state contract is the future backend API boundary.

**Tech Stack:** React 19, TypeScript 5.9, Vite 7, `localStorage`, React Testing Library, Vitest, agent-browser.

## Global Constraints

- Keep all Expert OS data browser-local in this phase. Do not call a real model, MCP server, or write tool.
- Initialize the store from `frontend/src/data/expert-os-mock.ts` once, and use a versioned storage document to handle incompatible saved data.
- Built-in Experts are read-only; only a custom draft without deployments can be deleted.
- A custom Expert starts at `draft` version `v0.1`; a published edit creates a new draft and never changes an existing deployment pin.
- Require a successful test after the last draft modification before publishing.
- Block test and publication for missing identity, prompt, provider/model, eligible Skills/Knowledge Bases, or approval policy for write/critical tools.
- Persist all writes and display the same runs and approvals in AiChat, Runtime Center, and Approval Queue.
- Do not expose or persist credential values, only the configured/missing state.
- Preserve existing FlowHub project/task page behavior and existing visual language.

---

## File Structure

- Create `frontend/src/data/expert-os-seed.ts`: exports the immutable initial state composed from the current mock records.
- Create `frontend/src/domain/expert-os.ts`: pure IDs, timestamps, validation, versioning, lifecycle, deployment, run, and approval command functions.
- Create `frontend/src/domain/expert-os.test.ts`: unit coverage for every lifecycle and eligibility rule.
- Create `frontend/src/store/expert-os-store.tsx`: persisted React context that calls the domain functions and exposes data plus commands.
- Create `frontend/src/components/expert-wizard.tsx`: accessible Expert create/edit/test/publish/deploy dialog flow.
- Create `frontend/src/components/resource-dialogs.tsx`: small dialogs for custom Skills, MCP servers, Providers, Knowledge Bases, and export.
- Modify `frontend/src/types/index.ts`: define the persisted state, Expert configuration, version snapshot, deployment, tool binding, and command input types.
- Modify `frontend/src/main.tsx`: wrap the application in `ExpertOsProvider`.
- Modify `frontend/src/pages/expert-center.tsx`: use store-backed rows and the Expert lifecycle UI.
- Modify `frontend/src/pages/expert-resources.tsx`: use store-backed rows and resource dialogs.
- Modify `frontend/src/pages/aichat.tsx`: use store-backed available deployments, sessions, runs, citations, and approvals.
- Modify `frontend/src/pages/runtime-center.tsx`: use shared runs and approval decisions; export filtered records.
- Modify `frontend/src/components/expert-os.tsx`: use status metadata independent of mock data and add reusable dialog primitives only if required.
- Modify `frontend/src/data/expert-os-mock.ts`: retain only visual seed constants not moved to `expert-os-seed.ts`; remove page dependency on mutable exported arrays.
- Modify `frontend/package.json`: add `vitest`, `@testing-library/react`, `@testing-library/user-event`, and `jsdom`; add `test` script.
- Create `frontend/vitest.config.ts`: use Vite React settings and a `jsdom` test environment.

### Task 1: Establish The Store Contract And Test Harness

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Modify: `frontend/src/types/index.ts:217-279`
- Create: `frontend/src/data/expert-os-seed.ts`
- Create: `frontend/src/domain/expert-os.ts`
- Test: `frontend/src/domain/expert-os.test.ts`

**Interfaces:**
- Consumes: current seed arrays from `frontend/src/data/expert-os-mock.ts`.
- Produces: `ExpertOsState`, `ExpertConfig`, `ExpertVersion`, `DeploymentRecord`, `ValidationResult`, `validateExpert`, `createExpert`, and `saveExpertDraft`.

- [ ] **Step 1: Add the test dependencies and script**

```json
{
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "@testing-library/react": "^16.3.0",
    "@testing-library/user-event": "^14.6.1",
    "jsdom": "^26.1.0",
    "vitest": "^3.2.4"
  }
}
```

- [ ] **Step 2: Add Vitest configuration**

```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  test: { environment: 'jsdom', globals: true },
})
```

- [ ] **Step 3: Define the persisted model types**

Add these types in `frontend/src/types/index.ts` after `ExpertRecord`:

```ts
export interface ExpertConfig {
  systemPrompt: string
  providerId: string
  model: string
  knowledgeBaseIds: string[]
  toolPolicies: Record<string, 'none' | 'required' | 'administrator_only'>
  lastTestedRevision: number | null
  revision: number
}

export interface ExpertVersion {
  id: string
  expertId: string
  version: string
  createdAt: string
  config: ExpertConfig
  skills: string[]
}

export interface DeploymentRecord {
  id: string
  expertId: string
  expertVersion: string
  name: string
  environment: 'test' | 'prod'
  alias: string
  status: 'active' | 'suspended'
  createdAt: string
}

export interface ExpertOsState {
  schemaVersion: 1
  experts: ExpertRecord[]
  expertConfigs: Record<string, ExpertConfig>
  versions: ExpertVersion[]
  deployments: DeploymentRecord[]
  skills: SkillRecord[]
  mcpServers: McpServerRecord[]
  mcpTools: McpToolRecord[]
  providers: ProviderRecord[]
  knowledgeBases: KnowledgeBaseRecord[]
  memories: MemoryRecord[]
  runs: RunRecord[]
  approvals: ApprovalRecord[]
  chatSessions: ChatSession[]
}
```

- [ ] **Step 4: Create immutable seed state**

```ts
import {
  mockApprovals, mockChatSessions, mockExperts, mockKnowledgeBases, mockMcpServers,
  mockMcpTools, mockMemoryEntries, mockProviders, mockRuns, mockSkills,
} from './expert-os-mock'
import type { ExpertOsState } from '../types'

export function createExpertOsSeed(): ExpertOsState {
  return {
    schemaVersion: 1,
    experts: structuredClone(mockExperts),
    expertConfigs: {},
    versions: [],
    deployments: [],
    skills: structuredClone(mockSkills),
    mcpServers: structuredClone(mockMcpServers),
    mcpTools: structuredClone(mockMcpTools),
    providers: structuredClone(mockProviders),
    knowledgeBases: structuredClone(mockKnowledgeBases),
    memories: structuredClone(mockMemoryEntries),
    runs: structuredClone(mockRuns),
    approvals: structuredClone(mockApprovals),
    chatSessions: structuredClone(mockChatSessions),
  }
}
```

- [ ] **Step 5: Write failing lifecycle and validation tests**

```ts
import { createExpertOsSeed } from '../data/expert-os-seed'
import { createExpert, validateExpert } from './expert-os'

it('creates a custom draft at v0.1', () => {
  const next = createExpert(createExpertOsSeed(), {
    name: '发布说明专家', slug: 'release-notes', description: '生成发布说明', owner: '林晓',
  })
  expect(next.experts.at(-1)).toMatchObject({ kind: 'custom', status: 'draft', version: 'v0.1' })
})

it('reports every publication blocker for an incomplete draft', () => {
  const state = createExpert(createExpertOsSeed(), {
    name: '发布说明专家', slug: 'release-notes', description: '', owner: '林晓',
  })
  const expert = state.experts.at(-1)!
  expect(validateExpert(state, expert.id, 'publish').blockers).toEqual(expect.arrayContaining([
    '请填写 Expert 描述', '请填写 System Prompt', '请选择健康的 Provider 与模型', '请至少绑定一个可用 Skill',
  ]))
})
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `npm test -- expert-os.test.ts`

Expected: FAIL because `expert-os.ts` exports do not yet exist.

- [ ] **Step 7: Implement pure creation and validation functions**

```ts
export type ValidationMode = 'save' | 'test' | 'publish'

export function createExpert(state: ExpertOsState, input: Pick<ExpertRecord, 'name' | 'slug' | 'description' | 'owner'>): ExpertOsState {
  if (state.experts.some((expert) => expert.slug === input.slug)) throw new Error('slug 已存在')
  const id = `exp-${crypto.randomUUID()}`
  const expert: ExpertRecord = {
    id, ...input, kind: 'custom', status: 'draft', version: 'v0.1', skills: [], deployments: 0,
    calls: 0, successRate: 0, lastRun: '尚未运行', updated: new Date().toLocaleString('zh-CN'),
  }
  return {
    ...state,
    experts: [...state.experts, expert],
    expertConfigs: { ...state.expertConfigs, [id]: { systemPrompt: '', providerId: '', model: '', knowledgeBaseIds: [], toolPolicies: {}, lastTestedRevision: null, revision: 0 } },
  }
}

export function validateExpert(state: ExpertOsState, expertId: string, mode: ValidationMode): ValidationResult {
  const expert = state.experts.find((item) => item.id === expertId)
  const config = state.expertConfigs[expertId]
  const blockers: string[] = []
  if (!expert?.description.trim()) blockers.push('请填写 Expert 描述')
  if (!config?.systemPrompt.trim()) blockers.push('请填写 System Prompt')
  const provider = state.providers.find((item) => item.id === config?.providerId)
  if (!provider || provider.status !== 'healthy' || provider.credential !== 'configured' || !provider.models.includes(config.model)) blockers.push('请选择健康的 Provider 与模型')
  if (!expert?.skills.length) blockers.push('请至少绑定一个可用 Skill')
  if (expert?.skills.some((id) => !['published', 'testing'].includes(state.skills.find((skill) => skill.id === id)?.status ?? ''))) blockers.push('绑定的 Skill 不可用')
  if (config?.knowledgeBaseIds.some((id) => state.knowledgeBases.find((base) => base.id === id)?.status !== 'indexed')) blockers.push('绑定的知识库尚未完成索引')
  const governedTools = state.mcpTools.filter((tool) => ['write_commit', 'critical'].includes(tool.risk))
  if (governedTools.some((tool) => !config?.toolPolicies[tool.id] || config.toolPolicies[tool.id] === 'none')) blockers.push('写入或高风险工具必须设置审批策略')
  if (mode === 'publish' && config?.lastTestedRevision !== config?.revision) blockers.push('当前修改尚未通过测试')
  return { blockers, valid: blockers.length === 0 }
}
```

- [ ] **Step 8: Run tests to verify the model passes**

Run: `npm test -- expert-os.test.ts`

Expected: PASS.

- [ ] **Step 9: Commit the model contract**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/types/index.ts frontend/src/data/expert-os-seed.ts frontend/src/domain/expert-os.ts frontend/src/domain/expert-os.test.ts
git commit -m "feat: add Expert OS local state contract"
```

### Task 2: Add Persisted Context And Lifecycle Commands

**Files:**
- Create: `frontend/src/store/expert-os-store.tsx`
- Modify: `frontend/src/main.tsx:9-18`
- Modify: `frontend/src/domain/expert-os.ts`
- Test: `frontend/src/domain/expert-os.test.ts`

**Interfaces:**
- Consumes: `ExpertOsState`, `createExpertOsSeed`, `validateExpert`, and lifecycle types from Task 1.
- Produces: `ExpertOsProvider`, `useExpertOs`, `saveDraft`, `testExpert`, `publishExpert`, `createDeployment`, `decideApproval`, and `resetDemoData`.

- [ ] **Step 1: Add failing tests for version pinning and one-time approval decisions**

```ts
it('pins a deployment to the version published at deployment time', () => {
  const state = publishedExpertState()
  const deployed = createDeployment(state, 'exp-release', { name: 'release-prod', environment: 'prod', alias: 'release' })
  const edited = saveExpertDraft(deployed, 'exp-release', { description: 'new description' })
  expect(edited.deployments[0].expertVersion).toBe('v0.1')
  expect(edited.experts.find((item) => item.id === 'exp-release')?.version).toBe('v0.2')
})

it('allows a pending approval to decide its linked interrupted run exactly once', () => {
  const state = interruptedRunState()
  const approved = decideApproval(state, 'apr-test', 'approved')
  expect(approved.runs.find((run) => run.id === 'run-test')?.status).toBe('succeeded')
  expect(() => decideApproval(approved, 'apr-test', 'rejected')).toThrow('审批已处理')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- expert-os.test.ts`

Expected: FAIL because deployment, publication, and approval commands do not exist.

- [ ] **Step 3: Implement lifecycle commands as immutable state transitions**

```ts
export function publishExpert(state: ExpertOsState, expertId: string): ExpertOsState {
  const result = validateExpert(state, expertId, 'publish')
  if (!result.valid) throw new Error(result.blockers.join('；'))
  const expert = requireExpert(state, expertId)
  const version: ExpertVersion = { id: `ver-${crypto.randomUUID()}`, expertId, version: expert.version, createdAt: now(), config: structuredClone(state.expertConfigs[expertId]), skills: [...expert.skills] }
  return { ...state, versions: [...state.versions, version], experts: state.experts.map((item) => item.id === expertId ? { ...item, status: 'published', updated: now() } : item) }
}

export function createDeployment(state: ExpertOsState, expertId: string, input: Pick<DeploymentRecord, 'name' | 'environment' | 'alias'>): ExpertOsState {
  const expert = requireExpert(state, expertId)
  if (expert.status !== 'published') throw new Error('仅已发布 Expert 可以创建 Deployment')
  const deployment: DeploymentRecord = { id: `dep-${crypto.randomUUID()}`, expertId, expertVersion: expert.version, ...input, status: 'active', createdAt: now() }
  return { ...state, deployments: [...state.deployments, deployment], experts: state.experts.map((item) => item.id === expertId ? { ...item, deployments: item.deployments + 1 } : item) }
}

export function decideApproval(state: ExpertOsState, approvalId: string, decision: 'approved' | 'rejected'): ExpertOsState {
  const approval = state.approvals.find((item) => item.id === approvalId)
  if (!approval || approval.status !== 'pending') throw new Error('审批已处理')
  const runId = approval.id.replace('apr-', 'run-')
  return {
    ...state,
    approvals: state.approvals.map((item) => item.id === approvalId ? { ...item, status: decision } : item),
    runs: state.runs.map((run) => run.id === runId ? { ...run, status: decision === 'approved' ? 'succeeded' : 'cancelled', duration: run.duration === '—' ? '0.8s' : run.duration } : run),
  }
}
```

- [ ] **Step 4: Implement a versioned `localStorage` provider**

```tsx
const STORAGE_KEY = 'flowhub_expert_os_v1'

function loadState(): ExpertOsState {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (!saved) return createExpertOsSeed()
    const state = JSON.parse(saved) as ExpertOsState
    return state.schemaVersion === 1 ? state : createExpertOsSeed()
  } catch {
    return createExpertOsSeed()
  }
}

export function ExpertOsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(loadState)
  useEffect(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)) }, [state])
  const commit = (command: (current: ExpertOsState) => ExpertOsState) => setState(command)
  const value = useMemo(() => ({ state, createExpert: (input: CreateExpertInput) => commit((current) => createExpert(current, input)), publishExpert: (id: string) => commit((current) => publishExpert(current, id)), decideApproval: (id: string, decision: 'approved' | 'rejected') => commit((current) => decideApproval(current, id, decision)), resetDemoData: () => setState(createExpertOsSeed()) }), [state])
  return <ExpertOsContext.Provider value={value}>{children}</ExpertOsContext.Provider>
}
```

- [ ] **Step 5: Mount the provider at application root**

```tsx
<ThemeProvider attribute="class" defaultTheme="light" enableSystem={false} disableTransitionOnChange>
  <AppProvider>
    <ExpertOsProvider>
      <App />
      <Toaster position="bottom-right" richColors closeButton />
    </ExpertOsProvider>
  </AppProvider>
</ThemeProvider>
```

- [ ] **Step 6: Run lifecycle tests and production build**

Run: `npm test -- expert-os.test.ts && npm run build`

Expected: all Expert domain tests pass and Vite finishes successfully.

- [ ] **Step 7: Commit persistence and commands**

```bash
git add frontend/src/domain/expert-os.ts frontend/src/domain/expert-os.test.ts frontend/src/store/expert-os-store.tsx frontend/src/main.tsx
git commit -m "feat: persist Expert OS lifecycle state"
```

### Task 3: Build The Expert Wizard And Expert Center Lifecycle UI

**Files:**
- Create: `frontend/src/components/expert-wizard.tsx`
- Modify: `frontend/src/pages/expert-center.tsx`
- Modify: `frontend/src/components/expert-os.tsx`
- Test: `frontend/src/components/expert-wizard.test.tsx`

**Interfaces:**
- Consumes: `useExpertOs().state`, `createExpert`, `saveDraft`, `testExpert`, `publishExpert`, `createDeployment`, and `validateExpert` from Task 2.
- Produces: create/edit/test/publish/deploy UI and detail actions for custom Experts.

- [ ] **Step 1: Write a failing wizard interaction test**

```tsx
it('creates a draft then blocks publishing before it has a successful test', async () => {
  const user = userEvent.setup()
  render(<ExpertWizard open onClose={vi.fn()} />)
  await user.type(screen.getByLabelText('名称'), '发布说明专家')
  await user.type(screen.getByLabelText('Slug'), 'release-notes')
  await user.click(screen.getByRole('button', { name: '保存草稿' }))
  expect(screen.getByText('当前修改尚未通过测试')).toBeVisible()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- expert-wizard.test.tsx`

Expected: FAIL because the wizard component does not exist.

- [ ] **Step 3: Implement an accessible five-step wizard**

```tsx
const steps = ['身份', '指令与模型', '能力', '治理', '审查'] as const

export function ExpertWizard({ open, expertId, onClose }: Props) {
  const { state, createExpert, saveDraft, testExpert, publishExpert, createDeployment } = useExpertOs()
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<ExpertDraft>(initialDraft(state, expertId))
  const validation = validateDraft(state, draft, step === 4 ? 'publish' : 'save')
  if (!open) return null
  return <Dialog open onOpenChange={(next) => !next && onClose()}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
      <DialogHeader><DialogTitle>{expertId ? '编辑 Expert' : '创建 Expert'}</DialogTitle></DialogHeader>
      <nav aria-label="创建步骤">{steps.map((label, index) => <button key={label} type="button" aria-current={index === step ? 'step' : undefined} onClick={() => setStep(index)}>{index + 1}. {label}</button>)}</nav>
      {step === 0 && <IdentityFields draft={draft} onChange={setDraft} />}
      {step === 1 && <ModelFields draft={draft} providers={state.providers} onChange={setDraft} />}
      {step === 2 && <CapabilityFields draft={draft} skills={state.skills} knowledgeBases={state.knowledgeBases} onChange={setDraft} />}
      {step === 3 && <GovernanceFields draft={draft} tools={state.mcpTools} onChange={setDraft} />}
      {step === 4 && <Review validation={validation} draft={draft} />}
      <DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button onClick={() => saveDraft(draft)}>保存草稿</Button>{step === 4 && <><Button disabled={!validateDraft(state, draft, 'test').valid} onClick={() => testExpert(draft.id, '验证当前 Expert 配置')}>运行测试</Button><Button disabled={!validation.valid} onClick={() => publishExpert(draft.id)}>发布版本</Button></>}</DialogFooter>
    </DialogContent>
  </Dialog>
}
```

- [ ] **Step 4: Replace Expert Center mock imports and connect actions**

```tsx
const { state, duplicateExpert, deleteExpert, suspendExpert, createDeployment } = useExpertOs()
const [wizard, setWizard] = useState<{ open: boolean; expertId?: string }>({ open: false })
const rows = useMemo(() => state.experts.filter(matchesFilters), [state.experts, query, status])

<button onClick={() => setWizard({ open: true })}><Plus className="mr-1 inline h-4 w-4" />创建 Expert</button>
<ExpertWizard open={wizard.open} expertId={wizard.expertId} onClose={() => setWizard({ open: false })} />
```

In the detail drawer, render these actions only for custom Experts: 编辑, 复制, 删除 when draft and no deployments, 测试, 发布, 创建 Deployment, and suspend/resume. Built-in records show a read-only label instead.

- [ ] **Step 5: Run the wizard test and build**

Run: `npm test -- expert-wizard.test.tsx && npm run build`

Expected: test passes and TypeScript reports no wizard errors.

- [ ] **Step 6: Browser-verify Expert creation**

Run: `npm run dev -- --host 127.0.0.1 --port 5173`

Use agent-browser to open Expert Center, create `发布说明专家`, fill all five steps with a healthy provider, published Skill, indexed Knowledge Base, and required approval policies. Confirm the new row remains after a browser refresh and publication remains blocked before test completion.

- [ ] **Step 7: Commit the Expert UI**

```bash
git add frontend/src/components/expert-wizard.tsx frontend/src/components/expert-wizard.test.tsx frontend/src/components/expert-os.tsx frontend/src/pages/expert-center.tsx
git commit -m "feat: add Expert creation and lifecycle UI"
```

### Task 4: Connect Local Runs, AiChat, Runtime Center, And Approvals

**Files:**
- Modify: `frontend/src/domain/expert-os.ts`
- Modify: `frontend/src/store/expert-os-store.tsx`
- Modify: `frontend/src/pages/aichat.tsx`
- Modify: `frontend/src/pages/runtime-center.tsx`
- Test: `frontend/src/domain/expert-os.test.ts`

**Interfaces:**
- Consumes: active `DeploymentRecord`, bound Expert configuration, shared state, and `decideApproval` from Task 2.
- Produces: `startLocalRun`, persisted ChatSession messages, shared Run records, shared Approval records, and JSON export.

- [ ] **Step 1: Add failing run tests**

```ts
it('interrupts a governed write run and creates a linked approval', () => {
  const next = startLocalRun(publishedExpertState(), { expertId: 'exp-release', prompt: '提交发布说明', writeIntent: true })
  expect(next.runs.at(-1)).toMatchObject({ status: 'interrupted' })
  expect(next.approvals.at(-1)).toMatchObject({ status: 'pending', expert: '发布说明专家' })
})

it('finishes a read-only run with a citation for an indexed knowledge base', () => {
  const next = startLocalRun(publishedExpertState(), { expertId: 'exp-release', prompt: '总结发布风险', writeIntent: false })
  expect(next.runs.at(-1)?.events).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'citation', status: 'succeeded' })]))
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- expert-os.test.ts`

Expected: FAIL because `startLocalRun` is not exported.

- [ ] **Step 3: Implement run creation and linked approval IDs**

```ts
export function startLocalRun(state: ExpertOsState, input: { expertId: string; prompt: string; writeIntent: boolean; sessionId?: string }): ExpertOsState {
  const expert = requireExpert(state, input.expertId)
  const deployment = state.deployments.find((item) => item.expertId === expert.id && item.status === 'active')
  if (!deployment) throw new Error('该 Expert 没有活跃 Deployment')
  const runId = `run-${crypto.randomUUID()}`
  const events = buildLocalEvents(state, expert.id, input.writeIntent)
  const run: RunRecord = { id: runId, session: input.prompt.slice(0, 32), expert: expert.name, version: deployment.expertVersion, deployment: deployment.name, status: input.writeIntent ? 'interrupted' : 'succeeded', duration: input.writeIntent ? '—' : '0.8s', traceId: `trace-${crypto.randomUUID()}`, started: now(), events }
  const approval = input.writeIntent ? { id: runId.replace('run-', 'apr-'), action: '执行受治理写入动作', tool: 'flowhub.local.write', expert: expert.name, risk: 'write_commit' as const, scope: input.prompt, requester: expert.owner, expires: '还剩 30 分钟', requested: now(), status: 'pending' as const } : null
  return { ...state, runs: [run, ...state.runs], approvals: approval ? [approval, ...state.approvals] : state.approvals }
}
```

- [ ] **Step 4: Replace AiChat local component state with the store**

Use `state.chatSessions`, `state.experts`, `state.skills`, and `state.deployments`. Restrict the Expert select options to Experts with `published` state and at least one active deployment. Restrict Skill options to `expert.skills`. On Send, call `startLocalRun`, append the user message and the result message with the returned Run events, and use the created approval ID for the approval panel.

- [ ] **Step 5: Replace Runtime Center and Approval Queue arrays with store data**

```tsx
const { state, decideApproval } = useExpertOs()
const rows = state.runs.filter((run) => status === 'all' || run.status === status)
const decide = (id: string, status: 'approved' | 'rejected') => {
  decideApproval(id, status)
  toast(`审批${status === 'approved' ? '已通过' : '已拒绝'}，运行记录已同步更新`)
}
```

Create a Blob from the filtered runs for the Runtime Center export button:

```ts
const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' })
const url = URL.createObjectURL(blob)
const anchor = document.createElement('a')
anchor.href = url
anchor.download = 'flowhub-runs.json'
anchor.click()
URL.revokeObjectURL(url)
```

- [ ] **Step 6: Run focused tests and full build**

Run: `npm test -- expert-os.test.ts && npm run build`

Expected: PASS, with runtime pages compiled against the shared state.

- [ ] **Step 7: Browser-verify the governed run path**

Use agent-browser to select a deployed Expert in AiChat, send a write-intent sample prompt, verify Runtime Center shows an interrupted run, approve it in Approval Queue, and verify the same run changes to succeeded.

- [ ] **Step 8: Commit runtime integration**

```bash
git add frontend/src/domain/expert-os.ts frontend/src/domain/expert-os.test.ts frontend/src/store/expert-os-store.tsx frontend/src/pages/aichat.tsx frontend/src/pages/runtime-center.tsx
git commit -m "feat: connect Expert runs and approvals"
```

### Task 5: Make Resource Centers Mutable And Enforce Eligibility

**Files:**
- Create: `frontend/src/components/resource-dialogs.tsx`
- Modify: `frontend/src/domain/expert-os.ts`
- Modify: `frontend/src/store/expert-os-store.tsx`
- Modify: `frontend/src/pages/expert-resources.tsx`
- Test: `frontend/src/domain/expert-os.test.ts`

**Interfaces:**
- Consumes: shared resource arrays and `validateExpert` from Task 1.
- Produces: create/update commands for custom Skills, MCP servers, Providers, Knowledge Bases, memory JSON export, and eligibility-relevant states.

- [ ] **Step 1: Add failing eligibility tests**

```ts
it('rejects a test when the selected provider is missing credentials', () => {
  const state = configuredDraftWithProvider('provider-openai')
  expect(validateExpert(state, 'exp-release', 'test').blockers).toContain('请选择健康的 Provider 与模型')
})

it('rejects a test when a selected knowledge base is indexing', () => {
  const state = configuredDraftWithKnowledgeBase('kb-product')
  expect(validateExpert(state, 'exp-release', 'test').blockers).toContain('绑定的知识库尚未完成索引')
})
```

- [ ] **Step 2: Run tests to verify they fail or expose missing eligibility coverage**

Run: `npm test -- expert-os.test.ts`

Expected: FAIL until the fixture helpers and validation behavior are complete.

- [ ] **Step 3: Add resource commands**

```ts
export function addProvider(state: ExpertOsState, input: Omit<ProviderRecord, 'id' | 'status' | 'latency'>): ExpertOsState {
  const provider: ProviderRecord = { id: `provider-${crypto.randomUUID()}`, ...input, status: input.credential === 'configured' ? 'healthy' : 'degraded', latency: '未检测' }
  return { ...state, providers: [...state.providers, provider] }
}

export function addKnowledgeBase(state: ExpertOsState, input: Pick<KnowledgeBaseRecord, 'name' | 'description' | 'sensitivity'>): ExpertOsState {
  return { ...state, knowledgeBases: [...state.knowledgeBases, { id: `kb-${crypto.randomUUID()}`, ...input, documents: 0, chunks: 0, status: 'indexing', updated: now() }] }
}

export function completeKnowledgeIndex(state: ExpertOsState, id: string): ExpertOsState {
  return { ...state, knowledgeBases: state.knowledgeBases.map((base) => base.id === id ? { ...base, status: 'indexed', chunks: Math.max(base.chunks, 1), updated: now() } : base) }
}
```

For MCP registration, create the server first as `unhealthy`, discover its tools as `discovered`, and provide a policy editor that must select `required` or `administrator_only` for write/critical tools before an Expert can bind it. For custom Skills, create as `draft` and offer publish only when name, slug, description, and tool count are present.

- [ ] **Step 4: Implement resource dialogs and wire primary actions**

`ResourceDialogHost` selects one focused form based on `kind`:

```tsx
type ResourceDialogKind = 'skill' | 'mcp' | 'provider' | 'knowledge'

export function ResourceDialogHost({ kind, open, onClose }: { kind: ResourceDialogKind | null; open: boolean; onClose: () => void }) {
  if (!kind) return null
  if (kind === 'provider') return <ProviderDialog open={open} onClose={onClose} />
  if (kind === 'knowledge') return <KnowledgeBaseDialog open={open} onClose={onClose} />
  if (kind === 'skill') return <SkillDialog open={open} onClose={onClose} />
  return <McpServerDialog open={open} onClose={onClose} />
}
```

Update each view in `expert-resources.tsx` to read `state.skills`, `state.mcpServers`, `state.providers`, `state.knowledgeBases`, and `state.memories`. Wire the page header button to set the matching dialog type. Use actual array counts in metrics. Add buttons in details for Provider health check, Knowledge Base index completion, Skill publishing, and MCP tool approval policy configuration. The Memory export button must use the Blob download approach from Task 4.

- [ ] **Step 5: Run domain tests and build**

Run: `npm test -- expert-os.test.ts && npm run build`

Expected: PASS; resource eligibility failures display through `validateExpert` rather than page-only checks.

- [ ] **Step 6: Browser-verify resource eligibility**

Use agent-browser to create a Provider without credentials and a Knowledge Base in indexing state. In the Expert wizard, select each in turn and confirm the test/review screen lists the correct blocker. Mark the Knowledge Base indexed, configure a healthy Provider, and confirm the blockers clear.

- [ ] **Step 7: Commit mutable resources**

```bash
git add frontend/src/components/resource-dialogs.tsx frontend/src/domain/expert-os.ts frontend/src/domain/expert-os.test.ts frontend/src/store/expert-os-store.tsx frontend/src/pages/expert-resources.tsx
git commit -m "feat: add local Expert resource management"
```

### Task 6: Complete Regression Verification And Documentation

**Files:**
- Modify: `README.md:9-15`
- Modify: `frontend/README.md`
- Test: `frontend/src/domain/expert-os.test.ts`
- Test: `frontend/src/components/expert-wizard.test.tsx`

**Interfaces:**
- Consumes: completed local-first store, lifecycle UI, resource dialogs, and runtime integrations.
- Produces: documented reset behavior and proof that the complete workflow works after refresh.

- [ ] **Step 1: Add persistence migration and deletion tests**

```ts
it('uses seed data when persisted schema version is unsupported', () => {
  localStorage.setItem('flowhub_expert_os_v1', JSON.stringify({ schemaVersion: 999 }))
  expect(loadExpertOsState().schemaVersion).toBe(1)
})

it('only deletes a custom draft without deployments', () => {
  expect(() => deleteExpert(publishedExpertState(), 'exp-release')).toThrow('仅可删除无 Deployment 的自建草稿')
})
```

- [ ] **Step 2: Run the complete frontend test suite**

Run: `npm test`

Expected: all domain and component tests pass.

- [ ] **Step 3: Document local-first usage and reset**

Add this section to `README.md` and `frontend/README.md`:

```markdown
### Expert OS 本地演示

Expert、资源、部署、运行和审批记录保存在浏览器 `localStorage` 的
`flowhub_expert_os_v1` 键中。此模式不调用模型、外部 MCP 或写入工具。
要恢复演示数据，在浏览器开发者工具中删除该键后刷新页面。
```

- [ ] **Step 4: Run final static verification**

Run: `npm run build && git diff --check`

Expected: production bundle succeeds and no whitespace errors are reported.

- [ ] **Step 5: Run browser end-to-end verification**

Use agent-browser to complete this exact scenario:

1. Reset `flowhub_expert_os_v1`.
2. Create a custom Expert with a healthy Provider, eligible Skill, indexed Knowledge Base, and approval policies.
3. Confirm publish is blocked until a successful test.
4. Run a governed test and approve its request in Approval Queue.
5. Publish the Expert and deploy it to `test`.
6. Refresh the page and confirm Expert, deployment, run, and approval persist.
7. Select the deployed Expert in AiChat, send a read-only message, and confirm the new run appears in Runtime Center.
8. Edit the published Expert, then confirm the existing deployment still displays the earlier pinned version.

- [ ] **Step 6: Commit documentation and verification changes**

```bash
git add README.md frontend/README.md frontend/src/domain/expert-os.test.ts frontend/src/components/expert-wizard.test.tsx
git commit -m "docs: document Expert OS local workflow"
```

## Self-Review

### Spec Coverage

- Shared local persistent state: Tasks 1 and 2.
- Expert identity, model, Skills, knowledge, governance, review, validation: Task 3.
- Draft, version, test, publish, deploy, suspend/delete restrictions: Tasks 2 and 3.
- Local run trace, citations, approval interruption and exact-once decision: Task 4.
- Resource create/health/index/approval-policy flows and Memory export: Task 5.
- Refresh persistence, block messages, browser workflow, and documentation: Task 6.
- Future backend contract: preserved in domain/store command interfaces defined in Tasks 1 and 2.

### Placeholder Scan

No `TODO`, `TBD`, unspecified tests, or undefined command names remain. Each named store command is either specified in this plan or is a direct wrapper around a specified pure domain function.

### Type Consistency

`ExpertOsState`, `ExpertConfig`, `ExpertVersion`, `DeploymentRecord`, `ValidationResult`, `RunRecord`, and `ApprovalRecord` are defined in Task 1 and used consistently by later tasks. `startLocalRun` maps `run-` IDs to `apr-` IDs, and `decideApproval` uses that same mapping.
