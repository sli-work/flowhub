import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import type { PageId, RoleKey, User } from '../types'
import { api, clearAuthCache, getStoredUser, getToken } from '../lib/api'

export type DialogName =
  | 'submit' | 'return' | 'transfer' | 'expertApproval'
  | 'validate' | 'retryNotify' | 'registerAccount' | 'changePwd' | 'createUser'
  | 'registerApprove' | 'permissionDemo' | 'userTakeover'

export interface CheckProblem {
  title: string
  nodeId: string
  desc: string
}

interface AppState {
  authed: boolean
  role: RoleKey
  page: PageId
  dialog: DialogName | null
  taskCounter: number
  orgUsers: User[]
  currentUser: User | null
  /** 画布定位目标节点（发布校验问题清单「定位」按钮触发） */
  locateNode: string | null
  /** 画布静态校验问题清单（供发布校验弹窗读取并定位） */
  checkProblems: CheckProblem[]
  /** 画布目标模板（模板页点开某模板时设置，画布按此加载） */
  canvasTarget: { templateId: string; templateName: string; version: string } | null
  /** Expert 编辑器目标：null=新建（未落库草稿），非空=编辑该 Expert */
  expertEditorTarget: string | null
  /** 注册审批弹窗目标：null=批量模式（全量多选/全部），非空=单个审批该用户 */
  approvalTargetId: string | null
  /** 当前工作项 / 任务（详情页与「去处理」页按此拉取真实数据） */
  activeWiId: string | null
  activeTaskId: string | null
  updateUser: (u: User) => void
  assignUserRoles: (userId: string, roles: string[]) => void
  /** 重新拉取用户表（审批/解锁等变更后刷新，保证用户管理列表与后端一致） */
  refreshOrgUsers: () => void
  navigate: (p: PageId) => void
  /** 打开工作项详情（携带 ID，详情页按真实数据渲染） */
  openWorkItem: (wiId: string) => void
  /** 打开任务处理页（携带任务 ID，处理页按真实任务渲染；可同时指定所属工作项便于返回） */
  openTask: (taskId: string, wiId?: string) => void
  login: () => void
  logout: () => void
  openDialog: (d: DialogName) => void
  closeDialog: () => void
  openApproval: (uid?: string | null) => void

  bumpTask: () => void
  locateCanvasNode: (nodeId: string) => void
  setCheckProblems: (problems: CheckProblem[]) => void
  openCanvas: (templateId: string, templateName: string, version: string) => void
  /** 打开 Expert 编辑器：不传或传 null 进入新建模式 */
  openExpertEditor: (expertId?: string | null) => void
}

const AppContext = createContext<AppState | null>(null)

/** 由登录用户角色推导前端视角（权限矩阵 34 权限点驱动导航可见性）。
 *  登录与刷新初始化共用，保证刷新后权限视角不丢失（'org' 视角可见权限矩阵/组织管理等）。 */
function mapRole(u: unknown): RoleKey {
  const roles = (u as { roles?: string[] } | null)?.roles ?? []
  return roles.includes('system_admin') || roles.includes('organization_admin') ? 'org'
    : roles.includes('project_admin') || roles.includes('leader') ? 'leader'
    : roles.includes('developer') ? 'dev'
    : 'sales'
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => !!getToken())
  // 刷新恢复：从 localStorage 的登录用户推导视角（与 login() 一致）
  const [role, setRole] = useState<RoleKey>(() => mapRole(getStoredUser()))
  const [page, setPage] = useState<PageId>('tasks')
  const [dialog, setDialog] = useState<DialogName | null>(null)
  const [taskCounter, setTaskCounter] = useState(0)
  /* 用户表单一事实源：组织管理「用户编辑」与权限矩阵「角色成员」双向共享（数据来自后端 /org/users） */
  const [orgUsers, setOrgUsers] = useState<User[]>([])
  const [currentUser, setCurrentUser] = useState<User | null>(getStoredUser() as User | null)
  const [locateNode, setLocateNode] = useState<string | null>(null)
  const [checkProblems, setCheckProblemsState] = useState<CheckProblem[]>([])
  const [canvasTarget, setCanvasTarget] = useState<{ templateId: string; templateName: string; version: string } | null>(null)
  const [expertEditorTarget, setExpertEditorTarget] = useState<string | null>(null)
  const [approvalTargetId, setApprovalTargetId] = useState<string | null>(null)
  const [activeWiId, setActiveWiId] = useState<string | null>(null)
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)

  /* 挂载时从后端拉取用户表（单一事实源：组织管理 ↔ 权限矩阵角色成员同步） */
  useEffect(() => {
    if (!getToken()) return
    api.get<{ items: User[] }>('/api/v1/org/users')
      .then((d) => { if (d.items.length) setOrgUsers(d.items) })
      .catch(() => { /* 后端不可用：保持空列表 */ })
  }, [])

  const updateUser = useCallback((u: User) => {
    setOrgUsers((prev) => prev.map((x) => (x.id === u.id ? u : x)))
  }, [])

  const refreshOrgUsers = useCallback(() => {
    api.get<{ items: User[] }>('/api/v1/org/users')
      .then((d) => { if (d.items.length) setOrgUsers(d.items) })
      .catch(() => { /* 后端不可用：保持现有列表 */ })
  }, [])

  const assignUserRoles = useCallback((userId: string, roles: string[]) => {
    setOrgUsers((prev) => prev.map((x) => (x.id === userId ? { ...x, roles } : x)))
  }, [])

  const navigate = useCallback((p: PageId) => {
    setPage(p)
    window.scrollTo({ top: 0 })
  }, [])

  /** 打开工作项详情（携带 ID，详情页按真实数据渲染，不再固定取第一条） */
  const openWorkItem = useCallback((wiId: string) => {
    setActiveWiId(wiId)
    setPage('workitem')
    window.scrollTo({ top: 0 })
  }, [])

  /** 打开任务处理页（携带任务 ID，处理页按真实任务渲染；可同时指定所属工作项便于返回） */
  const openTask = useCallback((taskId: string, wiId?: string) => {
    setActiveTaskId(taskId)
    if (wiId) setActiveWiId(wiId)
    setPage('node')
    window.scrollTo({ top: 0 })
  }, [])

  /** 发布校验问题定位：跳转画布并高亮目标节点 */
  const locateCanvasNode = useCallback((nodeId: string) => {
    setLocateNode(nodeId)
    setPage('canvas')
  }, [])

  const setCheckProblems = useCallback((problems: CheckProblem[]) => setCheckProblemsState(problems), [])

  const login = useCallback(() => {
    const u = getStoredUser() as (User & { roles?: string[] }) | null
    setCurrentUser(u)
    // 由登录用户的真实角色推导前端视角（权限矩阵 34 权限点驱动导航可见性）
    setRole(mapRole(u))
    setAuthed(true)
    setPage('tasks')
    window.dispatchEvent(new Event('flowhub-auth-changed'))
    // 重新拉取用户表（登出已清空 orgUsers，避免账号切换后仍显示上一账号的数据）
    api.get<{ items: User[] }>('/api/v1/org/users')
      .then((d) => { if (d.items.length) setOrgUsers(d.items) })
      .catch(() => { /* 后端不可用：保持空列表 */ })
    toast.success('登录成功：身份已映射')
  }, [])

  const logout = useCallback(() => {
    // 1) 清理全部认证缓存（localStorage：token/user + flowhub_ 前缀残留；sessionStorage 同前缀）
    clearAuthCache()
    // 2) 复位所有会话态（防止下一账号登录时残留上一账号的数据）
    setAuthed(false)
    setRole('leader')
    setCurrentUser(null)
    setOrgUsers([])
    setTaskCounter(0)
    setDialog(null)
    setCanvasTarget(null)
    setExpertEditorTarget(null)
    setActiveWiId(null)
    setActiveTaskId(null)
    setLocateNode(null)
    setCheckProblems([])
    setPage('tasks')
  }, [setCheckProblems])

  const openDialog = useCallback((d: DialogName) => setDialog(d), [])
  const closeDialog = useCallback(() => setDialog(null), [])
  /** 打开注册审批：传 uid 为单个审批该用户；不传/传 null 为批量模式（多选/全部通过） */
  const openApproval = useCallback((uid?: string | null) => {
    setApprovalTargetId(uid ?? null)
    setDialog('registerApprove')
  }, [])
  const bumpTask = useCallback(() => setTaskCounter((c) => c + 1), [])
  const openCanvas = useCallback((templateId: string, templateName: string, version: string) => {
    setCanvasTarget({ templateId, templateName, version })
    setPage('canvas')
  }, [])

  const openExpertEditor = useCallback((expertId?: string | null) => {
    setExpertEditorTarget(expertId ?? null)
    setPage('expert-editor')
    window.scrollTo({ top: 0 })
  }, [])

  const value = useMemo(() => ({
    authed, role, page, dialog, taskCounter, orgUsers, currentUser, locateNode, checkProblems, canvasTarget, approvalTargetId, activeWiId, activeTaskId, expertEditorTarget,
    navigate, openWorkItem, openTask, login, logout, openDialog, closeDialog, openApproval, bumpTask,
    updateUser, assignUserRoles, refreshOrgUsers, locateCanvasNode, setCheckProblems, openCanvas, openExpertEditor,
  }), [authed, role, page, dialog, taskCounter, orgUsers, currentUser, locateNode, checkProblems, canvasTarget, approvalTargetId, activeWiId, activeTaskId, expertEditorTarget, navigate, openWorkItem, openTask, login, logout, openDialog, closeDialog, openApproval, bumpTask, updateUser, assignUserRoles, refreshOrgUsers, locateCanvasNode, setCheckProblems, openCanvas, openExpertEditor])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}

/* 角色 → 可见导航（PRD §5 功能权限，原型 ROLE_NAV） */
export const ROLE_NAV: Record<RoleKey, PageId[]> = {
  leader: ['os-overview', 'aichat', 'tasks', 'notif', 'dashboard', 'projects', 'workitems', 'repos', 'templates', 'expert-center', 'skill-center', 'mcp-center', 'provider-center', 'knowledge', 'memory', 'runtime-center', 'approvals', 'docs', 'channel', 'external-tools'],
  org: ['os-overview', 'aichat', 'tasks', 'notif', 'dashboard', 'projects', 'workitems', 'repos', 'templates', 'expert-center', 'skill-center', 'mcp-center', 'provider-center', 'knowledge', 'memory', 'runtime-center', 'approvals', 'docs', 'org', 'channel', 'matrix', 'audit', 'external-tools'],
  dev: ['os-overview', 'aichat', 'tasks', 'notif', 'projects', 'workitems', 'repos', 'templates', 'expert-center', 'skill-center', 'mcp-center', 'provider-center', 'knowledge', 'memory', 'runtime-center', 'approvals', 'docs', 'external-tools'],
  sales: ['os-overview', 'aichat', 'tasks', 'notif', 'projects', 'workitems', 'templates', 'expert-center', 'skill-center', 'mcp-center', 'provider-center', 'knowledge', 'memory', 'runtime-center', 'approvals', 'docs', 'external-tools'],
}

export { toast }
