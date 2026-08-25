import { useEffect, useRef, useState } from 'react'
import {
  LayoutGrid, Bell, BarChart3, FolderKanban, Workflow,
  FileText, Bot, Building2, ShieldCheck, ScrollText, LogOut, Search,
  Moon, Sun, Webhook,
} from 'lucide-react'
import { useApp, toast, ROLE_NAV } from '../store/app-store'
import { api } from '../lib/api'
import { onNotification, subscribeToNotifications } from '../lib/notification-stream'
import { Avatar } from './common'
import { cn } from '../lib/utils'
import type { PageId } from '../types'

const NAV: { grp: string; items: { id: PageId; label: string; icon: React.ReactNode; badge?: number; dot?: boolean }[] }[] = [
  {
    grp: '工作台',
    items: [
      { id: 'tasks', label: '我的任务', icon: <LayoutGrid className="h-[17px] w-[17px]" />, badge: 6 },
      { id: 'notif', label: '通知中心', icon: <Bell className="h-[17px] w-[17px]" />, dot: true },
      { id: 'dashboard', label: '领导看板', icon: <BarChart3 className="h-[17px] w-[17px]" /> },
    ],
  },
  {
    grp: '业务',
    items: [
      { id: 'projects', label: '项目列表', icon: <FolderKanban className="h-[17px] w-[17px]" /> },
      { id: 'templates', label: '流程模板', icon: <Workflow className="h-[17px] w-[17px]" /> },
    ],
  },
  {
    grp: '资源',
    items: [
      { id: 'docs', label: '文档中心', icon: <FileText className="h-[17px] w-[17px]" /> },
      { id: 'agents', label: 'Agent 管理', icon: <Bot className="h-[17px] w-[17px]" /> },
    ],
  },
  {
    grp: '系统',
    items: [
      { id: 'org', label: '组织管理', icon: <Building2 className="h-[17px] w-[17px]" /> },
      { id: 'channel', label: '渠道配置', icon: <Webhook className="h-[17px] w-[17px]" /> },
      { id: 'matrix', label: '权限矩阵', icon: <ShieldCheck className="h-[17px] w-[17px]" /> },
      { id: 'audit', label: '审计中心', icon: <ScrollText className="h-[17px] w-[17px]" /> },
    ],
  },
]

const ROLE_LABEL: Record<string, string> = {
  system_admin: '系统管理员', organization_admin: '组织管理员', project_admin: '项目管理员',
  leader: '领导', product_manager: '产品经理', developer: '开发', after_sales: '售后',
  pre_sales: '售前', second_line: '二线',
}

function RoleBadge({ role }: { role: string }) {
  return (
    <span className="rounded-md bg-blue-50 px-1.5 py-0.5 text-[11px] font-medium text-blue-600 dark:bg-blue-500/15 dark:text-blue-400">
      {ROLE_LABEL[role] ?? role}
    </span>
  )
}

export function Sidebar() {
  const { page, navigate, role, logout, currentUser } = useApp()
  const visible = new Set(ROLE_NAV[role])
  const user = currentUser ?? { name: '未登录', account: '-', avatarGrad: undefined as string | undefined }

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-[60px] flex-col bg-sidebar text-sidebar-foreground md:w-[224px]">
      <div className="flex h-[60px] flex-none items-center gap-2.5 px-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-white">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 7h10a4 4 0 0 1 0 8H9" /><path d="m6 5-2 2 2 2" /><path d="M20 17H10a4 4 0 0 1 0-8h5" /><path d="m18 15 2-2-2-2" />
          </svg>
        </span>
        <span>
          <b className="hidden text-[14px] font-semibold text-white md:block">FlowHub 流枢</b>
          <small className="hidden text-[10px] tracking-wider text-slate-400 md:block">PRIVATE DEPLOY · v2.3</small>
        </span>
      </div>
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {NAV.map((g) => {
          const items = g.items.filter((i) => visible.has(i.id))
          if (items.length === 0) return null
          return (
            <div key={g.grp}>
              <div className="sb-grp hidden md:block">{g.grp}</div>
              {items.map((i) => (
                <button
                  key={i.id}
                  onClick={() => navigate(i.id)}
                  className={cn(
                    'mb-0.5 flex w-full items-center justify-center gap-2.5 rounded-lg px-0 py-2 text-[13px] transition-colors md:justify-start md:px-3 md:text-left',
                    page === i.id
                      ? 'bg-sidebar-accent font-semibold text-blue-400'
                      : 'text-slate-300 hover:bg-sidebar-accent/60 hover:text-slate-100',
                  )}
                >
                  <span className={cn(page === i.id ? 'text-blue-400' : 'text-slate-400')}>{i.icon}</span>
                  <span className="hidden flex-1 md:block">{i.label}</span>
                  {i.badge ? (
                    <span className="rounded-full bg-blue-600 px-1.5 py-0.5 text-[10.5px] font-semibold text-white">{i.badge}</span>
                  ) : i.dot ? (
                    <span className="h-2 w-2 rounded-full bg-amber-400" />
                  ) : null}
                </button>
              ))}
            </div>
          )
        })}
      </nav>
      <div className="flex flex-none items-center justify-center gap-2.5 border-t border-sidebar-border px-0 py-3 md:justify-start md:px-4">
        <Avatar name={user.name} grad={user.avatarGrad} size={30} />
        <div className="hidden min-w-0 flex-1 md:block">
          <b className="block truncate text-[13px] text-slate-100">{user.name}</b>
          <small className="block truncate text-[10.5px] text-slate-400">{user.account}</small>
        </div>
        <button className="text-slate-400 transition-colors hover:text-slate-100" onClick={logout} title="退出登录">
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </aside>
  )
}

export function Topbar() {
  const { page, logout, navigate, openDialog, openTask, openWorkItem, currentUser, orgUsers } = useApp()
  const [dark, setDark] = useState(false)
  const [notifOpen, setNotifOpen] = useState(false)
  const [userOpen, setUserOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchResults, setSearchResults] = useState<{ type: string; title: string; desc: string; to: string }[]>([])
  const [notifications, setNotifications] = useState<{ id: string; title: string; body: string; time: string; unread: boolean; kind: string; failed?: boolean; wiId?: string; taskId?: string }[]>([])
  const loadNotifs = () => {
    api.get<{ items: { id: string; title: string; body: string; time: string; unread: boolean; kind: string; failed?: boolean; wiId?: string; taskId?: string }[] }>('/api/v1/notifications?page_size=50')
      .then((d) => setNotifications(d.items))
      .catch(() => {})
  }
  useEffect(() => {
    loadNotifs()
    const unsubscribe = onNotification((notification) => {
      const nextNotification = { ...notification, unread: notification.unread ?? true }
      setNotifications((previous) => [nextNotification, ...previous.filter((item) => item.id !== notification.id)])
    })
    return () => unsubscribe()
  }, [])
  useEffect(() => subscribeToNotifications(), [])
  /* 铃铛：最近通知（未读优先），红点显示未读数 */
  const unreadCount = notifications.filter((n) => n.unread).length
  const recentNotifs = [...notifications].sort((a, b) => Number(b.unread) - Number(a.unread)).slice(0, 5)
  const markRead = (id: string) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, unread: false } : n)))
    api.post('/api/v1/notifications/read', { ids: [id] }).catch(() => {})
  }
  const searchTimer = useRef<number | null>(null)

  const titles: Record<PageId, [string, string]> = {
    tasks: ['我的任务', '工作台 / 我的任务'],
    notif: ['通知中心', '工作台 / 通知中心'],
    dashboard: ['领导看板', '工作台 / 领导看板'],
    projects: ['项目列表', '业务 / 项目列表'],
    templates: ['流程模板', '业务 / 流程模板'],
    canvas: ['流程画布 · 需求流程 v3', '业务 / 流程画布'],
    workitem: ['工作项详情', '业务 / 工作项详情'],
    node: ['节点处理 · 测试', '业务 / 节点处理'],
    docs: ['文档中心', '资源 / 文档中心'],
    agents: ['Agent 管理', '资源 / Agent 管理'],
    org: ['组织管理', '系统 / 组织管理'],
    channel: ['渠道配置', '系统 / 渠道配置'],
    matrix: ['权限矩阵', '系统 / 权限矩阵'],
    audit: ['审计中心', '系统 / 审计中心'],
  }
  const [title, crumb] = titles[page]

  const toggleTheme = () => {
    setDark(!dark)
    document.documentElement.classList.toggle('dark', !dark)
  }

  /* 当前用户完整资料：login brief 优先，orgUsers 补充头像渐变（后端 /org/users 权威） */
  const me = orgUsers.find((u) => u.id === currentUser?.id) ?? currentUser

  return (
    <header className="sticky top-0 z-30 flex h-[60px] flex-none items-center gap-3 border-b border-slate-200 bg-white px-6 dark:border-slate-800 dark:bg-slate-900">
      <div className="min-w-0">
        <div className="truncate text-[15px] font-semibold text-slate-900 dark:text-slate-100">{title}</div>
        <div className="truncate text-[11.5px] text-slate-400">{crumb}</div>
      </div>
      <div className="ml-auto flex items-center gap-2.5">
        <span className="hidden items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-1 text-[11.5px] font-medium text-amber-700 md:flex dark:bg-amber-500/10 dark:text-amber-400">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />企微通知待重试 {notifications.filter((n) => n.failed).length} 条
        </span>
        <div className="relative hidden lg:block">
          <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            className="h-9 w-[220px] rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-[13px] outline-none transition-all focus:border-blue-500 focus:ring-[3px] focus:ring-blue-500/10 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-200"
            placeholder="搜索工作项 / 文档 / 请求 ID…"
            onFocus={() => setSearchOpen(true)}
            onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
            onChange={(e) => {
              const v = e.target.value.trim()
              if (!v) { setSearchResults([]); return }
              // 防抖调后端 GET /search
              if (searchTimer.current !== null) window.clearTimeout(searchTimer.current)
              searchTimer.current = window.setTimeout(() => {
                api.get<{ items: { type: string; title: string; desc: string; to: string }[] }>(`/api/v1/search?q=${encodeURIComponent(v)}`)
                  .then((d) => setSearchResults(d.items))
                  .catch(() => {})
              }, 250)            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const v = (e.target as HTMLInputElement).value.trim()
                if (v) {
                  setSearchOpen(false)
                  navigate('tasks')
                  toast(`全局搜索：${v}`)
                } else {
                  setSearchOpen(true)
                }
              }
            }}
          />
          {searchOpen && (
            <div
              className="absolute right-0 top-11 z-50 w-[340px] rounded-xl border border-slate-200 bg-white p-3 shadow-l dark:border-slate-700 dark:bg-slate-900"
              onMouseDown={(e) => e.preventDefault()}>
              <div className="mb-2 text-xs font-semibold text-slate-400">快速定位</div>
              {searchResults.length ? searchResults.map((r, i) => (
                <button key={i} className="flex w-full items-start gap-2 rounded-lg p-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800"
                  onClick={() => { setSearchOpen(false); navigate(r.to as PageId) }}>
                  <Search className="mt-0.5 h-3.5 w-3.5 flex-none text-slate-300" />
                  <span><span className="block truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{r.title}</span><span className="text-[11px] text-slate-400">{r.desc}</span></span>
                </button>
              )) : (
                <div className="rounded-lg bg-slate-50 p-3 text-center text-[11.5px] text-slate-400 dark:bg-slate-800/60">输入关键字搜索工作项 / 文档 / 请求 ID</div>
              )}
              {searchResults.length > 0 && (
                <div className="mt-2 border-t border-slate-100 pt-2 text-center text-[11px] text-slate-400 dark:border-slate-800">搜索结果来自后端</div>
              )}
            </div>
          )}
        </div>
        <button className="relative flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={() => setNotifOpen(!notifOpen)} title="通知中心">
          <Bell className="h-[18px] w-[18px]" />
          {unreadCount > 0 && (
            <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[9.5px] font-bold leading-none text-white ring-2 ring-white dark:ring-slate-900">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </button>
        {notifOpen && (
          <div className="absolute right-40 top-[52px] z-50 w-[360px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-l dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 dark:border-slate-800">
              <b className="text-sm text-slate-800 dark:text-slate-100">通知{unreadCount > 0 ? `（${unreadCount} 条未读）` : ''}</b>
              <button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => { setNotifOpen(false); navigate('notif') }}>查看全部</button>
            </div>
            {recentNotifs.length === 0 && (
              <div className="px-4 py-8 text-center text-[12px] text-slate-400">暂无通知</div>
            )}
            {recentNotifs.map((n) => (
              <button key={n.id} className="flex w-full items-start gap-3 border-b border-slate-50 px-4 py-3 text-left transition-colors hover:bg-slate-50 dark:border-slate-800/60 dark:hover:bg-slate-800/60"
                onClick={() => {
                  setNotifOpen(false)
                  if (n.unread) markRead(n.id)
                  if (n.kind === 'agent') openDialog('agentConfirm')
                  else if (n.kind === 'arrive' && n.taskId) openTask(n.taskId, n.wiId)
                  else if (n.wiId) openWorkItem(n.wiId)
                  else navigate('notif')
                }}>
                <span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400">
                  {n.kind === 'agent' ? <Bot className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{n.title}：{n.body}</span>
                  <span className="mt-0.5 block text-[11px] text-slate-400">{n.time}</span>
                </span>
                {n.unread && <span className="mt-1.5 h-2 w-2 flex-none rounded-full bg-blue-500" />}
              </button>
            ))}
          </div>
        )}
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={toggleTheme} title="切换深浅主题">
          {dark ? <Sun className="h-[17px] w-[17px]" /> : <Moon className="h-[17px] w-[17px]" />}
        </button>
        <button className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={logout} title="退出登录">
          <LogOut className="h-[17px] w-[17px]" />
        </button>
        <div className="relative">
          <button
            className="flex items-center gap-2 rounded-lg px-1.5 py-1 transition-colors hover:bg-slate-100 dark:hover:bg-slate-800"
            onClick={() => setUserOpen(!userOpen)}
            title="点击查看个人信息">
            <Avatar name={me?.name ?? '未登录'} grad={me?.avatarGrad as 'g1' | 'g2' | 'g3' | 'g4' | 'g5' | 'g6' | undefined} size={30} />
            <span className="hidden lg:block">
              <b className="block text-[12.5px] leading-tight text-slate-800 dark:text-slate-100">{me?.name ?? '未登录'}</b>
              <small className="block text-[10.5px] text-slate-400">{me?.account ?? '-'}</small>
            </span>
          </button>
          {userOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setUserOpen(false)} />
              <div className="absolute right-0 top-[46px] z-50 w-[300px] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-l dark:border-slate-700 dark:bg-slate-900">
                {/* 用户信息头 */}
                <div className="flex items-center gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3.5 dark:border-slate-800 dark:bg-slate-800/40">
                  <Avatar name={me?.name ?? '未登录'} grad={me?.avatarGrad as 'g1' | 'g2' | 'g3' | 'g4' | 'g5' | 'g6' | undefined} size={42} />
                  <div className="min-w-0">
                    <b className="block truncate text-[14px] text-slate-800 dark:text-slate-100">{me?.name ?? '未登录'}</b>
                    <span className="block truncate text-[11.5px] text-slate-400">{me?.account ?? '-'}</span>
                  </div>
                </div>
                <div className="space-y-2.5 px-4 py-3.5">
                  <div className="flex justify-between gap-3 text-[12.5px]">
                    <span className="flex-none text-slate-400">部门</span>
                    <span className="truncate text-right text-slate-700 dark:text-slate-200">{me?.dept || '—'}</span>
                  </div>
                  <div className="flex justify-between gap-3 text-[12.5px]">
                    <span className="flex-none text-slate-400">角色</span>
                    <span className="flex flex-wrap justify-end gap-1">
                      {(me?.roles ?? []).length ? me!.roles!.map((r) => <RoleBadge key={r} role={r} />) : <span className="text-slate-400">—</span>}
                    </span>
                  </div>
                  <div className="flex justify-between gap-3 text-[12.5px]">
                    <span className="flex-none text-slate-400">技能</span>
                    <span className="truncate text-right text-slate-700 dark:text-slate-200">{(me?.skills ?? []).length ? me!.skills!.join(' / ') : '—'}</span>
                  </div>
                  <div className="flex justify-between gap-3 text-[12.5px]">
                    <span className="flex-none text-slate-400">状态</span>
                    <span className="text-slate-700 dark:text-slate-200">{me?.status === 'active' ? '正常' : (me?.status ?? '—')}</span>
                  </div>
                </div>
                <div className="border-t border-slate-100 px-3 py-2.5 dark:border-slate-800">
                  <button
                    className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-[12.5px] font-medium text-slate-600 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-red-500/40 dark:hover:bg-red-500/10 dark:hover:text-red-400"
                    onClick={() => { setUserOpen(false); logout() }}>
                    <LogOut className="h-4 w-4" />退出登录
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
