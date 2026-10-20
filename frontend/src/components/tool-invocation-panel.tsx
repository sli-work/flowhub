import { ToolResultDocuments } from './tool-result-documents'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { ToolInvocation } from '../lib/tools'
import { Button } from './ui/button'
import { ToolDetailDrawer as DetailDrawer } from './tool-detail-drawer'
import { toast } from '../store/app-store'

export function ToolInvocationPanel({ runId, toolVersionId, pendingOnly = false }: { runId?: string; toolVersionId?: string; pendingOnly?: boolean }) {
  const [items, setItems] = useState<ToolInvocation[]>([])
  const [selected, setSelected] = useState<ToolInvocation | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const query = new URLSearchParams({ ...(runId ? { run_id: runId } : {}), ...(toolVersionId ? { tool_version_id: toolVersionId } : {}), ...(pendingOnly ? { status: 'awaiting_approval' } : {}) })
  const path = `/api/v1/tool-invocations?${query}`
  useEffect(() => {
    let active = true
    const refresh = () => api.get<{ items: ToolInvocation[] }>(path).then(data => { if (active) { setItems(data.items); setError('') } }).catch(error => { if (active) setError(String(error)) })
    void refresh(); const timer = window.setInterval(() => void refresh(), 10000)
    return () => { active = false; clearInterval(timer) }
  }, [path])
  const decide = async (decision: 'approved' | 'rejected') => {
    if (!selected) return
    setBusy(true)
    try {
      await api.post(`/api/v1/tool-invocations/${selected.id}/decision`, { decision })
      const data = await api.get<{ items: ToolInvocation[] }>(path)
      setItems(data.items); setSelected(data.items.find(item => item.id === selected.id) ?? null)
      toast.success(decision === 'approved' ? '审批已通过，执行结果以调用记录为准' : '调用已拒绝')
    } catch (error) { toast.error(error instanceof Error ? error.message : '审批失败') } finally { setBusy(false) }
  }
  const visibleItems = items.filter(item => (!runId || item.runId === runId) && (!toolVersionId || item.toolVersionId === toolVersionId) && (!pendingOnly || item.status === 'awaiting_approval'))
  return <section className="space-y-3"><h3 className="text-sm font-semibold">工具调用{pendingOnly ? '审批' : '与结果'}</h3>{error && <p role="alert" className="text-xs text-red-600">{error}</p>}{!visibleItems.length && !error && <p className="text-xs text-slate-500">暂无调用记录</p>}{visibleItems.map(item => <button key={item.id} className="block w-full rounded-lg border p-3 text-left text-xs hover:border-blue-400" onClick={() => setSelected(item)}><b>查看工具调用</b><p>{item.status} · {item.approvalStatus} · {item.createdAt}</p></button>)}<DetailDrawer open={!!selected} onClose={() => setSelected(null)} title="工具调用详情" eyebrow="输入、执行结果与审批">{selected && <div className="space-y-3 text-xs"><p>状态：{selected.status} · 审批：{selected.approvalStatus}</p><details><summary>技术标识</summary><p className="break-all">调用：{selected.id}<br />Run：{selected.runId ?? '—'} / Task：{selected.taskId ?? '—'}<br />工具版本：{selected.toolVersionId}</p></details><h4>输入</h4><pre className="max-h-60 overflow-auto whitespace-pre-wrap">{JSON.stringify(selected.input, null, 2)}</pre><h4>结果与归档文档引用</h4><pre className="max-h-80 overflow-auto whitespace-pre-wrap">{JSON.stringify(selected.result, null, 2)}</pre><ToolResultDocuments result={selected.result} />{selected.error && <p role="alert" className="text-red-600">{selected.error}</p>}{selected.status === 'awaiting_approval' && selected.approvalStatus === 'pending' && <div className="flex gap-2"><Button disabled={busy} onClick={() => void decide('approved')}>批准执行</Button><Button disabled={busy} variant="outline" onClick={() => void decide('rejected')}>拒绝</Button></div>}</div>}</DetailDrawer></section>
}
