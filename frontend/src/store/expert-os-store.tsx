import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../lib/api'
import type { ApprovalRecord, DeploymentRecord, ExpertOsState, ExpertRecord, KnowledgeBaseRecord, McpServerRecord, McpToolRecord, ProviderRecord, RunRecord, SkillRecord } from '../types'

const STORAGE_KEY = 'flowhub_expert_os_v1'

const timestamp = () => new Date().toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })

function seedState(): ExpertOsState {
  return {
    schemaVersion: 1,
    serverSynced: false,
    experts: [], configs: {}, deployments: [], skills: [], mcpServers: [], mcpTools: [], providers: [], knowledgeBases: [], memories: [], runs: [], approvals: [], chatSessions: [],
  }
}

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return seedState()
    const parsed = JSON.parse(raw) as ExpertOsState
    return parsed.schemaVersion === 1 ? parsed : seedState()
  } catch { return seedState() }
}

export interface ExpertDraft {
  id?: string
  name: string
  slug: string
  description: string
  systemPrompt: string
  providerId: string
  model: string
  skills: string[]
  knowledgeBaseIds: string[]
}

/** 发布检查分组，编辑器右栏按此滚动定位到对应表单分区 */
export type ExpertCheckSection = 'basic' | 'model' | 'binding' | 'publish'

export interface ExpertCheckItem {
  key: string
  section: ExpertCheckSection
  label: string
  ok: boolean
  hint?: string
}

/** Provider 可用 = 健康 + 凭据已配置（校验与下拉禁用共用同一判定） */
export const providerUsable = (provider?: ProviderRecord) =>
  !!provider && provider.status === 'healthy' && provider.credential === 'configured'

const SLUG_PATTERN = /^[a-z][a-z0-9-]{0,95}$/

/** 纯函数发布检查：对草稿实时计算，新建未落库时同样给出逐项反馈 */
export function validateDraft(
  draft: ExpertDraft,
  ctx: Pick<ExpertOsState, 'providers' | 'skills' | 'knowledgeBases'> & { testedRevisionMatches?: boolean },
  options: { publishing?: boolean } = {},
): ExpertCheckItem[] {
  const provider = ctx.providers.find((item) => item.id === draft.providerId)
  const usableProvider = providerUsable(provider)
  const unusableReason = !provider ? '' : provider.credential !== 'configured' ? '凭据未配置' : provider.status === 'degraded' ? '状态降级' : provider.status === 'disabled' ? '已停用' : ''
  const staleSkillIds = draft.skills.filter((skillId) => !['published', 'testing'].includes(ctx.skills.find((skill) => skill.id === skillId)?.status ?? ''))
  const staleKnowledgeIds = draft.knowledgeBaseIds.filter((baseId) => ctx.knowledgeBases.find((base) => base.id === baseId)?.status !== 'indexed')
  const modelReady = usableProvider && !!draft.model && provider!.models.includes(draft.model)
  const items: ExpertCheckItem[] = [
    { key: 'name', section: 'basic', label: '名称与描述已填写', ok: !!draft.name.trim() && !!draft.description.trim(), hint: '在「基本信息」中补充名称、描述' },
    { key: 'slug', section: 'basic', label: 'Slug 符合规范（小写字母开头，仅小写字母、数字、连字符）', ok: SLUG_PATTERN.test(draft.slug), hint: draft.slug ? `${draft.slug} 不符合 Slug 规范` : '请在「基本信息」中填写 Slug' },
    { key: 'prompt', section: 'model', label: 'System Prompt 已填写', ok: !!draft.systemPrompt.trim(), hint: '在「模型与指令」中编写系统提示词' },
    {
      key: 'provider',
      section: 'model',
      label: !draft.providerId ? '未选择 Provider 与模型'
        : !usableProvider ? `Provider 暂不可用（${unusableReason}）`
        : !modelReady ? 'Provider 已选择，但未选中其下的有效模型'
        : `Provider 与模型可用（${provider!.name} · ${draft.model}）`,
      ok: modelReady,
      hint: !draft.providerId || !usableProvider || !modelReady ? '在「模型与指令」中选择健康的 Provider 及其下的模型' : undefined,
    },
    { key: 'skills', section: 'binding', label: '绑定的 Skill 均处于可运行状态', ok: staleSkillIds.length === 0, hint: '移除或修复状态异常的 Skill 绑定' },
    { key: 'knowledge', section: 'binding', label: '绑定的知识库已完成索引', ok: staleKnowledgeIds.length === 0, hint: '知识库完成索引后才可用于发布' },
  ]
  if (options.publishing) {
    const saved = !!draft.id
    items.push({
      key: 'tested',
      section: 'publish',
      label: !saved ? '草稿尚未保存，先保存再测试'
        : ctx.testedRevisionMatches ? '当前版本已通过测试'
        : '当前修改尚未通过测试',
      ok: saved && !!ctx.testedRevisionMatches,
      hint: saved && !ctx.testedRevisionMatches ? '每次保存后需重新运行测试才能发布' : '保存草稿后在右栏「测试」中运行用例',
    })
  }
  return items
}

/** 从 mcpServers 平铺派生工具清单（服务端无独立 tool 列表接口） */
export const deriveMcpTools = (servers: McpServerRecord[]): McpToolRecord[] =>
  servers.flatMap((server) => Array.isArray(server.tools) ? server.tools.map((tool) => ({ ...tool, server: tool.server || server.name })) : [])

/** 由名称生成 slug 建议（仅 ASCII 可保留，中文等字符剔除后需用户手填） */
export const suggestSlug = (name: string) =>
  name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 96)

export const isValidSlug = (value: string) => SLUG_PATTERN.test(value)

interface ExpertOsContextValue {
  state: ExpertOsState
  /** 创建或保存草稿；返回专家 id 与落库后的版本 id（供测试会话绑定） */
  createOrSaveExpert: (draft: ExpertDraft) => Promise<{ id: string; versionId: string }>
  validateExpert: (expertId: string, publishing?: boolean) => string[]
  /** 在 AiChat 中测试：创建绑定指定版本的聊天会话（无需 Deployment） */
  createTestChatSession: (name: string, versionId: string, providerModelId: string) => Promise<{ id: string; title: string; providerModelId: string }>
  /** 聊天测试首个回复成功后回写本地「已测试」状态（服务端已在对话路径写 tested_at） */
  markConfigTested: (expertId: string) => void
  testExpert: (expertId: string, prompt: string, writeIntent?: boolean) => Promise<RunRecord>
  publishExpert: (expertId: string) => Promise<void>
  createDeployment: (expertId: string, input: Pick<DeploymentRecord, 'name' | 'environment' | 'alias'>) => Promise<void>
  setDeploymentStatus: (deploymentId: string, status: DeploymentRecord['status']) => Promise<void>
  deleteExpert: (expertId: string) => Promise<void>
  duplicateExpert: (expertId: string) => Promise<void>
  addProvider: (input: { id?: string; name: string; baseUrl: string; models: string[]; apiKey: string; maxContextTokens?: number; modelLimits?: Record<string, { max_context_tokens: number | null; max_output_tokens: number | null }> }) => Promise<void>
  addKnowledgeBase: (input: Pick<KnowledgeBaseRecord, 'name' | 'description' | 'sensitivity'>) => Promise<void>
  finishKnowledgeIndex: (knowledgeBaseId: string) => Promise<void>
  uploadSkill: (file: File, metadata?: { name?: string; version?: string; description?: string }) => Promise<void>
  deleteSkill: (skillId: string) => Promise<void>
  createMcpServer: (input: { name: string; description: string; direction: string; transport: string; endpoint: string; authType: string; credentials: string; tools: { name: string; description: string; risk: string; approval: string }[] }) => Promise<void>
  updateMcpServer: (serverId: string, input: { name: string; description: string; direction: string; transport: string; endpoint: string; authType: string; credentials: string; tools: { name: string; description: string; risk: string; approval: string }[] }) => Promise<void>
  updateMcpTool: (toolId: string, input: { status?: string; enabled?: boolean; risk?: string; approval?: string }) => Promise<void>
  deleteMcpServer: (serverId: string) => Promise<void>
  addRun: (expertId: string, prompt: string, writeIntent?: boolean) => Promise<RunRecord | null>
  /** 补拉审批队列（运行中新产生的审批单不在挂载时加载的列表里） */
  fetchApprovals: () => Promise<void>
  decideApproval: (approvalId: string, decision: 'approved' | 'rejected') => Promise<void>
  reset: () => void
}

const ExpertOsContext = createContext<ExpertOsContextValue | null>(null)

export function ExpertOsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ExpertOsState>(loadState)
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        // 一项资源接口失败（例如当前用户没有 MCP 管理权限）不能让整个 OS
        // 回退到本地缓存；每张概览卡都独立使用本次成功返回的服务端数据。
        const results = await Promise.allSettled([
          api.get<{ items: ExpertRecord[] }>('/api/v1/experts'),
          api.get<{ items: ProviderRecord[] }>('/api/v1/providers'),
          api.get<{ items: DeploymentRecord[] }>('/api/v1/expert-deployments'),
          api.get<{ items: RunRecord[] }>('/api/v1/expert-runs?page_size=100'),
          api.get<{ items: ApprovalRecord[] }>('/api/v1/expert-approvals?status=all'),
          api.get<{ items: SkillRecord[] }>('/api/v1/expert-skills'),
          api.get<{ items: import('../types').McpServerRecord[] }>('/api/v1/mcp-servers'),
        ])
        const fulfilledValue = <T,>(result: PromiseSettledResult<T>, fallback: T): T =>
          result.status === 'fulfilled' ? result.value : fallback
        const experts = fulfilledValue(results[0], { items: [] as ExpertRecord[] })
        const providers = fulfilledValue(results[1], { items: [] as ProviderRecord[] })
        const deployments = fulfilledValue(results[2], { items: [] as DeploymentRecord[] })
        const runs = fulfilledValue(results[3], { items: [] as RunRecord[] })
        const approvals = fulfilledValue(results[4], { items: [] as ApprovalRecord[] })
        const skills = fulfilledValue(results[5], { items: [] as SkillRecord[] })
        const mcp = fulfilledValue(results[6], { items: [] as McpServerRecord[] })
        if (!active) return
        const details = await Promise.all(experts.items.map((expert) => api.get<{ expert: ExpertRecord; versions: { id: string; systemPrompt: string; providerModelId?: string; model?: string; skills: string[]; knowledgeBaseIds: string[]; status?: string; testedAt?: string }[] }>(`/api/v1/experts/${expert.id}`).catch(() => null)))
        if (!active) return
      const configs = Object.fromEntries(details.filter((detail): detail is NonNullable<typeof detail> => !!detail).map((detail) => {
        const version = detail.versions.find((item) => item.id === detail.expert.currentVersionId) ?? detail.versions.at(-1)
        // 版本负载只有 providerModelId（无裸模型名），按 modelEntries 条目精确反查 provider 与模型名
        let providerId = ''
        let model = ''
        if (version?.providerModelId) {
          for (const provider of providers.items) {
            const entry = (provider.modelEntries ?? []).find((item) => item.id === version.providerModelId)
            if (entry) { providerId = provider.id; model = entry.model; break }
          }
        }
        return [detail.expert.id, { versionId: version?.id, systemPrompt: version?.systemPrompt ?? '', providerId, model, knowledgeBaseIds: version?.knowledgeBaseIds ?? [], revision: 1, testedRevision: version?.status === 'published' || version?.testedAt ? 1 : null }]
      }))
        setState((current) => ({ ...current, experts: experts.items, configs: { ...current.configs, ...configs }, providers: providers.items, deployments: deployments.items, runs: runs.items, approvals: approvals.items, skills: skills.items, mcpServers: mcp.items, mcpTools: deriveMcpTools(mcp.items) }))
      } catch { /* 网络不可用时保留上次成功数据，避免概览卡短暂清空 */ }
      if (active) setState((current) => ({ ...current, serverSynced: true }))
    }
    void refresh()
    window.addEventListener('flowhub-auth-changed', refresh)
    return () => { active = false; window.removeEventListener('flowhub-auth-changed', refresh) }
  }, [])
  // The API is authoritative. This cache only prevents a blank screen while the
  // first authenticated request is in flight and is not used for mutations.
  useEffect(() => { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)) }, [state])

  const validateExpert = (expertId: string, publishing = false) => {
    const expert = state.experts.find((item) => item.id === expertId)
    if (!expert) return ['Expert 不存在']
    const config = state.configs[expertId]
    const draft: ExpertDraft = { id: expertId, name: expert.name, slug: expert.slug, description: expert.description, systemPrompt: config?.systemPrompt ?? '', providerId: config?.providerId ?? '', model: config?.model ?? '', skills: expert.skills, knowledgeBaseIds: config?.knowledgeBaseIds ?? [] }
    return validateDraft(draft, { ...state, testedRevisionMatches: config ? config.testedRevision === config.revision : false }, { publishing })
      .filter((item) => !item.ok)
      .map((item) => item.label)
  }

  const createOrSaveExpert = async (draft: ExpertDraft) => {
    // slug 仅创建时可提交（后端不支持修改）；provider_model_id 沿用既有约定：传 provider id，由服务端按模型归属解析
    const basePayload = { name: draft.name.trim(), description: draft.description.trim(), system_prompt: draft.systemPrompt, provider_model_id: draft.providerId, model: draft.model, skills: draft.skills, knowledge_base_ids: draft.knowledgeBaseIds, tool_policies: {} }
    const payload = draft.id ? basePayload : { ...basePayload, slug: draft.slug.trim() }
    const response = draft.id
      ? await api.patch<{ expert: ExpertRecord; version: { id: string } }>('/api/v1/experts/' + draft.id, payload)
      : await api.post<{ expert: ExpertRecord; version: { id: string } }>('/api/v1/experts', payload)
    const expert = response.expert
    setState((current) => ({ ...current, experts: current.experts.some((item) => item.id === expert.id) ? current.experts.map((item) => item.id === expert.id ? expert : item) : [...current.experts, expert], configs: { ...current.configs, [expert.id]: { ...(current.configs[expert.id] ?? {}), versionId: response.version.id, systemPrompt: draft.systemPrompt, providerId: draft.providerId, model: draft.model, knowledgeBaseIds: draft.knowledgeBaseIds, revision: (current.configs[expert.id]?.revision ?? 0) + 1, testedRevision: null } } }))
    return { id: expert.id, versionId: response.version.id }
  }

  const createTestChatSession = async (name: string, versionId: string, providerModelId: string) => {
    const data = await api.post<{ session: { id: string; title: string; providerModelId: string } }>('/api/v1/expert-chat/sessions', { version_id: versionId, provider_model_id: providerModelId, title: name.trim() ? `${name.trim()} 测试` : 'Expert 测试' })
    return data.session
  }

  const markConfigTested = (expertId: string) => {
    setState((current) => {
      const config = current.configs[expertId]
      if (!config || config.testedRevision === config.revision) return current
      return { ...current, configs: { ...current.configs, [expertId]: { ...config, testedRevision: config.revision } } }
    })
  }

  const addRun = async (expertId: string, prompt: string, writeIntent = false) => {
    const expert = state.experts.find((item) => item.id === expertId)
    const deployment = state.deployments.find((item) => item.expertId === expertId && item.status === 'active')
    if (!expert) return null
    if (deployment) {
      try { const response = await api.post<{ run: RunRecord }>('/api/v1/expert-runs', { deployment_id: deployment.id, prompt, write_intent: writeIntent }); setState((current) => ({ ...current, runs: [response.run, ...current.runs] })); return response.run } catch { return null }
    } else {
      return null
    }
  }

  const value = useMemo<ExpertOsContextValue>(() => ({
    state,
    createOrSaveExpert,
    validateExpert,
    createTestChatSession,
    markConfigTested,
    testExpert: async (expertId, prompt, writeIntent = false) => { const detail = await api.get<{ expert: ExpertRecord; versions: { id: string; version: string }[] }>(`/api/v1/experts/${expertId}`); const version = detail.versions.find((item) => item.id === detail.expert.currentVersionId) ?? detail.versions.at(-1); if (!version) throw new Error('Expert Version 不存在'); const result = await api.post<{ run: RunRecord }>(`/api/v1/experts/${expertId}/versions/${version.id}/test`, { prompt, write_intent: writeIntent }); setState((current) => ({ ...current, runs: [result.run, ...current.runs], configs: { ...current.configs, [expertId]: { ...current.configs[expertId], testedRevision: current.configs[expertId]?.revision ?? 1 } }, experts: current.experts.map((item) => item.id === expertId ? { ...item, status: 'testing', lastRun: '刚刚' } : item) })); return result.run },
    publishExpert: async (expertId) => { const detail = await api.get<{ expert: ExpertRecord; versions: { id: string; status: string }[] }>(`/api/v1/experts/${expertId}`); const version = detail.versions.find((item) => item.id === detail.expert.currentVersionId) ?? detail.versions.at(-1); if (!version) throw new Error('Expert Version 不存在'); const result = await api.post<{ expert: ExpertRecord }>(`/api/v1/experts/${expertId}/versions/${version.id}/publish`); setState((current) => ({ ...current, experts: current.experts.map((item) => item.id === expertId ? result.expert : item) })) },
    createDeployment: async (expertId, input) => { const result = await api.post<{ deployment: { id: string; versionId: string; name: string } }>(`/api/v1/experts/${expertId}/deployments`, { name: input.name, environment: input.environment, alias: input.alias }); setState((current) => ({ ...current, deployments: [...current.deployments, { ...input, id: result.deployment.id, expertId, expertVersion: current.experts.find((item) => item.id === expertId)?.version ?? '', status: 'active', createdAt: timestamp() }] })) },
    setDeploymentStatus: async (deploymentId, status) => { const action = status === 'active' ? 'resume' : 'suspend'; await api.post(`/api/v1/expert-deployments/${deploymentId}/${action}`); setState((current) => ({ ...current, deployments: current.deployments.map((item) => item.id === deploymentId ? { ...item, status } : item) })) },
    deleteExpert: async (expertId) => { await api.del(`/api/v1/experts/${expertId}`); setState((current) => ({ ...current, experts: current.experts.filter((item) => item.id !== expertId) })) },
    duplicateExpert: async (expertId) => { const result = await api.post<{ expert: ExpertRecord }>(`/api/v1/experts/${expertId}/duplicate`); setState((current) => ({ ...current, experts: [...current.experts, result.expert] })) },
  addProvider: async (input) => {
    if (input.id) await api.patch(`/api/v1/providers/${input.id}`, { name: input.name, base_url: input.baseUrl, api_key: input.apiKey, models: input.models, max_context_tokens: input.maxContextTokens ?? 32_000, model_limits: input.modelLimits ?? {} })
    else await api.post('/api/v1/providers', { name: input.name, base_url: input.baseUrl, api_key: input.apiKey, models: input.models, max_context_tokens: input.maxContextTokens ?? 32_000, model_limits: input.modelLimits ?? {} })
    const providers = await api.get<{ items: ProviderRecord[] }>('/api/v1/providers')
    setState((current) => ({ ...current, providers: providers.items }))
  },
    addKnowledgeBase: async (input) => { throw new Error(`知识库 API 尚未接入：${input.name}`) },
    finishKnowledgeIndex: async (knowledgeBaseId) => { throw new Error(`知识库索引 API 尚未接入：${knowledgeBaseId}`) },
     uploadSkill: async (file, metadata = {}) => {
       const form = new FormData()
       form.append('file', file)
       if (metadata.name) form.append('name', metadata.name)
       if (metadata.version) form.append('version', metadata.version)
       if (metadata.description) form.append('description', metadata.description)
       const token = localStorage.getItem('flowhub_token')
       const response = await fetch('/api/v1/expert-skills/upload', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body: form })
       const payload = await response.json()
       if (!response.ok || payload.code !== 0) throw new Error(payload.message || 'Skill 上传失败')
       setState((current) => ({ ...current, skills: [payload.data.skill, ...current.skills] }))
     },
     deleteSkill: async (skillId) => { await api.del(`/api/v1/expert-skills/${skillId}`); setState((current) => ({ ...current, skills: current.skills.filter((item) => item.id !== skillId) })) },
     createMcpServer: async (input) => { const result = await api.post<{ server: McpServerRecord }>('/api/v1/mcp-servers', { name: input.name, description: input.description, direction: input.direction, transport: input.transport, endpoint: input.endpoint, auth_type: input.authType, credentials: input.credentials, tools: input.tools }); setState((current) => ({ ...current, mcpServers: [result.server, ...current.mcpServers] })) },
     updateMcpServer: async (serverId, input) => { const result = await api.patch<{ server: McpServerRecord }>(`/api/v1/mcp-servers/${serverId}`, { name: input.name, description: input.description, direction: input.direction, transport: input.transport, endpoint: input.endpoint, auth_type: input.authType, credentials: input.credentials, tools: input.tools }); setState((current) => ({ ...current, mcpServers: current.mcpServers.map((server) => server.id === serverId ? result.server : server) })) },
     updateMcpTool: async (toolId, input) => { const result = await api.patch<{ tool: McpToolRecord }>(`/api/v1/mcp-tools/${toolId}`, input); setState((current) => ({ ...current, mcpServers: current.mcpServers.map((server) => ({ ...server, tools: Array.isArray(server.tools) ? server.tools.map((tool) => tool.id === toolId ? { ...tool, ...result.tool } : tool) : server.tools })) })) },
     deleteMcpServer: async (serverId) => { await api.del(`/api/v1/mcp-servers/${serverId}`); setState((current) => ({ ...current, mcpServers: current.mcpServers.filter((server) => server.id !== serverId) })) },
    addRun,
    fetchApprovals: async () => { const data = await api.get<{ items: ApprovalRecord[] }>('/api/v1/expert-approvals?status=all'); setState((current) => ({ ...current, approvals: data.items })) },
    // 通过审批单上的 runId 关联更新本地 run 状态（服务端 id 前缀无固定映射规则，不能靠字符串替换）
    decideApproval: async (approvalId, decision) => { const target = state.approvals.find((item) => item.id === approvalId); await api.post(`/api/v1/expert-approvals/${approvalId}/${decision === 'approved' ? 'approve' : 'reject'}`, { note: '' }); setState((current) => ({ ...current, approvals: current.approvals.map((item) => item.id === approvalId ? { ...item, status: decision } : item), runs: current.runs.map((run) => run.id === target?.runId ? { ...run, status: decision === 'approved' ? 'succeeded' : 'cancelled' } : run) })) },
    reset: () => setState(seedState()),
  }), [state])
  return <ExpertOsContext.Provider value={value}>{children}</ExpertOsContext.Provider>
}

export function useExpertOs() {
  const context = useContext(ExpertOsContext)
  if (!context) throw new Error('useExpertOs must be used within ExpertOsProvider')
  return context
}
