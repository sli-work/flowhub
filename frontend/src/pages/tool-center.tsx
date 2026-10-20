import { ToolExecutionSettings } from '../components/tool-execution-settings'
import { ToolDetailDrawer as DetailDrawer } from '../components/tool-detail-drawer'
import { useEffect, useState } from 'react'
import { PageHeader } from '../components/common'
import { FilterBar, ResourceTable } from '../components/expert-os'
import { Button } from '../components/ui/button'
import { api } from '../lib/api'
import { fetchTools, type ToolDefinition, type ToolVersion } from '../lib/tools'
import { parseToolArguments } from '../lib/tool-contract.mjs'
import { ToolInvocationPanel } from '../components/tool-invocation-panel'
import { toast } from '../store/app-store'

export function ToolCenterPage() {
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('all')
  const [status, setStatus] = useState('all')
  const [selected, setSelected] = useState<ToolDefinition | null>(null)
  const [versionId, setVersionId] = useState('')
  const [tab, setTab] = useState('contract')
  const [argumentsText, setArgumentsText] = useState('{}')
  const [executeTest, setExecuteTest] = useState(false)
  const [taskId, setTaskId] = useState('')
  const [result, setResult] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const refresh = async () => { const items = await fetchTools(); setTools(items); setError(''); setSelected(current => current ? items.find(item => item.id === current.id) ?? null : null) }
  useEffect(() => { fetchTools().then(setTools).catch(error => setError(String(error))) }, [])
  const version = selected?.versions.find(item => item.id === versionId)
  const runAction = async (action: 'test' | 'publish' | 'status') => {
    if (!selected || !version) return
    setBusy(true)
    try {
      const response = action === 'status'
        ? await api.patch(`/api/v1/tools/${selected.id}`, { status: selected.status === 'active' ? 'disabled' : 'active' })
        : await api.post(`/api/v1/tools/${selected.id}/versions/${version.id}/${action}`, action === 'test' ? { arguments: parseToolArguments(argumentsText), ...(taskId.trim() ? { task_id: taskId.trim() } : {}), dry_run: !executeTest } : {})
      setResult(response); await refresh(); toast.success(action === 'test' ? '测试请求已处理，请检查执行或审批状态' : action === 'publish' ? '工具版本已发布' : '工具状态已更新')
    } catch (error) { toast.error(error instanceof Error ? error.message : '操作失败') } finally { setBusy(false) }
  }
  const rows = tools.filter(tool => (source === 'all' || tool.source === source) && (status === 'all' || tool.status === status) && `${tool.name} ${tool.slug} ${tool.description}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="page-container"><PageHeader title="工具中心" sub="发布可执行契约，再授权给 Expert；MCP 连接在 MCP 中心配置" actions={<Button variant="outline" onClick={() => void refresh().catch(error => toast.error(String(error)))}>刷新目录与测试状态</Button>} />{error && <p role="alert" className="text-red-600">加载失败：{error}</p>}<FilterBar query={query} onQueryChange={setQuery} placeholder="搜索工具名称或说明"><select aria-label="工具来源" value={source} onChange={event => setSource(event.target.value)}><option value="all">全部来源</option><option value="native">FlowHub 原生</option><option value="mcp">MCP</option></select><select aria-label="工具状态" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></FilterBar><ResourceTable rows={rows} onRowClick={tool => { setSelected(tool); setVersionId(tool.currentVersionId ?? tool.versions.at(-1)?.id ?? ''); setResult(null); setExecuteTest(false); setTab('contract') }} columns={[{ key: 'name', label: '工具', render: tool => <div><b>{tool.name}</b><p className="text-xs text-slate-500">{tool.description}</p></div> }, { key: 'source', label: '来源', render: tool => tool.source === 'native' ? 'FlowHub 原生' : 'MCP' }, { key: 'status', label: '状态', render: tool => tool.status === 'active' ? '启用' : '停用' }, { key: 'versions', label: '版本', render: tool => tool.versions.length }]} /><DetailDrawer open={!!selected} onClose={() => setSelected(null)} title={selected?.name ?? ''} eyebrow="可执行工具契约">{selected && <div className="space-y-4 text-sm"><p>{selected.description}</p><label className="block">固定版本 <select aria-label="工具版本" value={versionId} onChange={event => { setVersionId(event.target.value); setResult(null) }}>{selected.versions.map(item => <option key={item.id} value={item.id}>v{item.revision} · {item.status}</option>)}</select></label><div role="tablist" aria-label="工具详情" className="flex gap-2">{[['contract', '契约'], ['settings', '执行设置'], ['test', '测试与发布'], ['usage', '调用记录']].map(([id, label]) => <Button key={id} role="tab" aria-selected={tab === id} variant={tab === id ? 'default' : 'outline'} onClick={() => setTab(id)}>{label}</Button>)}</div>{version && tab === 'contract' && <Contract version={version} source={selected.source} />}{version && tab === 'settings' && <ToolExecutionSettings key={version.id} tool={selected} version={version} onCreated={async (id) => { await refresh(); setVersionId(id); setResult(null) }} />}{version && tab === 'test' && <div className="space-y-3"><p className="text-xs text-slate-500">默认仅预览，不写入业务数据。预览不解锁写工具发布；实际执行测试需审批并成功完成。输入需符合契约。</p><label className="block">任务 ID（需要任务上下文时填写）<input className="block w-full rounded border p-2" value={taskId} onChange={event => setTaskId(event.target.value)} /></label><label className="block">JSON 参数<textarea rows={8} className="block w-full rounded border bg-transparent p-2 font-mono" value={argumentsText} onChange={event => setArgumentsText(event.target.value)} /></label><label className="flex gap-2"><input type="checkbox" checked={executeTest} onChange={event => setExecuteTest(event.target.checked)} />实际执行测试（可能写入业务或外部系统，遵循工具审批）</label><div className="flex gap-2"><Button disabled={busy || selected.status !== 'active'} onClick={() => void runAction('test')}>{executeTest ? '提交实际执行测试' : '预览测试'}</Button><Button disabled={busy || version.status !== 'tested' || !version.testedAt || selected.status !== 'active'} onClick={() => void runAction('publish')}>发布版本</Button><Button variant="outline" disabled={busy} onClick={() => void runAction('status')}>{selected.status === 'active' ? '停用工具' : '恢复工具'}</Button></div><p className="text-xs text-slate-500">已发布契约不可修改；发布需要当前版本测试通过。</p>{result !== null && <ToolInvocationPanel toolVersionId={version.id} />}{result !== null && <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(result, null, 2)}</pre>}</div>}{tab === 'usage' && <ToolInvocationPanel toolVersionId={versionId} />}</div>}</DetailDrawer></div>
}
function Contract({ version, source }: { version: ToolVersion; source: string }) {
  return <div className="space-y-3"><p>来源：{source} · 风险：{version.risk} · 审批：{version.approval}</p>{[['输入 Schema', version.inputSchema], ['输出 Schema', version.outputSchema]].map(([label, schema]) => <div key={String(label)}><h3 className="font-semibold">{String(label)}</h3><pre className="max-h-72 overflow-auto rounded border p-3 text-xs">{JSON.stringify(schema, null, 2)}</pre></div>)}</div>
}
