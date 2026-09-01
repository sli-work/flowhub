import { useEffect, useState } from 'react'
import {
  Bell, Bot, Clock, Undo2, CheckCircle2, ArrowRightLeft, Info, TriangleAlert, RefreshCw,
} from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { onNotification } from '../lib/notification-stream'
import { Badge, PageHeader } from '../components/common'
import { cn } from '../lib/utils'
import type { NotificationItem } from '../types'

const kindIcon = (k: NotificationItem['kind']) => {
  switch (k) {
    case 'arrive': return <Bell className="h-4 w-4" />
    case 'agent': return <Bot className="h-4 w-4" />
    case 'timeout': return <Clock className="h-4 w-4" />
    case 'return': return <Undo2 className="h-4 w-4" />
    case 'complete': return <CheckCircle2 className="h-4 w-4" />
    case 'transfer': return <ArrowRightLeft className="h-4 w-4" />
    case 'fail': return <TriangleAlert className="h-4 w-4" />
    default: return <Info className="h-4 w-4" />
  }
}
const kindTone = (k: NotificationItem['kind']) =>
  k === 'agent' ? 'bg-violet-50 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400'
    : k === 'timeout' || k === 'fail' ? 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-400'
    : k === 'return' ? 'bg-amber-50 text-amber-600 dark:bg-amber-500/15 dark:text-amber-400'
    : k === 'complete' ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400'
    : 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400'

const PAGE_SIZE = 10

export function NotificationsPage() {
  const { navigate, openDialog, openTask, openWorkItem } = useApp()
  const [tab, setTab] = useState<'all' | 'mine' | 'agent' | 'failed'>('all')
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<{ all: number; unread: number; agent: number; failed: number } | null>(null)
  /* 渠道健康：GET /notifications/channels/health（钉钉/企微/邮件/站内配置状态） */
  const [health, setHealth] = useState<Record<string, { enabled: boolean; ok: boolean; desc: string }>>({})

  /* Tab → 服务端过滤参数（全部 / 待我处理=未读 / Expert / 发送失败） */
  const tabParams = (t: typeof tab): Record<string, string> => {
    if (t === 'mine') return { unread: 'true' }
    if (t === 'agent') return { kind: 'agent' }
    if (t === 'failed') return { failed: 'true' }
    return {}
  }

  const refresh = (nextTab: typeof tab = tab, nextPage: number = page) => {
    const qs = new URLSearchParams({ page: String(nextPage), page_size: String(PAGE_SIZE), ...tabParams(nextTab) })
    api.get<{ items: NotificationItem[]; total: number; stats: { all: number; unread: number; agent: number; failed: number } }>(`/api/v1/notifications?${qs}`)
      .then((d) => { setNotifications(d.items); setTotal(d.total); setStats(d.stats) })
      .catch(() => {})
  }

  /* 接后端：GET /notifications（服务端分页 + Tab 过滤）+ 渠道健康 */
  useEffect(() => {
    refresh()
    api.get<{ channels: Record<string, { enabled: boolean; ok: boolean; desc: string }> }>('/api/v1/notifications/channels/health')
      .then((d) => setHealth(d.channels))
      .catch(() => {})
  }, [tab, page])

  /* 切 Tab 回到第 1 页（避免翻页后切换分类出现空页） */
  const changeTab = (t: typeof tab) => { if (t !== tab) { setTab(t); setPage(1) } }

  useEffect(() => onNotification(() => {
    // 服务端分页下直接刷新当前视图，保证计数与列表一致
    refresh()
  }), [tab, page])

  const retry = async (n: NotificationItem) => {
    try {
      const d = await api.post<{ retries: number }>(`/api/v1/notifications/${n.id}/retry`)
      toast((d.retries ?? 0) > (n.retries ?? 0) ? '已重新投递全部已启用渠道' : '重试完成')
      refresh()  // 重新拉取渠道投递结果与失败状态
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '重试失败')
    }
  }

  const counts = {
    all: stats?.all ?? 0,
    mine: stats?.unread ?? 0,
    agent: stats?.agent ?? 0,
    failed: stats?.failed ?? 0,
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)

  const onOpen = (n: NotificationItem) => {
    if (n.kind === 'agent') openDialog('expertApproval')
    else if (n.failed) retry(n)
    else if (n.kind === 'arrive' || n.kind === 'transfer') {
      // 跳转到对应任务处理页（携带真实任务 ID），无关联任务则提示回列表
      if (n.taskId) openTask(n.taskId, n.wiId)
      else toast('该通知未关联处理任务，请到任务列表查看')
    }
    else if (n.wiId) openWorkItem(n.wiId)
    else toast('该通知未关联工作项，请到通知列表查看')
  }

  return (
    <div className="page-container">
      <PageHeader
        title="通知中心"
        sub="钉钉 / 企微 / 邮件 / 站内 四渠道 · 通知为异步副作用，发送失败不回滚流程 · 失败可人工重试（PRD §11）"
        actions={
          <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            onClick={() => navigate('channel')}>
            <RefreshCw className="h-4 w-4" />渠道配置
          </button>
        }
      />

      {/* 渠道健康状态（钉钉/企微/邮件/站内，来自后端 channels/health） */}
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
        <span className="text-[12px] font-medium text-slate-500 dark:text-slate-400">通知渠道：</span>
        {Object.entries(health).map(([name, h]) => (
          <span key={name} title={h.desc}
            className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] font-medium',
              h.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500')}>
            <span className={cn('h-1.5 w-1.5 rounded-full', h.enabled ? 'bg-emerald-500' : 'bg-slate-300 dark:bg-slate-600')} />
            {name} {h.enabled ? '可用' : '未配置'}
          </span>
        ))}
        {Object.keys(health).length === 0 && <span className="text-[11.5px] text-slate-400">渠道状态加载中…</span>}
        <span className="ml-auto text-[11px] text-slate-400">配置 .env 的 DINGTALK/WECOM_WEBHOOK、SMTP_* 后渠道即生效</span>
      </div>

      <div className="mb-4 flex items-center gap-1 border-b border-slate-200 dark:border-slate-700">
        {([
          ['all', '全部', counts.all], ['mine', '待我处理', counts.mine], ['agent', 'Expert 待审批', counts.agent], ['failed', '发送失败', counts.failed],
        ] as const).map(([k, label, cnt]) => (
          <button key={k} onClick={() => changeTab(k)}
            className={cn('-mb-px flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-[13.5px] font-medium transition-colors',
              tab === k ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300')}>
            {label}
            <span className={cn('rounded-full px-1.5 text-[10.5px] font-semibold', tab === k ? 'bg-blue-100 text-blue-700 dark:bg-blue-500/20 dark:text-blue-300' : 'bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-400')}>{cnt}</span>
          </button>
        ))}
      </div>

      <div className="space-y-2">
        {notifications.map((n) => (
          <button key={n.id} onClick={() => onOpen(n)}
            className={cn('flex w-full items-start gap-3 rounded-xl border bg-white p-4 text-left shadow-s transition-all hover:border-blue-300 hover:shadow-m dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/40',
              n.unread ? 'border-blue-200 dark:border-blue-500/30' : 'border-slate-200',
              n.failed ? 'border-red-200 dark:border-red-500/30' : '')}>
            <span className={cn('flex h-9 w-9 flex-none items-center justify-center rounded-lg', kindTone(n.kind))}>{kindIcon(n.kind)}</span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <b className="text-[13.5px] text-slate-800 dark:text-slate-100">{n.title}</b>
                {n.failed && <Badge tone="err">failed ×{n.retries}</Badge>}
                {n.unread && <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />}
              </span>
              <span className="mt-0.5 block text-[12.5px] text-slate-500 dark:text-slate-400">{n.body}</span>
              <span className="mt-1.5 block text-[11.5px] text-slate-400">
                {n.time} · {n.channels.map((c) => `${c.name} ${c.ok ? '✓' : '✗（重试中）'}`).join(' · ')}
              </span>
            </span>
            {(n.kind === 'agent' || n.failed) && (
              <span className="flex-none rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-medium text-blue-600 hover:bg-blue-50 dark:border-slate-600 dark:hover:bg-blue-500/10">
                {n.failed ? '重试' : '确认'}
              </span>
            )}
          </button>
        ))}
        {notifications.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-[13px] text-slate-400 dark:border-slate-700 dark:bg-slate-900">当前分类无通知</div>
        )}
        {/* 分页 footer（服务端分页） */}
        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between border-t border-slate-100 pt-3 text-[12px] text-slate-400 dark:border-slate-800">
            <span>共 {total} 条通知 · 第 {safePage}/{totalPages} 页</span>
            <div className="flex items-center gap-1.5">
              <button
                className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                  safePage <= 1 ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                上一页
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
                <button key={p}
                  className={cn('h-7 w-7 rounded-md border text-[12px] transition-colors',
                    p === safePage ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                  onClick={() => setPage(p)}>{p}</button>
              ))}
              <button
                className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                  safePage >= totalPages ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
                下一页
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
