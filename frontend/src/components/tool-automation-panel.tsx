import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { toast } from '../store/app-store'
import { Button } from './ui/button'
import { ToolResultDocuments } from './tool-result-documents'
interface AutomationJob { id: string; taskId: string; runId?: string; event: string; status: string; attempts: number; error?: string; result: unknown; createdAt: string }
const statusLabels: Record<string, string> = { queued: '等待执行', running: '执行中', succeeded: '已完成', failed: '失败', awaiting_approval: '等待审批' }
export function ToolAutomationPanel({ taskId, allowBackfill = false }: { taskId?: string; allowBackfill?: boolean }) {
  const [items, setItems] = useState<AutomationJob[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const path = `/api/v1/tool-automation-jobs${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`
  useEffect(() => {
    let active = true
    const load = () => api.get<{ items: AutomationJob[] }>(path).then(data => { if (active) { setItems(data.items); setError('') } }).catch(error => { if (active) setError(String(error)) })
    void load(); const timer = setInterval(() => void load(), 10000)
    return () => { active = false; clearInterval(timer) }
  }, [path])
  const act = async (retryId?: string) => {
    setBusy(true)
    try {
      await api.post(retryId ? `/api/v1/tool-automation-jobs/${retryId}/retry` : `/api/v1/tasks/${taskId}/automation/backfill`, retryId ? {} : { generate_summary: true })
      setItems((await api.get<{ items: AutomationJob[] }>(path)).items)
      toast.success(retryId ? '已安排重试' : '已安排归档与总结，完成后仍需人工验收')
    } catch (error) { toast.error(error instanceof Error ? error.message : '自动化请求失败') } finally { setBusy(false) }
  }
  const visible = items.filter(item => !taskId || item.taskId === taskId)
  return <section className="my-4 space-y-3 rounded-xl border bg-white p-4 dark:border-slate-700 dark:bg-slate-900"><div className="flex flex-wrap items-center gap-3"><h3 className="flex-1 text-sm font-semibold">自动化归档任务</h3>{taskId && allowBackfill && <Button size="sm" disabled={busy} onClick={() => void act()}>执行归档并生成待验收总结</Button>}</div><p className="text-xs text-slate-500">归档保留交付物原文件；AI 总结为待人工验收稿。失败可重试，成功记录可下载检查。</p>{error && <p role="alert" className="text-xs text-red-600">{error}</p>}{!visible.length && !error && <p className="text-xs text-slate-500">暂无自动化记录</p>}{visible.map(item => <article key={item.id} className="space-y-2 rounded-lg border p-3 text-xs"><div className="flex items-center gap-2"><b>{item.event === 'node_entered' ? '节点交付物归档' : 'Expert 总结归档'}</b><span>{statusLabels[item.status] ?? item.status}</span><span className="text-slate-500">尝试 {item.attempts} 次 · {item.createdAt}</span></div>{item.error && <p role="alert" className="text-red-600">{item.error}</p>}<ToolResultDocuments result={item.result} />{item.status === 'failed' && <Button variant="outline" size="sm" disabled={busy} onClick={() => void act(item.id)}>重试失败任务</Button>}<details><summary className="cursor-pointer text-slate-500">技术详情</summary><pre className="max-h-60 overflow-auto whitespace-pre-wrap">{JSON.stringify(item, null, 2)}</pre></details></article>)}</section>
}
