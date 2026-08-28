import { Activity, ArrowUpRight, ShieldAlert } from 'lucide-react'
import { useApp } from '../store/app-store'
import { Badge, PageHeader, SectionCard } from '../components/common'
import { MetricStrip, OsStatusBadge } from '../components/expert-os'
import { useExpertOs } from '../store/expert-os-store'

export function ExpertOsOverviewPage() {
  const { navigate } = useApp()
  const { state } = useExpertOs()
  const recentRuns = state.runs.slice(0, 5)
  return <div className="page-container">
    <PageHeader title="Expert OS 总览" sub="运行状态、能力资产和待处理风险的组织级控制面" actions={<><button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => navigate('runtime-center')}>查看运行中心</button><button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-blue-700" onClick={() => navigate('aichat')}>打开 AiChat <ArrowUpRight className="ml-1 inline h-3.5 w-3.5" /></button></>} />
     <MetricStrip items={[{ label: '活跃 Experts', value: state.experts.filter((item) => item.status === 'published').length, note: '服务端真实数据', tone: 'pur' }, { label: '已发布 Skills', value: state.skills.filter((item) => item.status === 'published').length, note: '服务端真实数据', tone: 'suc' }, { label: '健康 MCP', value: state.mcpServers.filter((item) => item.status === 'active').length, note: `共 ${state.mcpServers.length} 个`, tone: 'suc' }, { label: '运行中 Runs', value: state.runs.filter((item) => ['queued', 'running', 'interrupted'].includes(item.status)).length, note: '服务端真实数据', tone: 'info' }, { label: '待审批动作', value: state.approvals.filter((item) => item.status === 'pending').length, note: '需要当前用户处理', tone: 'warn' }]} />
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.7fr)_minmax(280px,0.8fr)]">
      <SectionCard title="运行与工具调用" extra={<Badge tone="info" dot>近 7 日</Badge>}>
         <div className="flex h-[250px] items-center justify-center text-sm text-slate-400">{state.runs.length ? `${state.runs.length} 条服务端 Run 记录` : '暂无服务端运行记录'}</div>
        <div className="mt-2 flex gap-5 text-[11.5px] text-slate-400"><span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-violet-600" />Runs</span><span><i className="mr-1.5 inline-block h-2 w-2 rounded-full bg-teal-600" />Tool Calls</span><span className="ml-auto text-emerald-600">较前 7 日 +18.4%</span></div>
      </SectionCard>
       <SectionCard title="需要关注" extra={<span className="text-[11px] text-slate-400">实时</span>}>
         <div className="space-y-2.5">{state.approvals.filter((item) => item.status === 'pending').slice(0, 4).map((item) => <button key={item.id} className="flex w-full items-start gap-3 rounded-lg p-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60" onClick={() => navigate('approvals')}><span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-amber-50 text-amber-600"><ShieldAlert /></span><span className="min-w-0"><b className="block truncate text-[12.5px] font-medium">{item.action || item.tool}</b><span className="mt-0.5 block truncate text-[11px] text-slate-400">{item.scope}</span></span></button>)}{!state.approvals.some((item) => item.status === 'pending') && <div className="py-5 text-center text-xs text-slate-400">暂无待审批动作</div>}</div>
      </SectionCard>
    </div>
    <div className="mt-5 grid gap-5 lg:grid-cols-3">
      <SectionCard title="最近运行" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('runtime-center')}>全部</button>}><div className="space-y-2">{recentRuns.map((run) => <div key={run.id} className="flex items-center gap-3 rounded-lg p-2"><span className="flex h-7 w-7 items-center justify-center rounded-md bg-slate-100 text-slate-500"><Activity className="h-3.5 w-3.5" /></span><span className="min-w-0 flex-1"><b className="block truncate text-[12px] font-medium">{run.session || run.id}</b><span className="text-[10.5px] text-slate-400">{run.id} · {run.started}</span></span><OsStatusBadge status={run.status} /></div>)}{!recentRuns.length && <div className="py-5 text-center text-xs text-slate-400">暂无服务端运行记录</div>}</div></SectionCard>
      <SectionCard title="Expert 调用排行"><div className="py-5 text-center text-xs text-slate-400">调用统计待后端聚合接口</div></SectionCard>
      <SectionCard title="运行层健康度"><div className="space-y-3">{[['Expert', state.experts.length], ['Skill', state.skills.length], ['MCP', state.mcpServers.length], ['Provider', state.providers.length], ['Knowledge', state.knowledgeBases.length], ['Memory', state.memories.length]].map(([name, value]) => <div key={name} className="flex items-center gap-3"><span className="w-16 text-xs text-slate-500">{name}</span><div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100"><div className="h-full w-full rounded-full bg-emerald-500" /></div><span className="w-14 text-right text-xs font-medium">{value}</span></div>)}</div></SectionCard>
    </div>
  </div>
}
