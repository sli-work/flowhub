import { useEffect, useState } from 'react'
import {
  Activity, CheckCircle2, Clock, Bot, TrendingUp, TriangleAlert,
} from 'lucide-react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  PieChart, Pie, Cell,
} from 'recharts'
import { useApp } from '../store/app-store'
import { api } from '../lib/api'
import { KpiCard, PageHeader, SectionCard, Badge } from '../components/common'
import { cn } from '../lib/utils'

type DashboardKpi = { label: string; value: number }
type WeeklyPoint = { d: string; v: number }

export function DashboardPage() {
  const { navigate } = useApp()
  const [kpis, setKpis] = useState<DashboardKpi[]>([
    { label: '运行中流程', value: 0 },
    { label: '已关闭工作项', value: 0 },
    { label: '超时任务', value: 0 },
    { label: 'Agent 待确认', value: 0 },
  ])
  const [typeSplit, setTypeSplit] = useState<{ name: string; value: number; color: string }[]>([])
  const [timeoutTop, setTimeoutTop] = useState<{ name: string; v: string }[]>([])
  const [weekly, setWeekly] = useState<WeeklyPoint[]>([])
  const [weeklyLabel, setWeeklyLabel] = useState('近 7 日活动')
  const [weeklyTotal, setWeeklyTotal] = useState(0)
  const [weeklyPreviousTotal, setWeeklyPreviousTotal] = useState(0)
  const [deptLoad, setDeptLoad] = useState<{ name: string; v: number }[]>([])
  const [nodeHeat, setNodeHeat] = useState<{ name: string; v: number }[]>([])

  /* 接后端：所有可视化数据均来自持久化聚合，不保留静态展示值。 */
  useEffect(() => {
    api.get<{ kpis: DashboardKpi[]; type_split?: { name: string; value: number; color?: string }[]; timeout_top?: { name: string; v: string }[]; weekly?: WeeklyPoint[]; weekly_label?: string; weekly_total?: number; weekly_previous_total?: number; dept_load?: { name: string; v: number }[]; node_heat?: { name: string; v: number }[] }>('/api/v1/dashboard/overview')
      .then((d) => {
        if (d.kpis.length) setKpis(d.kpis)
        if (d.type_split?.length) setTypeSplit(d.type_split.map((t) => ({ name: t.name, value: t.value, color: t.color ?? '#94A3B8' })))
        if (d.timeout_top?.length) setTimeoutTop(d.timeout_top)
        if (d.weekly) setWeekly(d.weekly)
        if (d.weekly_label) setWeeklyLabel(d.weekly_label)
        if (typeof d.weekly_total === 'number') setWeeklyTotal(d.weekly_total)
        if (typeof d.weekly_previous_total === 'number') setWeeklyPreviousTotal(d.weekly_previous_total)
        if (d.dept_load?.length) setDeptLoad(d.dept_load)
        if (d.node_heat?.length) setNodeHeat(d.node_heat)
      })
      .catch(() => { /* 后端不可用：保持零值 */ })
  }, [])

  const weeklyChange = weeklyPreviousTotal === 0
    ? (weeklyTotal === 0 ? '暂无活动' : `${weeklyTotal} 条活动`)
    : `较前 7 日 ${weeklyTotal >= weeklyPreviousTotal ? '+' : ''}${Math.round((weeklyTotal - weeklyPreviousTotal) / weeklyPreviousTotal * 100)}%`
  const deptLoadMax = Math.max(...deptLoad.map((item) => item.v), 1)
  const highLoadDepartments = deptLoad.filter((item) => item.v >= 8).length
  const nodeHeatMax = Math.max(...nodeHeat.map((item) => item.v), 1)

  return (
    <div className="page-container">
      <PageHeader
        title="领导看板"
        sub="组织级流程健康度 · 数据范围 all_projects（仅对具备 dashboard:read 的角色）"
        actions={
          <button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            onClick={() => navigate('tasks')}>
            <TrendingUp className="mr-1 inline h-4 w-4" />查看全部
          </button>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KpiCard label={kpis[0].label} value={kpis[0].value} icon={<Activity className="h-4 w-4" />} tone="blue" />
        <KpiCard label={kpis[1].label} value={kpis[1].value} icon={<CheckCircle2 className="h-4 w-4" />} tone="green" />
        <KpiCard label={kpis[2].label} value={kpis[2].value} icon={<Clock className="h-4 w-4" />} tone="red" />
        <KpiCard label={kpis[3].label} value={kpis[3].value} icon={<Bot className="h-4 w-4" />} tone="violet" />
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        {/* 周趋势 */}
        <SectionCard title={weeklyLabel} className="lg:col-span-2" extra={<Badge tone="info" dot>{weeklyChange}</Badge>}>
          <div className="h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={weekly} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                <defs>
                  <linearGradient id="gFlow" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2563EB" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#2563EB" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(215 16% 88%)" />
                <XAxis dataKey="d" tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: '#94A3B8' }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ borderRadius: 10, border: '1px solid #E2E8F0', fontSize: 12 }}
                  formatter={(v) => [`${v} 条`, '活动数']}
                />
                <Area type="monotone" dataKey="v" stroke="#2563EB" strokeWidth={2} fill="url(#gFlow)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </SectionCard>

        {/* 类型占比 */}
        <SectionCard title="流程类型占比">
          <div className="h-[180px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={typeSplit} dataKey="value" nameKey="name" innerRadius={52} outerRadius={76} paddingAngle={3}>
                  {typeSplit.map((s) => <Cell key={s.name} fill={s.color} />)}
                </Pie>
                <Tooltip contentStyle={{ borderRadius: 10, border: '1px solid #E2E8F0', fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-1 space-y-1.5">
            {typeSplit.map((s) => (
              <div key={s.name} className="flex items-center gap-2 text-[12px] text-slate-500 dark:text-slate-400">
                <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} />
                <span className="flex-1">{s.name}</span>
                <b className="text-slate-700 dark:text-slate-200">{s.value}</b>
              </div>
            ))}
          </div>
        </SectionCard>

        {/* 部门负载 */}
        <SectionCard title="部门任务负载" extra={<Badge tone={highLoadDepartments ? 'warn' : 'suc'}>{highLoadDepartments} 部门偏高</Badge>}>
          <div className="space-y-3">
            {deptLoad.map((d) => (
              <div key={d.name} className="flex items-center gap-3">
                <span className="w-[88px] flex-none text-[12px] text-slate-500 dark:text-slate-400">{d.name}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className={cn('h-full rounded-full', d.v >= 12 ? 'bg-gradient-to-r from-red-400 to-red-500' : d.v >= 8 ? 'bg-gradient-to-r from-amber-400 to-orange-500' : 'bg-gradient-to-r from-blue-400 to-blue-600')}
                    style={{ width: `${d.v / deptLoadMax * 100}%` }} />
                </div>
                <span className="w-6 flex-none text-right text-[12px] font-medium text-slate-600 dark:text-slate-300">{d.v}</span>
              </div>
            ))}
          </div>
        </SectionCard>

        {/* 超时 Top */}
        <SectionCard title="超时 Top 工作项" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('tasks')}>查看全部</button>}>
          <div className="space-y-1.5">
            {timeoutTop.map((t, i) => (
              <div key={t.name} className="flex items-center gap-3 rounded-lg p-2 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                <span className={cn('flex h-6 w-6 flex-none items-center justify-center rounded-md text-[11px] font-bold',
                  i === 0 ? 'bg-red-50 text-red-500 dark:bg-red-500/15' : i === 1 ? 'bg-amber-50 text-amber-600 dark:bg-amber-500/15' : 'bg-slate-100 text-slate-500 dark:bg-slate-800')}>
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-600 dark:text-slate-300">{t.name}</span>
                <span className={cn('flex-none text-[11.5px] font-medium', t.v.includes('超时') ? 'text-red-500' : 'text-slate-400')}>{t.v}</span>
              </div>
            ))}
          </div>
        </SectionCard>

        {/* 节点热区 */}
        <SectionCard title="节点处理热区" extra={<Badge tone="info">Top 5</Badge>}>
          <div className="space-y-2.5">
            {nodeHeat.map((n) => (
              <div key={n.name} className="flex items-center gap-3">
                <span className="w-[70px] flex-none text-[12px] text-slate-500 dark:text-slate-400">{n.name}</span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-600" style={{ width: `${n.v / nodeHeatMax * 100}%` }} />
                </div>
                <span className="w-6 flex-none text-right text-[12px] font-medium text-slate-600 dark:text-slate-300">{n.v}</span>
              </div>
            ))}
          </div>
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-slate-50 p-2.5 text-[11px] leading-relaxed text-slate-400 dark:bg-slate-800/60">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 flex-none text-amber-500" />
            看板查询为异步聚合，避免长时间聚合阻塞核心事务（PRD §15.3 性能验收基线）。
          </div>
        </SectionCard>
      </div>
    </div>
  )
}
