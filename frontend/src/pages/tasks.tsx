import { useEffect, useState } from 'react'
import { Bot, Clock, Inbox, LayoutGrid, ArrowRight, GitCommitHorizontal } from 'lucide-react'
import { useApp } from '../store/app-store'
import {
  Badge, KpiCard, PageHeader, SectionCard, priorityBadge, taskStatusBadge, Avatar,
} from '../components/common'
import { CreateWorkItemDialog } from '../components/dialogs'
import { api } from '../lib/api'
import { cn } from '../lib/utils'
import type { TaskItem } from '../types'

function TaskRow({ t }: { t: TaskItem }) {
  const { openWorkItem, openTask } = useApp()
  return (
    <div
      onClick={() => openWorkItem(t.wiId)}
      className={cn(
        'flex w-full cursor-pointer items-center gap-4 rounded-xl border p-4 text-left transition-all hover:-translate-y-0.5 hover:border-blue-300 hover:shadow-m dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/40',
        t.overdue ? 'border-red-200 bg-red-50/50 dark:border-red-500/30 dark:bg-red-500/5' : 'border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900',
      )}
    >
      <span className={cn('flex h-10 w-10 flex-none items-center justify-center rounded-lg',
        t.type === 'requirement' ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400')}>
        <Inbox className="h-5 w-5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[11.5px] text-slate-400">{t.wiId}</span>
          <span className="text-[14px] font-medium text-slate-800 dark:text-slate-100">{t.title}</span>
          {priorityBadge(t.priority)}
          {t.agentPending && <Badge tone="pur" dot>Agent 待确认</Badge>}
          {(t as TaskItem & { frozen?: boolean }).frozen && <Badge tone="blk">冻结</Badge>}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-slate-400 dark:text-slate-500">
          <span className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400">
            <GitCommitHorizontal className="h-3.5 w-3.5" />{t.node}
          </span>
          <span>项目：{t.project}</span>
          <span className={cn('inline-flex items-center gap-1', t.overdue ? 'font-medium text-red-500' : '')}>
            <Clock className="h-3.5 w-3.5" />{t.overdue ? `已超时 · ${t.due}` : `截止 ${t.due}`}
          </span>
          {t.source && <span className="text-slate-400">来源：{t.source}</span>}
        </div>
      </div>
      <div className="flex flex-none flex-col items-end gap-1.5">
        {taskStatusBadge(t.status)}
        <button
          onClick={(e) => { e.stopPropagation(); openTask(t.id, t.wiId) }}
          disabled={(t as TaskItem & { frozen?: boolean }).frozen}
          className={cn(
            'rounded-lg px-3 py-1 text-[12px] font-medium shadow-sm transition-colors',
            (t as TaskItem & { frozen?: boolean }).frozen
              ? 'cursor-not-allowed bg-slate-200 text-slate-400 dark:bg-slate-800'
              : 'bg-blue-600 text-white hover:bg-blue-700',
          )}>
          {(t as TaskItem & { frozen?: boolean }).frozen ? '已冻结' : t.status === 'completed' ? '查看' : '去处理'}
        </button>
        <span className="text-[11px] text-slate-400">SLA {t.slaHours}h</span>
      </div>
      <ArrowRight className="h-4 w-4 flex-none text-slate-300" />
    </div>
  )
}

export function MyTasksPage() {
  const { navigate, openWorkItem, taskCounter } = useApp()
  const [createOpen, setCreateOpen] = useState(false)
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])
  const [projectFilter, setProjectFilter] = useState('')
  const [workItems, setWorkItems] = useState<{ id: string; creator: string; title: string; progress: string }[]>([])
  /* 流程动态：来自后端审计最近记录（真实业务动作流） */
  const [activities, setActivities] = useState<{ time: string; title: string; desc: string; by: string }[]>([])
  const actIcon = (k: string) =>
    k === 'agent' ? <Bot className="h-4 w-4" /> : k === 'system' ? <GitCommitHorizontal className="h-4 w-4" /> : <Inbox className="h-4 w-4" />

  /* 任务列表接后端（GET /tasks，按当前登录人过滤 + 项目筛选） */
  useEffect(() => {
    api.get<{ items: TaskItem[] }>(`/api/v1/tasks?page_size=50${projectFilter ? `&project=${encodeURIComponent(projectFilter)}` : ''}`)
      .then((d) => { if (d.items.length) setTasks(d.items); else setTasks([]) })
      .catch(() => { /* 后端不可用：空列表 */ })
  }, [taskCounter, projectFilter])

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

  const pending = tasks.filter((t) => !['completed', 'cancelled'].includes(t.status)).length
  const overdue = tasks.filter((t) => t.overdue).length
  const agentPending = tasks.filter((t) => t.agentPending).length

  return (
    <div className="page-container">
      <PageHeader
        title="我的任务"
        sub="待办 6 · 数据范围 assigned_tasks + own_created_items · 超时任务将通知项目管理员"
        actions={
          <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={() => setCreateOpen(true)}>
            新建工作项
          </button>
        }
      />

      {/* KPI */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KpiCard label="待我处理" value={pending + (taskCounter % 3)} delta="+1" up icon={<LayoutGrid className="h-4 w-4" />} tone="blue" />
        <KpiCard label="已超时" value={overdue} delta="+0" up={false} icon={<Clock className="h-4 w-4" />} tone="red" />
        <KpiCard label="Agent 待确认" value={agentPending} delta="—" icon={<Bot className="h-4 w-4" />} tone="violet" />
        <KpiCard label="今日完成" value={tasks.filter((t) => t.status === 'completed').length} delta="+1" up icon={<Inbox className="h-4 w-4" />} tone="green" />
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_330px]">
        {/* 任务列表 */}
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12.5px] font-medium text-slate-500 dark:text-slate-400">共 {tasks.length} 条任务</span>
            <select
              className="h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-[12px] text-slate-600 outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              value={projectFilter}
              onChange={(e) => setProjectFilter(e.target.value)}>
              <option value="">全部项目</option>
              {projects.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
            </select>
          </div>
          {tasks.map((t) => <TaskRow key={t.id} t={t} />)}
          {tasks.length === 0 && (
            <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-[12.5px] text-slate-400 dark:border-slate-700">
              {projectFilter ? `「${projectFilter}」暂无任务` : '暂无任务'}
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
                { label: 'Agent 授权', page: 'agents' as const },
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
