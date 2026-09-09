import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { BookOpen, Cable, Database, Network, Sparkles, Trash2, Upload } from 'lucide-react'
import { PageHeader } from '../components/common'
import { DetailDrawer, FilterBar, MetricStrip, OsStatusBadge, ResourceTable } from '../components/expert-os'
import { useExpertOs } from '../store/expert-os-store'
import type { KnowledgeBaseRecord, McpServerRecord, MemoryRecord, ProviderRecord, SkillRecord } from '../types'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { toast } from '../store/app-store'
import { api } from '../lib/api'

type ResourceKind = 'skill' | 'mcp' | 'provider' | 'knowledge' | 'memory'
const config: Record<ResourceKind, { title: string; sub: string }> = {
  skill: { title: 'Expert Skill', sub: '版本化的专业能力包、子图、工具契约和策略限制' },
  mcp: { title: 'MCP 中心', sub: 'FlowHub Native Tool、外部 MCP Server 和对外接入能力' },
  provider: { title: 'Provider 中心', sub: '管理模型连接和凭据健康度' },
  knowledge: { title: '知识库', sub: '集合、文档、分块和可追溯检索来源' },
  memory: { title: 'Memory', sub: '按 Expert、任务和运行级命名空间治理记忆留存' },
}

export function ResourceCenterPage({ kind }: { kind: ResourceKind }) {
  const { state, addProvider, uploadSkill, deleteSkill, createMcpServer, updateMcpServer, updateMcpTool, deleteMcpServer } = useExpertOs()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<SkillRecord | McpServerRecord | ProviderRecord | KnowledgeBaseRecord | MemoryRecord | null>(null)
  const [providerOpen, setProviderOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<ProviderRecord | null>(null)
  const [skillBusy, setSkillBusy] = useState(false)
  const [mcpOpen, setMcpOpen] = useState(false)
  const [editingMcp, setEditingMcp] = useState<McpServerRecord | null>(null)
  const [confluenceOpen, setConfluenceOpen] = useState(false)
  const source = kind === 'skill' ? state.skills : kind === 'mcp' ? state.mcpServers : kind === 'provider' ? state.providers : kind === 'knowledge' ? state.knowledgeBases : state.memories
  const rows = useMemo(() => source.filter((item) => JSON.stringify(item).toLowerCase().includes(query.toLowerCase())), [source, query])
  const meta = config[kind]

  const upload = async (file: File) => {
    setSkillBusy(true)
    try { await uploadSkill(file); toast.success('Skill 包上传成功') }
    catch (error) { toast.error(error instanceof Error ? error.message : 'Skill 上传失败') }
    finally { setSkillBusy(false) }
  }
  const removeSkill = async (skill: SkillRecord) => {
    if (!window.confirm(`确认删除 Skill「${skill.name}」？历史 Expert 版本仍会保留。`)) return
    try { await deleteSkill(skill.id); setSelected(null); toast.success('Skill 已删除') }
    catch (error) { toast.error(error instanceof Error ? error.message : 'Skill 删除失败') }
  }

  const actions = kind === 'mcp'
    ? <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-blue-700" onClick={() => { setEditingMcp(null); setMcpOpen(true) }}>新增 MCP Server</button>
    : kind === 'provider'
    ? <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-blue-700" onClick={() => { setEditingProvider(null); setProviderOpen(true) }}>新增 Provider</button>
    : kind === 'skill'
      ? <div className="flex flex-col items-start gap-1.5 sm:items-end"><label className={`inline-flex cursor-pointer items-center gap-2 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-blue-700 ${skillBusy ? 'pointer-events-none opacity-60' : ''}`}><Upload className="h-4 w-4" />{skillBusy ? '上传中…' : '上传 Skill 包'}<input type="file" className="hidden" accept=".tar,.tar.gz,.tgz,.zip,application/x-tar,application/gzip,application/zip" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void upload(file) }} /></label><span className="text-[11px] text-slate-400">仅支持 ZIP、TAR、TAR.GZ、TGZ 压缩包</span></div>
      : undefined

  return <div className="page-container">
    <PageHeader title={meta.title} sub={meta.sub} actions={actions} />
    <MetricStrip items={[{ label: '当前资源', value: rows.length, note: '服务端真实数据', tone: 'info' }, { label: '活跃', value: rows.filter((item) => ['active', 'published', 'healthy', 'indexed'].includes(item.status)).length, note: '可用于 Expert', tone: 'suc' }, { label: '待处理', value: rows.filter((item) => ['draft', 'indexing', 'attention', 'degraded'].includes(item.status)).length, note: '需要处理', tone: 'warn' }]} />
    <FilterBar query={query} onQueryChange={setQuery} placeholder={`搜索${meta.title}…`} />
    {kind === 'skill' && <ResourceTable rows={rows as SkillRecord[]} onRowClick={setSelected} columns={[
      { key: 'name', label: 'Skill', render: (item) => <ResourceName icon={<Sparkles className="h-4 w-4" />} name={item.name} sub={item.slug} /> },
      { key: 'version', label: '版本', render: (item) => item.version },
      { key: 'packageType', label: '包类型', render: (item) => item.packageType?.toUpperCase() ?? '—' },
      { key: 'status', label: '状态', render: (item) => <OsStatusBadge status={item.status} /> },
      { key: 'action', label: '操作', render: (item) => <button type="button" aria-label={`删除 ${item.name}`} className="rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-500/10" onClick={(event) => { event.stopPropagation(); void removeSkill(item) }}><Trash2 className="h-4 w-4" /></button> },
    ]} />}
    {kind === 'mcp' && <ResourceTable rows={rows as McpServerRecord[]} onRowClick={setSelected} columns={[{ key: 'name', label: 'MCP Server', render: (item) => <ResourceName icon={<Cable className="h-4 w-4" />} name={item.name} sub={`${item.direction} · ${item.transport}`} /> }, { key: 'endpoint', label: 'Endpoint', render: (item) => item.builtin ? (item.configured ? '内置 · 已配置' : '内置 · 待配置') : item.endpoint || '内置' }, { key: 'tools', label: 'Tools', render: (item) => Array.isArray(item.tools) ? item.tools.length : item.tools }, { key: 'status', label: '状态', render: (item) => <OsStatusBadge status={item.status} /> }, { key: 'action', label: '操作', render: (item) => <div className="flex items-center gap-1">{item.id === 'builtin-confluence' ? <button type="button" className="rounded-md px-2 py-1 text-[11px] text-blue-600 hover:bg-blue-50" onClick={(event) => { event.stopPropagation(); setConfluenceOpen(true) }}>配置</button> : <><button type="button" className="rounded-md px-2 py-1 text-[11px] text-blue-600 hover:bg-blue-50" onClick={(event) => { event.stopPropagation(); setEditingMcp(item); setMcpOpen(true) }}>编辑</button><button type="button" aria-label={`删除 ${item.name}`} className="rounded-md p-1.5 text-slate-400 hover:text-red-600" onClick={(event) => { event.stopPropagation(); if (window.confirm(`确认删除 MCP Server「${item.name}」？`)) void deleteMcpServer(item.id).then(() => toast.success('MCP Server 已删除')).catch((error: unknown) => toast.error(error instanceof Error ? error.message : '删除失败')) }}><Trash2 className="h-4 w-4" /></button></>}</div> }]} />}
    {kind === 'provider' && <ResourceTable rows={rows as ProviderRecord[]} onRowClick={(item) => { setEditingProvider(item); setProviderOpen(true) }} columns={[{ key: 'name', label: 'Provider', render: (item) => <ResourceName icon={<Network className="h-4 w-4" />} name={item.name} sub={item.provider} /> }, { key: 'models', label: '模型', render: (item) => item.models.join(', ') }, { key: 'status', label: '状态', render: (item) => <OsStatusBadge status={item.status} /> }]} />}
    {kind === 'knowledge' && <ResourceTable rows={rows as KnowledgeBaseRecord[]} onRowClick={setSelected} columns={[{ key: 'name', label: '知识库', render: (item) => <ResourceName icon={<BookOpen className="h-4 w-4" />} name={item.name} sub={item.description} /> }, { key: 'documents', label: '文档', render: (item) => item.documents }, { key: 'status', label: '状态', render: (item) => <OsStatusBadge status={item.status} /> }]} />}
    {kind === 'memory' && <ResourceTable rows={rows as MemoryRecord[]} onRowClick={setSelected} columns={[{ key: 'name', label: 'Namespace', render: (item) => <ResourceName icon={<Database className="h-4 w-4" />} name={item.namespace} sub={item.owner} /> }, { key: 'content', label: '内容', render: (item) => <span className="max-w-[420px] truncate">{item.content}</span> }, { key: 'status', label: '状态', render: (item) => <OsStatusBadge status={item.status} /> }]} />}
    <DetailDrawer open={!!selected && kind !== 'provider'} title={selected && 'name' in selected ? selected.name : ''} eyebrow={meta.title} onClose={() => setSelected(null)}>{selected && <div className="space-y-3 text-sm"><p className="text-slate-500">{'description' in selected ? selected.description : '资源详情'}</p>{kind === 'skill' && <div className="grid grid-cols-2 gap-2 text-xs"><span>文件：{(selected as SkillRecord).filename ?? '—'}</span><span>类型：{(selected as SkillRecord).packageType ?? '—'}</span>{!(selected as SkillRecord).builtin && <button className="col-span-2 mt-2 rounded-lg border border-red-200 px-3 py-2 text-red-600 hover:bg-red-50" onClick={() => void removeSkill(selected as SkillRecord)}>删除 Skill</button>}</div>}{kind === 'mcp' && <>{(selected as McpServerRecord).id === 'builtin-confluence' ? <button className="rounded-lg border border-blue-200 px-3 py-2 text-xs text-blue-600 hover:bg-blue-50" onClick={() => setConfluenceOpen(true)}>配置 Confluence</button> : <button className="rounded-lg border border-blue-200 px-3 py-2 text-xs text-blue-600 hover:bg-blue-50" onClick={() => { setEditingMcp(selected as McpServerRecord); setMcpOpen(true) }}>编辑 JSON 配置</button>}<McpToolList server={selected as McpServerRecord} onUpdate={updateMcpTool} /></>}</div>}</DetailDrawer>
    {kind === 'provider' && <ProviderDialog open={providerOpen} provider={editingProvider} onClose={() => setProviderOpen(false)} onSave={async (input) => { await addProvider(input) }} />}
    {kind === 'mcp' && <McpServerDialog open={mcpOpen} server={editingMcp} onClose={() => setMcpOpen(false)} onSave={async (input) => { if (editingMcp) await updateMcpServer(editingMcp.id, input); else await createMcpServer(input); setMcpOpen(false); setSelected(null); toast.success(editingMcp ? 'MCP Server 已更新' : 'MCP Server 已创建') }} />}
    <ConfluenceDialog open={confluenceOpen} onClose={() => setConfluenceOpen(false)} onSaved={() => window.location.reload()} />
  </div>
}

function McpToolList({ server, onUpdate }: { server: McpServerRecord; onUpdate: (id: string, input: { status?: string; enabled?: boolean }) => Promise<void> }) {
  const tools = Array.isArray(server.tools) ? server.tools : []
  return <div className="space-y-2"><h3 className="text-sm font-semibold">Tools（{tools.length}）</h3>{tools.length ? tools.map((tool) => <div key={tool.id} className="flex items-center gap-2 rounded-lg border border-slate-200 p-2 text-xs dark:border-slate-700"><span className="min-w-0 flex-1"><b className="block truncate">{tool.name}</b><span className="text-slate-400">{tool.risk} · {tool.approval}</span></span><button className="rounded border px-2 py-1" onClick={() => void onUpdate(tool.id, { status: tool.status === 'disabled' ? 'approved' : 'disabled', enabled: tool.status === 'disabled' })}>{tool.status === 'disabled' ? '启用' : '禁用'}</button></div>) : <p className="text-xs text-slate-400">暂无已发现 Tool</p>}</div>
}

function ConfluenceDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: () => void }) {
  const [baseUrl, setBaseUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [verifySsl, setVerifySsl] = useState(true)
  const [timeoutSeconds, setTimeoutSeconds] = useState('30')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    if (!baseUrl.trim() || !username.trim() || !password) { toast.error('请填写地址、账号和密码'); return }
    setBusy(true)
    try {
      await api.put('/api/v1/mcp-servers/confluence/config', { base_url: baseUrl.trim(), username: username.trim(), password, verify_ssl: verifySsl, timeout_seconds: Number(timeoutSeconds) || 30 })
      const tested = await api.post<{ ok: boolean; health: string; error?: string }>('/api/v1/mcp-servers/confluence/test')
      if (!tested.ok) throw new Error(tested.error || tested.health || '连接检测失败')
      toast.success('Confluence 已配置并连接成功'); onSaved(); onClose()
    } catch (error) { toast.error(error instanceof Error ? error.message : 'Confluence 配置失败') } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={(next) => !next && onClose()}><DialogContent><DialogHeader><DialogTitle>配置内置 Confluence MCP</DialogTitle></DialogHeader><div className="space-y-3"><label className="grid gap-1 text-sm">Confluence 地址<Input placeholder="https://confluence.example.com" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label><label className="grid gap-1 text-sm">账号<Input value={username} onChange={(event) => setUsername(event.target.value)} /></label><label className="grid gap-1 text-sm">密码<Input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label><div className="flex items-center gap-4 text-sm"><label className="flex items-center gap-2"><input type="checkbox" checked={verifySsl} onChange={(event) => setVerifySsl(event.target.checked)} />校验证书</label><label className="flex items-center gap-2">超时（秒）<Input className="w-20" type="number" min="1" max="120" value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(event.target.value)} /></label></div><p className="text-xs text-slate-400">密码会加密保存，不会在页面或接口响应中回显。</p></div><DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={busy} onClick={() => void save()}>{busy ? '保存并检测中…' : '保存并检测'}</Button></DialogFooter></DialogContent></Dialog>
}

function McpServerDialog({ open, server, onClose, onSave }: { open: boolean; server: McpServerRecord | null; onClose: () => void; onSave: (input: { name: string; description: string; direction: string; transport: string; endpoint: string; authType: string; credentials: string; tools: { name: string; description: string; risk: string; approval: string }[] }) => Promise<void> }) {
  const [json, setJson] = useState('{}')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!open) return; setJson(JSON.stringify(server ? { name: server.name, description: server.description ?? '', direction: server.direction, transport: server.transport, endpoint: server.endpoint, auth_type: server.authType ?? 'none', credentials: '', tools: Array.isArray(server.tools) ? server.tools.map((tool) => ({ name: tool.name, description: tool.description ?? '', input_schema: tool.inputSchema ?? {}, risk: tool.risk, approval: tool.approval })) : [] } : { name: 'FlowHub MCP', description: '', direction: 'outbound', transport: 'streamable-http', endpoint: '', auth_type: 'none', credentials: '', tools: [] }, null, 2)) }, [open, server])
  const submit = async () => { setBusy(true); try { const parsed = JSON.parse(json) as Record<string, unknown>; if (!parsed.name || typeof parsed.name !== 'string') throw new Error('JSON 必须包含字符串字段 name'); const rawTools = Array.isArray(parsed.tools) ? parsed.tools : []; await onSave({ name: parsed.name, description: typeof parsed.description === 'string' ? parsed.description : '', direction: typeof parsed.direction === 'string' ? parsed.direction : 'outbound', transport: typeof parsed.transport === 'string' ? parsed.transport : 'streamable-http', endpoint: typeof parsed.endpoint === 'string' ? parsed.endpoint : '', authType: typeof parsed.auth_type === 'string' ? parsed.auth_type : 'none', credentials: typeof parsed.credentials === 'string' ? parsed.credentials : '', tools: rawTools.map((tool: unknown) => { const item = tool as Record<string, unknown>; return { name: String(item.name ?? ''), description: String(item.description ?? ''), risk: String(item.risk ?? 'read'), approval: String(item.approval ?? 'none') } }) }) } catch (error) { toast.error(error instanceof Error ? error.message : 'JSON 配置无效') } finally { setBusy(false) } }
  return <Dialog open={open} onOpenChange={(next) => !next && onClose()}><DialogContent className="sm:max-w-[720px]"><DialogHeader><DialogTitle>{server ? `编辑 MCP Server · ${server.name}` : '新增 MCP Server'}</DialogTitle></DialogHeader><label className="grid gap-1 text-sm">JSON 配置<textarea aria-label="MCP Server JSON 配置" value={json} onChange={(event) => setJson(event.target.value)} spellCheck={false} className="min-h-[360px] w-full rounded-lg border border-slate-300 bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-100 outline-none focus:border-blue-500 dark:border-slate-700" /></label><p className="text-xs text-slate-400">支持 name、description、direction、transport、endpoint、auth_type、credentials、tools 字段。credentials 只在新增或更换凭据时填写。</p><DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={busy} onClick={() => void submit()}>{busy ? '保存中…' : server ? '保存修改' : '创建 Server'}</Button></DialogFooter></DialogContent></Dialog>
}

function ResourceName({ icon, name, sub }: { icon: ReactNode; name: string; sub: string }) { return <div className="flex min-w-[220px] items-center gap-2.5"><span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-500/15">{icon}</span><span><b className="block font-medium text-slate-800 dark:text-slate-100">{name}</b><span className="block max-w-[320px] truncate text-[10.5px] text-slate-400">{sub}</span></span></div> }

function ProviderDialog({ open, provider, onClose, onSave }: { open: boolean; provider: ProviderRecord | null; onClose: () => void; onSave: (input: { id?: string; name: string; baseUrl: string; models: string[]; apiKey: string; maxContextTokens?: number; modelLimits?: Record<string, { max_context_tokens: number | null; max_output_tokens: number | null }>; visionModels?: string[] }) => Promise<void> }) {
  const [name, setName] = useState(provider?.name ?? '')
  const [baseUrl, setBaseUrl] = useState(provider?.baseUrl ?? '')
  const [models, setModels] = useState(provider?.models.join(', ') ?? '')
  const [apiKey, setApiKey] = useState('')
  const [maxContextTokens, setMaxContextTokens] = useState(String(provider?.maxContextTokens ?? 32_000))
  const [modelLimits, setModelLimits] = useState<Record<string, { max_context_tokens: number | null; max_output_tokens: number | null }>>({})
  const [visionModels, setVisionModels] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [tested, setTested] = useState(false)
  const [testResult, setTestResult] = useState('')
  useEffect(() => { setName(provider?.name ?? ''); setBaseUrl(provider?.baseUrl ?? ''); setModels(provider?.models.join(', ') ?? ''); setApiKey(''); setModelLimits(Object.fromEntries((provider?.modelEntries ?? []).map((entry) => [entry.model, { max_context_tokens: entry.maxContextTokens ?? null, max_output_tokens: entry.maxOutputTokens ?? null }]))); setVisionModels((provider?.modelEntries ?? []).filter((entry) => entry.supportsVision).map((entry) => entry.model)); setMaxContextTokens(String(provider?.maxContextTokens ?? 32_000)); setTested(false); setTestResult('') }, [provider, open])
  const test = async () => { if (!name.trim() || !baseUrl.trim() || !models.trim() || !apiKey.trim()) { toast.error('检测需要填写 Base URL、模型和 API Key'); return }; setBusy(true); try { const result = await api.post<{ ok: boolean; latencyMs: number; error?: string }>('/api/v1/providers/test', { base_url: baseUrl.trim(), api_key: apiKey, model: models.split(',')[0].trim() }); setTested(result.ok); setTestResult(result.ok ? `连接成功 · ${result.latencyMs}ms` : `连接失败 · ${result.error ?? '未知错误'}`) } catch (error) { setTestResult(error instanceof Error ? error.message : '检测请求失败') } finally { setBusy(false) } }
  const submit = async () => { if (!tested) return; setBusy(true); try { await onSave({ id: provider?.id, name: name.trim(), baseUrl: baseUrl.trim(), models: models.split(',').map((item) => item.trim()).filter(Boolean), apiKey, maxContextTokens: Number(maxContextTokens) || 32_000, modelLimits, visionModels }); onClose() } finally { setBusy(false) } }
  return <Dialog open={open} onOpenChange={(next) => !next && onClose()}><DialogContent><DialogHeader><DialogTitle>{provider ? `编辑 Provider · ${provider.name}` : '新增 Provider'}</DialogTitle></DialogHeader><div className="space-y-3"><label className="grid gap-1 text-sm">名称<Input value={name} onChange={(event) => { setName(event.target.value); setTested(false) }} /></label><label className="grid gap-1 text-sm">Base URL<Input value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setTested(false) }} /></label><label className="grid gap-1 text-sm">模型列表<Input value={models} onChange={(event) => { setModels(event.target.value); setTested(false) }} /></label><label className="grid gap-1 text-sm">最大上下文窗口<Input value={maxContextTokens} onChange={(event) => setMaxContextTokens(event.target.value)} /></label>{models.split(',').map((model) => model.trim()).filter(Boolean).map((model) => <div key={model} className="rounded border p-2 space-y-2"><div className="flex items-center justify-between"><b className="text-xs">{model}</b><label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={visionModels.includes(model)} onChange={(event) => setVisionModels((current) => event.target.checked ? [...current, model] : current.filter((item) => item !== model))} />支持视觉</label></div><div className="grid grid-cols-2 gap-2"><label className="text-xs">上下文窗口（留空继承）<Input type="number" min={8000} value={modelLimits[model]?.max_context_tokens ?? ''} onChange={(event) => setModelLimits((current) => ({ ...current, [model]: { max_output_tokens: current[model]?.max_output_tokens ?? null, max_context_tokens: event.target.value ? Number(event.target.value) : null } }))} /></label><label className="text-xs">输出上限（默认 4096）<Input type="number" min={256} value={modelLimits[model]?.max_output_tokens ?? ''} onChange={(event) => setModelLimits((current) => ({ ...current, [model]: { max_context_tokens: current[model]?.max_context_tokens ?? null, max_output_tokens: event.target.value ? Number(event.target.value) : null } }))} /></label></div></div>)}<label className="grid gap-1 text-sm">API Key<Input type="password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setTested(false) }} /></label>{testResult && <div className="rounded-lg bg-slate-50 p-2 text-xs">{testResult}</div>}</div><DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button variant="outline" disabled={busy} onClick={() => void test()}>{busy ? '检测中…' : '检测连接'}</Button><Button disabled={busy || !tested} onClick={() => void submit()}>{provider ? '保存修改' : '创建 Provider'}</Button></DialogFooter></DialogContent></Dialog>
}
