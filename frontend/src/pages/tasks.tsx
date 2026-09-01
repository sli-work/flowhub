import { useEffect, useState } from 'react'
import { Bot, Clock, Inbox, LayoutGrid, ArrowRight, GitCommitHorizontal, Search } from 'lucide-react'
import { useApp } from '../store/app-store'
import {
  Badge, KpiCard, PageHeader, SectionCard, priorityBadge, taskStatusBadge, Avatar,
} from '../components/common'
import { CreateWorkItemDialog } from '../components/dialogs'
import { api } from '../lib/api'
import { cn } from '../lib/utils'
import type { TaskItem } from '../types'

const PAGE_SIZE = 10

/** 状态分组（与后端 STATUS_GROUPS 对齐） */
const STATUS_TABS = [
  { key: '', label: '全部', statKey: 'all' },
  { key: 'todo', label: '待处理', statKey: 'todo' },
  { key: 'doing', label: '进行中', statKey: 'doing' },
  { key: 'submitted', label: '已提交', statKey: 'submitted' },
  { key: 'done', label: '已完成', statKey: 'done' },
] as const

interface TaskStats {
  all: number; todo: number; doing: number; submitted: number; done: number
  open: number; overdue: number; expertPending: number
}

const SELECT_CLS =
  'h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-[12px] text-slate-600 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300'

function TaskRow({ t }: { t: TaskItem }) {
  const { openWorkItem, openTask } = useApp()
  return (
    <div
      onClick={() => openWorkItem(t.wiId)}
      className={cn(
        'flex w-full cursor-pointer items-center gap-3.5 rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:border-blue-300 hover:shadow-m dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/40',
        t.overdue ? 'border-red-200 bg-red-50/50 dark:border-red-500/30 dark:bg-red-500/5' : 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900',
      )}
    >
      <span className={cn('flex h-10 w-10 flex-none items-center justify-center rounded-lg',
        t.type === 'requirement' ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400')}>
        <Inbox className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        {/* 主行：标题 + 关键徽章 */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[14px] font-medium text-slate-800 dark:text-slate-100">{t.title}</span>
          {priorityBadge(t.priority)}
          {taskStatusBadge(t.status)}
          {t.overdue && <Badge tone="err" dot>已超时</Badge>}
          {t.expertPending && <Badge tone="pur" dot>Expert 待审批</Badge>}
          {t.frozen && <Badge tone="blk">冻结</Badge>}
        </div>
        {/* 次行：元信息（弱化展示） */}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-400 dark:text-slate-500">
          <span className="font-mono">{t.wiId}</span>
          <span className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400">
            <GitCommitHorizontal className="h-3.5 w-3.5" />{t.node}
          </span>
          {t.parentTaskId && (
            <span className="inline-flex items-center gap-0.5 font-medium text-violet-500" title={`父任务 ${t.parentTaskId}`}>
              ↳ 子任务
            </span>
          )}
          <span>{t.project}</span>
          <span className={cn('inline-flex items-center gap-1', t.overdue ? 'font-medium text-red-500' : '')}>
            <Clock className="h-3.5 w-3.5" />{t.overdue ? `已超时 · ${t.due}` : `截止 ${t.due}`}
          </span>
          {t.source && <span>来源：{t.source}</span>}
          <span>SLA {t.slaHours}h</span>
        </div>
      </div>
      <button
        onClick={(e) => { e.stopPropagation(); openTask(t.id, t.wiId) }}
        disabled={t.frozen}
        className={cn(
          'flex-none rounded-lg px-3 py-1.5 text-[12px] font-medium shadow-sm transition-colors',
          t.frozen
            ? 'cursor-not-allowed bg-slate-200 text-slate-400 dark:bg-slate-800'
            : 'bg-blue-600 text-white hover:bg-blue-700',
        )}>
        {t.frozen ? '已冻结' : t.status === 'completed' ? '查看' : '去处理'}
      </button>
      <ArrowRight className="h-4 w-4 flex-none text-slate-300" />
    </div>
  )
}

export function MyTasksPage() {
  const { navigate, openWorkItem, taskCounter } = useApp()
  const [createOpen, setCreateOpen] = useState(false)
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])
  /* 列表筛选状态：全部走服务端分页/过滤；任何筛选变更重置到第 1 页 */
  const [page, setPage] = useState(1)
  const [statusGroup, setStatusGroup] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('')
  const [projectFilter, setProjectFilter] = useState('')
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<TaskStats | null>(null)
  const [workItems, setWorkItems] = useState<{ id: string; creator: string; title: string; progress: string }[]>([])
  /* 流程动态：来自后端审计最近记录（真实业务动作流） */
  const [activities, setActivities] = useState<{ time: string; title: string; desc: string; by: string }[]>([])
  const actIcon = (k: string) =>
    k === 'agent' ? <Bot className="h-4 w-4" /> : k === 'system' ? <GitCommitHorizontal className="h-4 w-4" /> : <Inbox className="h-4 w-4" />

  /* 任务列表：服务端分页 + 状态组/关键词/排序/项目过滤 */
  useEffect(() => {
    const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
    if (statusGroup) qs.set('status_group', statusGroup)
    if (q) qs.set('q', q)
    if (sort) qs.set('sort', sort)
    if (projectFilter) qs.set('project', projectFilter)
    api.get<{ items: TaskItem[]; total: number; stats: TaskStats }>(`/api/v1/tasks?${qs}`)
      .then((d) => { setTasks(d.items); setTotal(d.total); setStats(d.stats) })
      .catch(() => { /* 后端不可用：空列表 */ })
  }, [taskCounter, page, statusGroup, q, sort, projectFilter])

  /* 项目列表（筛选下拉数据源） */
  useEffect(() => {
    api.get<{ items: { id: string; name: string }[] }>('/api/v1/projects')
      .then((d) => setProjects(d.items))
      .catch(() => {})
  }, [])

  /* 快捷入口：最近工作项 + 流程动态：最近审计 */
  useEffect(() => {
    api.get<{ items: { id: string; creator: string; title: string; progress: string }[] }>('/api/v1/work-items?page_size=5')
      .then((d) => setWorkItems(d.items))
      .catch(() => {})
    api.get<{ items: { time: string; actor: string; action: string; target: string }[] }>('/api/v1/audits?page_size=6')
      .then((d) => {
        setActivities(d.items.map((a) => ({
          time: a.time.length >= 16 ? a.time.slice(0, 16) : a.time, title: `${a.actor} ${a.action}`, desc: a.target, by: a.action.startsWith('agent') ? 'agent' : a.action.startsWith('org') ? 'system' : 'user',
        })))
      })
      .catch(() => {})
  }, [taskCounter])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const goPage = (p: number) => setPage(Math.min(Math.max(1, p), totalPages))
  /** 筛选变更：重置回第 1 页（避免翻页后筛选出现空页） */
  const changeFilter = <T,>(setter: (v: T) => void) => (value: T) => { setter(value); setPage(1) }

  return (
    <div className="page-container">
      <PageHeader
        title="我的任务"
        sub={`共 ${total} 条任务 · 按状态分组浏览，超时任务将通知项目管理员`}
        actions={
          <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={() => setCreateOpen(true)}>
            新建工作项
          </button>
        }
      />

      {/* KPI：服务端聚合，不再基于当前页估算 */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KpiCard label="待我处理" value={stats?.open ?? 0} delta="—" up icon={<LayoutGrid className="h-4 w-4" />} tone="blue" />
        <KpiCard label="已超时" value={stats?.overdue ?? 0} delta="—" up={false} icon={<Clock className="h-4 w-4" />} tone="red" />
        <KpiCard label="Expert 待审批" value={stats?.expertPending ?? 0} delta="—" icon={<Bot className="h-4 w-4" />} tone="violet" />
        <KpiCard label="已完成" value={stats?.done ?? 0} delta="—" up icon={<Inbox className="h-4 w-4" />} tone="green" />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_330px]">
        {/* 任务列表 */}
        <div className="space-y-3">
          {/* 状态 Tabs（带服务端计数） */}
          <div className="flex flex-wrap items-center gap-1.5">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => changeFilter(setStatusGroup)(tab.key)}
                className={cn(
                  'rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
                  statusGroup === tab.key
                    ? 'bg-blue-600 text-white shadow-sm'
                    : 'border border-slate-200 text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-400',
                )}>
                {tab.label}
                {stats && <span className={cn('ml-1', statusGroup === tab.key ? 'text-blue-100' : 'text-slate-400')}>{stats[tab.statKey]}</span>}
              </button>
            ))}
          </div>
          {/* 搜索 / 排序 / 项目筛选 */}
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative inline-flex flex-1 items-center sm:max-w-[260px]">
              <Search className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                className="h-8 w-full rounded-lg border border-slate-300 bg-white pl-8 pr-2 text-[12px] text-slate-600 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                placeholder="搜索任务标题…"
                value={q}
                onChange={(e) => changeFilter(setQ)(e.target.value)}
              />
            </label>
            <select className={SELECT_CLS} value={sort} onChange={(e) => changeFilter(setSort)(e.target.value)} aria-label="排序方式">
              <option value="">默认排序（待办优先）</option>
              <option value="created">最新创建</option>
              <option value="priority">按优先级</option>
              <option value="due">按截止时间</option>
            </select>
            <select className={SELECT_CLS} value={projectFilter} onChange={(e) => changeFilter(setProjectFilter)(e.target.value)} aria-label="项目筛选">
              <option value="">全部项目</option>
              {projects.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
            </select>
          </div>
          {tasks.map((t) => <TaskRow key={t.id} t={t} />)}
          {tasks.length === 0 && (
            <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-[12.5px] text-slate-400 dark:border-slate-700">
              {q || projectFilter || statusGroup ? '当前筛选条件下暂无任务' : '暂无任务'}
            </div>
          )}
          {/* 分页 footer（服务端分页，参照 audit 页模式） */}
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-slate-100 px-1 pt-3 text-[12px] text-slate-400 dark:border-slate-800">
              <span>共 {total} 条 · 第 {safePage}/{totalPages} 页</span>
              <div className="flex items-center gap-1.5">
                <button
                  className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                    safePage <= 1 ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                  disabled={safePage <= 1} onClick={() => goPage(safePage - 1)}>
                  上一页
                </button>
                {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                  <button key={p}
                    className={cn('h-7 w-7 rounded-md border text-[12px] transition-colors',
                      p === safePage ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                    onClick={() => goPage(p)}>{p}</button>
                ))}
                <button
                  className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                    safePage >= totalPages ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                  disabled={safePage >= totalPages} onClick={() => goPage(safePage + 1)}>
                  下一页
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 右侧：流程动态 + 快捷入口 */}
        <div className="space-y-5">
          <SectionCard
            title="流程动态"
            extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('audit')}>全部</button>}
            bodyClassName="p-3"
          >
            <div className="space-y-0.5">
              {activities.map((a) => (
                <div key={a.title + a.time} className="flex gap-3 rounded-lg px-2 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/60">
                  <span className={cn('mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-full text-[10.5px] font-medium',
                    a.by === 'agent' ? 'bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400'
                      : a.by === 'system' ? 'bg-slate-100 text-slate-500 dark:bg-slate-800'
                      : 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400')}>
                    {actIcon(a.by)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{a.title}</span>
                      <span className="flex-none text-[10.5px] text-slate-400">{a.time}</span>
                    </div>
                    <div className="truncate text-[11.5px] text-slate-400">{a.desc}</div>
                  </div>
                </div>
              ))}
            </div>
          </SectionCard>

          <SectionCard title="快捷入口" bodyClassName="p-4">
            <div className="grid grid-cols-2 gap-2.5">
              {[
                { label: '流程模板', page: 'templates' as const },
                { label: '上传文档', page: 'docs' as const },

                { label: '领导看板', page: 'dashboard' as const },
              ].map((q) => (
                <button key={q.label} onClick={() => navigate(q.page)}
                  className="flex items-center justify-center gap-1.5 rounded-lg border border-slate-200 py-2.5 text-[12.5px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300">
                  {q.label}
                </button>
              ))}
            </div>
            <div className="mt-4 space-y-1.5">
              {workItems.slice(0, 3).map((w) => (
                <button key={w.id} onClick={() => openWorkItem(w.id)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60">
                  <Avatar name={w.creator} grad="g2" size={22} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] font-medium text-slate-600 dark:text-slate-300">{w.title}</span>
                    <span className="text-[10.5px] text-slate-400">{w.id} · {w.progress}</span>
                  </span>
                </button>
              ))}
            </div>
          </SectionCard>
        </div>
      </div>

      {createOpen && <CreateWorkItemDialog onClose={() => setCreateOpen(false)} />}
    </div>
  )
}
