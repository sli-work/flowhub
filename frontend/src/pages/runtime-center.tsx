import { useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, Eye, ShieldCheck, XCircle } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { Badge, PageHeader } from '../components/common'
import { DetailDrawer, FilterBar, MetricStrip, OsStatusBadge, ResourceTable } from '../components/expert-os'
import { useExpertOs } from '../store/expert-os-store'
import { api } from '../lib/api'
import type { ApprovalRecord, RunRecord } from '../types'

export function RuntimeCenterPage() {
  const [query, setQuery] = useState(''); const [status, setStatus] = useState('all'); const [selected, setSelected] = useState<RunRecord | null>(null); const [page, setPage] = useState(1); const [data, setData] = useState<{ items: RunRecord[]; total: number; metrics: { running: number; interrupted: number; succeeded: number; failed: number; success_rate: number | null } }>({ items: [], total: 0, metrics: { running: 0, interrupted: 0, succeeded: 0, failed: 0, success_rate: null } })
  useEffect(() => { const timer = window.setTimeout(() => api.get<typeof data>(`/api/v1/expert-runs?page=${page}&page_size=20&status=${status === 'all' ? '' : status}&q=${encodeURIComponent(query)}`).then(setData).catch(() => {}), 180); return () => clearTimeout(timer) }, [page, status, query])
  const totalPages = Math.max(1, Math.ceil(data.total / 20)); const rows = data.items
  return <div className="page-container"><PageHeader title="运行中心" sub="监控 LangGraph Run、Trace 和 Tool Call，定位失败与审批中断" /><MetricStrip items={[{ label: '运行中', value: data.metrics.running, note: '实时状态', tone: 'info' }, { label: '已中断', value: data.metrics.interrupted, note: '等待审批', tone: 'warn' }, { label: '成功率', value: data.metrics.success_rate === null ? '—' : `${data.metrics.success_rate}%`, note: '成功 / 成功+失败', tone: 'suc' }, { label: '成功', value: data.metrics.succeeded, note: '全部历史', tone: 'suc' }, { label: '失败', value: data.metrics.failed, note: '全部历史', tone: 'err' }]} /><FilterBar query={query} onQueryChange={(v) => { setQuery(v); setPage(1) }} placeholder="搜索 Run ID、Session 或 Expert…"><select aria-label="筛选运行状态" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1) }} className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-[12px] text-slate-600 outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"><option value="all">全部状态</option><option value="running">运行中</option><option value="interrupted">已中断</option><option value="succeeded">成功</option><option value="failed">失败</option></select></FilterBar><ResourceTable rows={rows} onRowClick={setSelected} columns={[{ key: 'id', label: 'Run / Trace', render: (run) => <div className="min-w-[155px]"><b className="block font-mono text-[11.5px] text-slate-700 dark:text-slate-200">{run.id}</b><span className="font-mono text-[10px] text-slate-400">{run.traceId}</span></div> }, { key: 'session', label: 'Session', render: (run) => <div className="min-w-[180px]"><b className="block text-[12px] font-medium text-slate-700 dark:text-slate-200">{run.session}</b><span className="text-[10.5px] text-slate-400">{run.expert} · {run.version}</span></div> }, { key: 'deployment', label: 'Deployment', render: (run) => <span className="font-mono text-[10.5px]">{run.deployment}</span> }, { key: 'status', label: '状态', render: (run) => <OsStatusBadge status={run.status} /> }, { key: 'duration', label: '耗时', render: (run) => run.duration }, { key: 'started', label: '开始时间', render: (run) => <span className="whitespace-nowrap text-slate-400">{run.started}</span> }, { key: 'action', label: '', render: () => <Eye className="h-4 w-4" /> }]} /><div className="mt-3 flex items-center justify-between text-xs text-slate-400"><span>共 {data.total} 条，第 {page}/{totalPages} 页</span><div className="flex gap-2"><button disabled={page === 1} onClick={() => setPage(page - 1)}>上一页</button><button disabled={page === totalPages} onClick={() => setPage(page + 1)}>下一页</button></div></div>
<DetailDrawer open={!!selected} title={selected ? `Run ${selected.id}` : ''} eyebrow="LangGraph Run" onClose={() => setSelected(null)}>
  {selected && (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <OsStatusBadge status={selected.status} />
        <Badge tone="info">{selected.expert} · {selected.version}</Badge>
      </div>
      <div className="space-y-2 rounded-xl bg-slate-50 p-4 text-[12px] dark:bg-slate-800/60">
        <div><span className="block text-slate-400">Trace ID</span><b className="break-all font-mono text-[11.5px]">{selected.traceId}</b></div>
        <div><span className="block text-slate-400">Deployment</span><b className="break-all font-mono text-[11.5px]">{selected.deployment}</b></div>
        <div><span className="block text-slate-400">开始时间 · 耗时</span><b>{selected.started} · {selected.duration}</b></div>
      </div>
      <div>
        <span className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">输入</span>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-[11.5px] leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">{selected.input ?? selected.session}</pre>
      </div>
      <div>
        <span className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">产出</span>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-[11.5px] leading-relaxed text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">{selected.output || '（暂无产出）'}</pre>
      </div>
      {selected.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] leading-relaxed text-red-600 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
          <b className="mb-1 block">运行错误</b>{selected.error}
        </div>
      )}
    </div>
  )}
</DetailDrawer></div>
}

export function ApprovalsPage() {
  const { state, decideApproval, fetchApprovals } = useExpertOs(); const [selected, setSelected] = useState<ApprovalRecord | null>(null); const { navigate } = useApp(); const items = state.approvals
  const decide = async (id: string, status: 'approved' | 'rejected') => {
    try {
      await decideApproval(id, status)
      setSelected(null)
      toast.success(`审批${status === 'approved' ? '已通过' : '已拒绝'}，运行记录已同步更新`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '审批操作失败')
    } finally {
      void fetchApprovals()
    }
  }
  const pending = items.filter((item) => item.status === 'pending')
  return <div className="page-container"><PageHeader title="审批队列" sub="高风险 Tool Invocation 的运行时执行边界，批准后才会精确执行一次" actions={<button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => navigate('runtime-center')}>查看运行中心</button>} /><MetricStrip items={[{ label: '待审批', value: pending.length, note: '需要当前用户决策', tone: 'warn' }, { label: '今日已通过', value: 18, note: '均已写入审计', tone: 'suc' }, { label: '今日已拒绝', value: 3, note: '运行已终止', tone: 'err' }, { label: '平均决策时间', value: '4.2m', note: '近 7 日', tone: 'info' }, { label: '已过期', value: items.filter((item) => item.status === 'expired').length, note: '不可恢复执行', tone: 'gry' }]} /><div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-[12px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300"><ShieldCheck className="mr-2 inline h-4 w-4" />审批不是建议状态，而是 LangGraph interrupt 的执行边界。未批准的写入动作不会调用 Tool。</div><div className="grid gap-4 lg:grid-cols-2">{items.map((item) => <button key={item.id} className={`rounded-xl border bg-white p-4 text-left transition hover:-translate-y-0.5 hover:shadow-m dark:bg-slate-900 ${item.status === 'pending' ? 'border-amber-300 dark:border-amber-500/40' : 'border-slate-200 dark:border-slate-700/60'}`} onClick={() => setSelected(item)}><div className="flex items-start gap-3"><span className={`flex h-10 w-10 flex-none items-center justify-center rounded-lg ${item.risk === 'critical' ? 'bg-red-50 text-red-600 dark:bg-red-500/15' : 'bg-amber-50 text-amber-600 dark:bg-amber-500/15'}`}>{item.risk === 'critical' ? <CircleAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}</span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><b className="text-[13px] text-slate-800 dark:text-slate-100">{item.action}</b><OsStatusBadge status={item.status} /></div><div className="mt-1 font-mono text-[10.5px] text-slate-400">{item.tool}</div><p className="mt-2 text-[11.5px] text-slate-500">{item.expert} · {item.scope}</p></div></div><div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-[10.5px] text-slate-400 dark:border-slate-800"><span>{item.requester} · {item.requested}</span><span className={item.status === 'pending' ? 'font-medium text-amber-600' : ''}>{item.expires}</span></div></button>)}</div><DetailDrawer open={!!selected} title={selected?.action ?? ''} eyebrow="审批请求" onClose={() => setSelected(null)}>{selected && <div className="space-y-5"><div className="flex items-center gap-2"><OsStatusBadge status={selected.status} /><Badge tone={selected.risk === 'critical' ? 'err' : 'warn'}>{selected.risk}</Badge></div><div className="space-y-3 rounded-xl bg-slate-50 p-4 text-[12px] dark:bg-slate-800/60"><div><span className="block text-slate-400">请求 Expert</span><b>{selected.expert}</b></div><div><span className="block text-slate-400">调用 Tool</span><b className="font-mono">{selected.tool}</b></div><div><span className="block text-slate-400">作用范围</span><b>{selected.scope}</b></div><div><span className="block text-slate-400">策略原因</span><span className="block mt-1 leading-relaxed text-slate-600 dark:text-slate-300">该工具会写入外部系统或改变业务状态，必须由授权用户确认。</span></div></div>{selected.status === 'pending' && <div className="flex gap-2"><button className="flex-1 rounded-lg bg-emerald-600 py-2 text-[12.5px] font-medium text-white hover:bg-emerald-700" onClick={() => decide(selected.id, 'approved')}><CheckCircle2 className="mr-1 inline h-4 w-4" />批准执行</button><button className="flex-1 rounded-lg border border-red-300 py-2 text-[12.5px] font-medium text-red-600 hover:bg-red-50" onClick={() => decide(selected.id, 'rejected')}><XCircle className="mr-1 inline h-4 w-4" />拒绝</button></div>}</div>}</DetailDrawer></div>
}
