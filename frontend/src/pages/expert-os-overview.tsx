import { Activity, ArrowUpRight, Bot, PlugZap, Puzzle, ShieldAlert } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ExpertOsState, RunRecord } from '../types'
import { useApp } from '../store/app-store'
import { Badge, PageHeader, SectionCard } from '../components/common'
import { MetricStrip, OsStatusBadge } from '../components/expert-os'
import { useExpertOs } from '../store/expert-os-store'

const DAY_MS = 24 * 60 * 60 * 1000

function dateKey(value: string) {
  const parsed = Date.parse(value)
  if (Number.isNaN(parsed)) return ''
  return new Date(parsed).toISOString().slice(0, 10)
}

function buildDailyRunSeries(runs: RunRecord[], now = new Date()) {
  const days = Array.from({ length: 7 }, (_, offset) => {
    const day = new Date(now.getTime() - (6 - offset) * DAY_MS)
    return { key: day.toISOString().slice(0, 10), label: `${day.getMonth() + 1}/${day.getDate()}`, runs: 0, failed: 0 }
  })
  const dayByKey = new Map(days.map((day) => [day.key, day]))
  for (const run of runs) {
    const day = dayByKey.get(dateKey(run.started))
    if (!day) continue
    day.runs += 1
    if (['failed', 'interrupted', 'cancelled'].includes(run.status)) day.failed += 1
  }
  return days
}

function buildExpertRanking(state: Pick<ExpertOsState, 'experts' | 'runs'>) {
  const calls = new Map<string, number>()
  for (const run of state.runs) calls.set(run.expert, (calls.get(run.expert) ?? 0) + 1)
  return state.experts
    .map((expert) => ({ id: expert.id, name: expert.name, calls: calls.get(expert.id) ?? 0 }))
    .filter((expert) => expert.calls > 0)
    .sort((left, right) => right.calls - left.calls || left.name.localeCompare(right.name, 'zh-CN'))
    .slice(0, 5)
}

export function ExpertOsOverviewPage() {
  const { navigate } = useApp()
  const { state } = useExpertOs()
  const recentRuns = state.runs.slice(0, 5)
  const dailyRuns = buildDailyRunSeries(state.runs)
  const expertRanking = buildExpertRanking(state)
  const health = [
    { name: 'Expert', value: state.experts.length, active: state.experts.filter((item) => item.status === 'published').length },
    { name: 'Skill', value: state.skills.length, active: state.skills.filter((item) => item.status === 'published').length },
    { name: 'MCP', value: state.mcpServers.length, active: state.mcpServers.filter((item) => item.status === 'active').length },
    { name: 'Provider', value: state.providers.length, active: state.providers.filter((item) => item.status === 'healthy').length },
  ]

  return <div className="page-container">
    <PageHeader title="Expert OS 总览" sub="运行状态、能力资产和待处理风险的组织级控制面" actions={<><button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => navigate('runtime-center')}>查看运行中心</button><button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-blue-700" onClick={() => navigate('aichat')}>打开 AiChat <ArrowUpRight className="ml-1 inline h-3.5 w-3.5" /></button></>} />
    <MetricStrip items={[
      { label: '活跃 Experts', value: state.experts.filter((item) => item.status === 'published').length, note: `共 ${state.experts.length} 个 Expert`, tone: 'pur', icon: <Bot className="h-4 w-4" /> },
      { label: '已发布 Skills', value: state.skills.filter((item) => item.status === 'published').length, note: `共 ${state.skills.length} 个 Skill`, tone: 'suc', icon: <Puzzle className="h-4 w-4" /> },
      { label: '健康 MCP', value: state.mcpServers.filter((item) => item.status === 'active').length, note: `共 ${state.mcpServers.length} 个 MCP`, tone: 'suc', icon: <PlugZap className="h-4 w-4" /> },
      { label: '运行中 Runs', value: state.runs.filter((item) => ['queued', 'running', 'interrupted'].includes(item.status)).length, note: `近端加载 ${state.runs.length} 条`, tone: 'info', icon: <Activity className="h-4 w-4" /> },
      { label: '待审批动作', value: state.approvals.filter((item) => item.status === 'pending').length, note: '需要当前用户处理', tone: 'warn', icon: <ShieldAlert className="h-4 w-4" /> },
    ]} />
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.8fr)]">
      <SectionCard title="运行趋势" extra={<Badge tone="info" dot>近 7 日</Badge>}>
        <div className="h-[250px]" aria-label="近七日运行趋势">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={dailyRuns} margin={{ top: 12, right: 8, left: -22, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="3 3" />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: '#94a3b8' }} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 11, fill: '#94a3b8' }} />
              <Tooltip cursor={{ fill: '#f8fafc' }} contentStyle={{ borderRadius: 8, borderColor: '#e2e8f0', fontSize: 12 }} />
              <Bar dataKey="runs" name="运行数" fill="#7c3aed" radius={[4, 4, 0, 0]} />
              <Bar dataKey="failed" name="异常/中断" fill="#f59e0b" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex gap-5 text-[11.5px] text-slate-400"><span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-violet-600" />运行数</span><span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-amber-500" />异常/中断</span><span className="ml-auto">数据来自当前用户的最近 100 条 Run</span></div>
      </SectionCard>
      <SectionCard title="需要关注" extra={<span className="text-[11px] text-slate-400">实时</span>}>
        <div className="space-y-2.5">{state.approvals.filter((item) => item.status === 'pending').slice(0, 4).map((item) => <button key={item.id} className="flex w-full items-start gap-3 rounded-lg p-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60" onClick={() => navigate('approvals')}><span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-amber-50 text-amber-600 dark:bg-amber-500/15"><ShieldAlert className="h-4 w-4" /></span><span className="min-w-0"><b className="block truncate text-[12.5px] font-medium">{item.action || item.tool}</b><span className="mt-0.5 block truncate text-[11px] text-slate-400">{item.scope}</span></span></button>)}{!state.approvals.some((item) => item.status === 'pending') && <div className="py-5 text-center text-xs text-slate-400">暂无待审批动作</div>}</div>
      </SectionCard>
    </div>
    <div className="mt-5 grid gap-5 lg:grid-cols-3">
      <SectionCard title="最近运行" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('runtime-center')}>全部</button>}><div className="space-y-2">{recentRuns.map((run) => <div key={run.id} className="flex items-center gap-3 rounded-lg p-2"><span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-500 dark:bg-slate-800"><Activity className="h-3.5 w-3.5" /></span><span className="min-w-0 flex-1"><b className="block truncate text-[12px] font-medium">{run.session || run.id}</b><span className="text-[10.5px] text-slate-400">{run.id} · {run.started}</span></span><OsStatusBadge status={run.status} /></div>)}{!recentRuns.length && <div className="py-5 text-center text-xs text-slate-400">暂无服务端运行记录</div>}</div></SectionCard>
      <SectionCard title="Expert 调用排行"><div className="space-y-2.5">{expertRanking.map((expert, index) => <div key={expert.id} className="flex items-center gap-2.5"><span className="flex h-6 w-6 items-center justify-center rounded-md bg-violet-50 text-[11px] font-semibold text-violet-600 dark:bg-violet-500/15">{index + 1}</span><span className="min-w-0 flex-1 truncate text-xs font-medium">{expert.name}</span><span className="text-xs text-slate-400">{expert.calls} 次</span></div>)}{!expertRanking.length && <div className="py-5 text-center text-xs text-slate-400">暂无可统计的 Expert 调用</div>}</div></SectionCard>
      <SectionCard title="运行层健康度"><div className="space-y-3">{health.map((item) => <div key={item.name} className="flex items-center gap-3"><span className="w-16 text-xs text-slate-500">{item.name}</span><div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${item.value ? item.active * 100 / item.value : 0}%` }} /></div><span className="w-14 text-right text-xs font-medium">{item.active}/{item.value}</span></div>)}</div></SectionCard>
    </div>
  </div>
}
