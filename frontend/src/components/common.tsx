import type { ReactNode } from 'react'
import { cn } from '../lib/utils'
import type { FlowNode, TaskStatus, WorkItemStatus } from '../types'

/* ============ 状态 → 徽标 映射 ============ */
const badgeTone = {
  suc: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  warn: 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  err: 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-300',
  info: 'bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300',
  pur: 'bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300',
  cyn: 'bg-cyan-50 text-cyan-700 dark:bg-cyan-500/15 dark:text-cyan-300',
  orgx: 'bg-orange-50 text-orange-700 dark:bg-orange-500/15 dark:text-orange-300',
  gry: 'bg-slate-100 text-slate-500 dark:bg-slate-700/40 dark:text-slate-400',
  blk: 'bg-slate-800 text-slate-200 dark:bg-slate-200 dark:text-slate-800',
} as const

export type Tone = keyof typeof badgeTone

export function Badge({ tone = 'gry', children, dot, className }: {
  tone?: Tone; children: ReactNode; dot?: boolean; className?: string
}) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium leading-5',
      badgeTone[tone], className,
    )}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

/* 工作项状态 → 徽标 */
export function wiStatusBadge(status: WorkItemStatus) {
  const map: Record<WorkItemStatus, [Tone, string]> = {
    draft: ['gry', '草稿'], submitted: ['info', '已提交'], in_progress: ['cyn', '进行中'],
    waiting_for_information: ['warn', '待补充信息'], waiting_for_verification: ['orgx', '待验证'],
    resolved: ['suc', '已解决'], accepted: ['suc', '已验收'], rejected: ['err', '已驳回'],
    closed: ['blk', '已关闭'], cancelled: ['gry', '已取消'], archived: ['gry', '已归档'],
  }
  const [tone, label] = map[status] ?? ['gry', status]
  return <Badge tone={tone}>{label}</Badge>
}

/* 任务状态 → 徽标 */
export function taskStatusBadge(status: TaskStatus) {
  const map: Record<TaskStatus, [Tone, string]> = {
    assigned: ['info', '已分配'], accepted: ['cyn', '已认领'], in_progress: ['pur', '处理中'],
    waiting_for_information: ['warn', '待补充信息'], pending_confirmation: ['orgx', '待确认'],
    submitted: ['info', '已提交'], returned: ['err', '已退回'], transferred: ['gry', '已转办'],
    completed: ['suc', '已完成'], cancelled: ['gry', '已取消'],
  }
  const [tone, label] = map[status] ?? ['gry', status]
  return <Badge tone={tone}>{label}</Badge>
}

export function priorityBadge(p: 'P0' | 'P1' | 'P2' | 'P3') {
  const tone: Tone = p === 'P0' ? 'err' : p === 'P1' ? 'orgx' : p === 'P2' ? 'info' : 'gry'
  return <Badge tone={tone}>{p}</Badge>
}

/* ============ 头像 ============ */
const grads: Record<string, string> = {
  g1: 'linear-gradient(135deg,#0EA5E9,#2563EB)',
  g2: 'linear-gradient(135deg,#F59E0B,#EA580C)',
  g3: 'linear-gradient(135deg,#10B981,#059669)',
  g4: 'linear-gradient(135deg,#8B5CF6,#6D28D9)',
  g5: 'linear-gradient(135deg,#EC4899,#DB2777)',
  g6: 'linear-gradient(135deg,#14B8A6,#0D9488)',
}

export function Avatar({ name, grad = 'g1', size = 30, className, rounded }: {
  name: string; grad?: string; size?: number; className?: string; rounded?: boolean
}) {
  return (
    <span
      className={cn('inline-flex flex-none items-center justify-center font-semibold text-white', className)}
      style={{
        width: size, height: size, borderRadius: rounded ? 9 : '50%',
        background: grads[grad] ?? grads.g1,
        fontSize: size >= 30 ? 12 : 10,
      }}
    >
      {name.slice(0, 1)}
    </span>
  )
}

/* ============ KPI 卡 ============ */
export function KpiCard({ label, value, delta, up, icon, tone = 'blue' }: {
  label: string; value: ReactNode; delta?: string; up?: boolean; icon?: ReactNode; tone?: 'blue' | 'amber' | 'red' | 'green' | 'violet'
}) {
  const iconBg: Record<string, string> = {
    blue: 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
    amber: 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
    red: 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-400',
    green: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
    violet: 'bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
  }
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-s transition-transform duration-200 hover:-translate-y-0.5 dark:border-slate-700/60 dark:bg-slate-900">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-[13px] font-medium leading-snug text-slate-500 dark:text-slate-400">{label}</div>
        {icon && <span className={cn('flex h-8 w-8 items-center justify-center rounded-lg', iconBg[tone])}>{icon}</span>}
      </div>
      <div className="mt-2 flex items-end gap-2">
        <span className="whitespace-nowrap text-[28px] font-semibold leading-none text-slate-900 dark:text-slate-100">{value}</span>
        {delta && (
          <span className={cn('mb-0.5 text-xs font-medium', up ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500')}>
            {up ? '↑' : '↓'} {delta}
          </span>
        )}
      </div>
    </div>
  )
}

/* ============ 页头 ============ */
export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-wide text-slate-900 dark:text-slate-100">{title}</h1>
        {sub && <div className="mt-1 text-[13px] text-slate-500 dark:text-slate-400">{sub}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/* ============ 卡片 ============ */
export function SectionCard({ title, extra, children, className, bodyClassName }: {
  title?: ReactNode; extra?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string
}) {
  return (
    <div className={cn('rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700/60 dark:bg-slate-900', className)}>
      {title !== undefined && (
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <h3 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">{title}</h3>
          {extra}
        </div>
      )}
      <div className={cn('p-5', bodyClassName)}>{children}</div>
    </div>
  )
}

/* ============ 文档行 ============ */
const docIcons: Record<string, ReactNode> = {
  pdf: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></svg>,
  img: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>,
  zip: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 2v20" /><path d="m6 6 6-4 6 4" /></svg>,
  code: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="m8 6-6 6 6 6" /><path d="m16 6 6 6-6 6" /></svg>,
  doc: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h8M8 17h5" /></svg>,
  clock: <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></svg>,
}

export function kindIcon(kind: string) {
  const k = kind.toLowerCase()
  if (k.includes('pdf')) return docIcons.pdf
  if (k.includes('png') || k.includes('jpg') || k.includes('截图') || k.includes('原型')) return docIcons.img
  if (k.includes('zip') || k.includes('压缩')) return docIcons.zip
  if (k.includes('代码') || k.includes('txt') || k.includes('yaml') || k.includes('伪代码')) return docIcons.code
  return docIcons.doc
}

export function DocRow({ name, meta, kind, tone = 'info', onClick, active }: {
  name: string; meta?: ReactNode; kind: string; tone?: Tone; onClick?: () => void; active?: boolean
}) {
  const iconBg: Record<Tone, string> = {
    info: 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400',
    suc: 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400',
    warn: 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400',
    err: 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-400',
    pur: 'bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400',
    cyn: 'bg-cyan-50 text-cyan-600 dark:bg-cyan-500/15 dark:text-cyan-400',
    orgx: 'bg-orange-50 text-orange-600 dark:bg-orange-500/15 dark:text-orange-400',
    gry: 'bg-slate-100 text-slate-500 dark:bg-slate-700/40 dark:text-slate-400',
    blk: 'bg-slate-800 text-slate-300',
  }
  return (
    <div className={cn('flex items-center gap-2.5 rounded-lg border px-3 py-2.5',
      active ? 'border-blue-300 bg-blue-50/60 dark:border-blue-500/40 dark:bg-blue-500/10' : 'border-slate-100 bg-white dark:border-slate-800 dark:bg-slate-900',
      onClick && 'cursor-pointer transition-colors hover:border-blue-300 dark:hover:border-blue-500/40')}
      onClick={onClick}>
      <span className={cn('flex h-7 w-7 flex-none items-center justify-center rounded-md', iconBg[tone])}>{kindIcon(kind)}</span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] font-medium text-slate-700 dark:text-slate-200">{name}</div>
        {meta && <div className="mt-0.5 text-[11.5px] text-slate-400 dark:text-slate-500">{meta}</div>}
      </div>
    </div>
  )
}

/* ============ 流程步骤（节点处理页左栏） ============ */
export function FlowSteps({ nodes }: { nodes: FlowNode[] }) {
  return (
    <ol className="relative space-y-0">
      {nodes.map((n, i) => (
        <li key={n.name} className="relative flex gap-3 pb-4 last:pb-0">
          {i < nodes.length - 1 && (
            <span className={cn('absolute left-[13px] top-7 h-full w-0.5',
              n.status === 'done' ? 'bg-emerald-400' : 'bg-slate-200 dark:bg-slate-700')} />
          )}
          <span className={cn('relative z-10 mt-1 flex h-7 w-7 flex-none items-center justify-center rounded-full text-[11px] font-semibold',
            n.status === 'done' && 'bg-emerald-500 text-white',
            n.status === 'current' && 'bg-blue-600 text-white ring-4 ring-blue-100 dark:ring-blue-500/25',
            n.status === 'return' && 'bg-red-500 text-white',
            (n.status === 'wait') && 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500')}>
            {n.status === 'done' ? (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
            ) : n.status === 'return' ? (
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 7h10a4 4 0 0 1 0 8H9" /><path d="m6 5-2 2 2 2" /></svg>
            ) : (
              <span className="text-[10px]">{i + 1}</span>
            )}
          </span>
          <div className="min-w-0 flex-1 pt-0.5">
            <div className={cn('flex items-center justify-between gap-2 text-[13px]',
              n.status === 'current' ? 'font-semibold text-blue-700 dark:text-blue-300'
                : n.status === 'done' ? 'font-medium text-slate-700 dark:text-slate-200' : 'text-slate-400 dark:text-slate-500')}>
              <span className="truncate">{n.name}</span>
              {n.status === 'current' && <span className="flex-none text-[10.5px] font-medium text-blue-600 dark:text-blue-400">进行中</span>}
            </div>
            {(n.assignee || n.time) && n.status !== 'wait' && (
              <div className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">
                {[n.assignee, n.time].filter(Boolean).join(' · ')}
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

/* ============ 处理历史时间线 ============ */
export function Timeline({ events }: {
  events: { time: string; title: string; desc: string; by: string; kind: string }[]
}) {
  const dotTone: Record<string, string> = {
    system: 'bg-slate-300 dark:bg-slate-600',
    user: 'bg-blue-500',
    agent: 'bg-violet-500',
    action: 'bg-red-400',
  }
  return (
    <div className="space-y-0">
      {events.map((e) => (
        <div key={e.time + e.title} className="relative flex gap-3 pb-4 last:pb-0">
          <span className="absolute left-[5px] top-6 h-full w-px bg-slate-100 last:hidden dark:bg-slate-800" />
          <span className={cn('relative z-10 mt-1.5 h-2.5 w-2.5 flex-none rounded-full', dotTone[e.kind] ?? dotTone.system)} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 text-[13px]">
              <span className="font-medium text-slate-700 dark:text-slate-200">{e.title}</span>
              <span className="text-[11px] text-slate-400">{e.time}</span>
            </div>
            <div className="mt-0.5 text-[12px] leading-relaxed text-slate-500 dark:text-slate-400">{e.desc}</div>
            <div className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">by {e.by}</div>
          </div>
        </div>
      ))}
    </div>
  )
}

/* ============ 空态 ============ */
export function EmptyState({ title, desc, action }: { title: string; desc?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-300 dark:bg-slate-800 dark:text-slate-600">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" /></svg>
      </span>
      <div className="mt-1 text-[13.5px] font-medium text-slate-600 dark:text-slate-300">{title}</div>
      {desc && <div className="max-w-sm text-[12px] text-slate-400 dark:text-slate-500">{desc}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

/* ============ 键值对 ============ */
export function Kv({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="py-1">
      <div className="text-[11px] font-medium text-slate-400 dark:text-slate-500">{k}</div>
      <div className="mt-0.5 text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{v}</div>
    </div>
  )
}

export function RiskTip({ children }: { children: ReactNode }) {
  return (
    <div className="risk-tip">
      <svg className="mt-0.5 flex-none" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  )
}

/* ============ 工具栏搜索框 ============ */
export function SearchInput({ placeholder, width = 260, onSearch }: { placeholder: string; width?: number; onSearch?: (v: string) => void }) {
  return (
    <div className="relative max-w-full" style={{ width }}>
      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
      </span>
      <input
        className="h-9 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm outline-none transition-all focus:border-blue-500 focus:ring-[3px] focus:ring-blue-500/10 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        placeholder={placeholder}
        onKeyDown={(e) => { if (e.key === 'Enter' && onSearch) onSearch((e.target as HTMLInputElement).value) }}
      />
    </div>
  )
}

/* ============ 权限点行（矩阵用） ============ */
export function MatrixCell({ on, disabled, onClick }: { on: boolean; disabled?: boolean; onClick: () => void }) {
  return (
    <td className="border-b border-slate-100 p-0 dark:border-slate-800">
      <button
        disabled={disabled}
        onClick={onClick}
        className={cn('flex h-full w-full items-center justify-center py-2.5 transition-colors',
          disabled ? 'cursor-not-allowed opacity-40' : 'hover:bg-slate-50 dark:hover:bg-slate-800/60')}
        title={disabled ? '系统管理员不可被降权（避免权限自锁）' : undefined}
      >
        <span className={cn('h-[15px] w-[15px] rounded-[4px] border transition-all',
          on ? 'border-blue-600 bg-blue-600 shadow-[inset_0_0_0_3px_white] dark:shadow-[inset_0_0_0_3px_#0f172a]' : 'border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800')} />
      </button>
    </td>
  )
}
