import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, CircleAlert, Clock3, Copy, ExternalLink, FileSearch, X } from 'lucide-react'
import { Badge, type Tone } from './common'
import { cn } from '../lib/utils'
import type { RuntimeEvent } from '../types'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿', testing: '测试中', published: '已发布', suspended: '已挂起', archived: '已归档',
  active: '活跃', unhealthy: '不健康', disabled: '已停用', healthy: '健康', degraded: '降级',
  queued: '排队中', running: '运行中', interrupted: '已中断', succeeded: '成功', failed: '失败',
  cancelled: '已取消', pending: '待审批', approved: '已批准', rejected: '已拒绝', expired: '已过期',
}

const STATUS_TONES: Record<string, Tone> = {
  published: 'suc', active: 'suc', healthy: 'suc', succeeded: 'suc', approved: 'suc',
  testing: 'warn', pending: 'warn', degraded: 'warn', interrupted: 'orgx', unhealthy: 'err',
  failed: 'err', rejected: 'err', running: 'info', queued: 'info', draft: 'pur', expired: 'gry',
}

export function OsStatusBadge({ status, label, dot = true }: { status: string; label?: string; dot?: boolean }) {
  return <Badge tone={STATUS_TONES[status] ?? 'gry'} dot={dot}>{label ?? STATUS_LABELS[status] ?? status}</Badge>
}

export function MetricStrip({ items }: { items: { label: string; value: ReactNode; note?: string; tone?: Tone }[] }) {
  return (
    <div className="mb-5 grid grid-cols-2 gap-3 xl:grid-cols-5">
      {items.map((item) => (
        <div key={item.label} className="rounded-xl border border-slate-200 bg-white px-4 py-3.5 dark:border-slate-700/60 dark:bg-slate-900">
          <div className="flex items-center justify-between gap-2 text-[11.5px] font-medium text-slate-400"><span>{item.label}</span>{item.tone && <span className={cn('h-2 w-2 rounded-full', item.tone === 'suc' ? 'bg-emerald-500' : item.tone === 'warn' ? 'bg-amber-500' : item.tone === 'err' ? 'bg-red-500' : 'bg-blue-500')} />}</div>
          <div className="mt-2 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">{item.value}</div>
          {item.note && <div className="mt-1 truncate text-[11px] text-slate-400">{item.note}</div>}
        </div>
      ))}
    </div>
  )
}

export function FilterBar({ query, onQueryChange, placeholder = '搜索名称、ID 或描述…', children }: { query: string; onQueryChange: (value: string) => void; placeholder?: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <label className="relative min-w-[220px] flex-1 sm:max-w-[320px]">
        <span className="sr-only">搜索</span>
        <FileSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder={placeholder} className="h-9 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-[12.5px] outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" />
      </label>
      {children}
    </div>
  )
}

export function ResourceTable<T extends { id: string }>({ columns, rows, onRowClick, emptyLabel = '暂无数据' }: { columns: { key: string; label: string; render: (row: T) => ReactNode }[]; rows: T[]; onRowClick?: (row: T) => void; emptyLabel?: string }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white dark:border-slate-700/60 dark:bg-slate-900">
      <table className="w-full min-w-[720px] border-collapse text-left">
        <thead><tr className="border-b border-slate-100 text-[11px] font-medium uppercase tracking-wide text-slate-400 dark:border-slate-800">{columns.map((column) => <th key={column.key} className="whitespace-nowrap px-4 py-3">{column.label}</th>)}</tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id} className={cn('border-b border-slate-50 last:border-0 dark:border-slate-800/70', onRowClick && 'cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/40')} onClick={() => onRowClick?.(row)}>{columns.map((column) => <td key={column.key} className="px-4 py-3.5 align-middle text-[12.5px] text-slate-600 dark:text-slate-300">{column.render(row)}</td>)}</tr>)}</tbody>
      </table>
      {!rows.length && <div className="px-5 py-12 text-center text-[12.5px] text-slate-400">{emptyLabel}</div>}
    </div>
  )
}

export function DetailDrawer({ open, title, eyebrow, onClose, children }: { open: boolean; title: string; eyebrow?: string; onClose: () => void; children: ReactNode }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => { if (open) closeRef.current?.focus() }, [open])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[70] flex justify-end" role="dialog" aria-modal="true" aria-labelledby="expert-os-drawer-title">
      <button className="absolute inset-0 cursor-default bg-slate-950/30 backdrop-blur-[1px]" aria-label="关闭详情" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-[520px] flex-col overflow-y-auto border-l border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-950">
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-slate-100 bg-white/95 px-5 py-4 backdrop-blur dark:border-slate-800 dark:bg-slate-950/95">
          <div className="min-w-0 flex-1"><div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-600 dark:text-blue-400">{eyebrow ?? '详情'}</div><h2 id="expert-os-drawer-title" className="mt-1 truncate text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h2></div>
          <button ref={closeRef} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200" onClick={onClose} aria-label="关闭详情"><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 p-5">{children}</div>
      </aside>
    </div>
  )
}

export function RuntimeEvent({ event, expanded, onToggle, onNavigate }: { event: RuntimeEvent; expanded: boolean; onToggle: () => void; onNavigate?: () => void }) {
  const icon = event.kind === 'approval' ? <CircleAlert className="h-4 w-4" /> : event.kind === 'citation' ? <FileSearch className="h-4 w-4" /> : event.kind === 'tool' ? <ChevronRight className="h-4 w-4" /> : <Clock3 className="h-4 w-4" />
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/70 dark:border-slate-700 dark:bg-slate-800/40" aria-live="polite">
      <button className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left" onClick={onToggle} aria-expanded={expanded}>
        <span className={cn('flex h-7 w-7 items-center justify-center rounded-md', event.status === 'failed' ? 'bg-red-50 text-red-500 dark:bg-red-500/15' : event.status === 'pending' ? 'bg-amber-50 text-amber-600 dark:bg-amber-500/15' : 'bg-white text-slate-500 dark:bg-slate-900 dark:text-slate-300')}>{icon}</span>
        <span className="min-w-0 flex-1"><span className="block truncate text-[12px] font-medium text-slate-700 dark:text-slate-200">{event.title}</span><span className="block truncate text-[10.5px] text-slate-400">{event.status === 'succeeded' ? '已完成' : event.status === 'pending' ? '等待审批' : event.status === 'failed' ? '失败' : event.status}{event.duration ? ` · ${event.duration}` : ''}</span></span>
        <ChevronRight className={cn('h-3.5 w-3.5 flex-none text-slate-400 transition-transform', expanded && 'rotate-90')} />
      </button>
      {expanded && <div className="border-t border-slate-200 px-3 pb-3 pt-2.5 text-[11.5px] leading-relaxed text-slate-500 dark:border-slate-700 dark:text-slate-400"><p>{event.detail}</p>{event.risk && <p className="mt-1">风险级别：<b className="font-medium text-slate-700 dark:text-slate-200">{STATUS_LABELS[event.risk] ?? event.risk}</b></p>}{event.locator && <button className="mt-2 inline-flex items-center gap-1 font-medium text-blue-600 hover:underline" onClick={onNavigate}><ExternalLink className="h-3 w-3" />查看来源 {event.locator}</button>}</div>}
    </div>
  )
}

export function CopyValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)
  return <button className="inline-flex items-center gap-1 font-mono text-[11px] text-slate-400 hover:text-blue-600" onClick={() => { void navigator.clipboard?.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1200) }} title="复制"><Copy className="h-3 w-3" />{copied ? '已复制' : value}</button>
}
