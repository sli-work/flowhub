import { useEffect, useMemo, useState } from 'react'
import {
  ShieldCheck, RotateCcw, Users, Lock, Settings2, Plus, Trash2,
  FolderKanban, Workflow, GitBranch, Inbox, FileText, Bot, ScrollText, BarChart3, Shield,
} from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import {
  Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle,
} from '../components/ui/sheet'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog'
import { Switch } from '../components/ui/switch'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { PageHeader, Badge, Avatar, type Tone } from '../components/common'
import { cn } from '../lib/utils'

/* ============ 角色元数据 ============ */
const roleMeta: Record<string, { label: string; desc: string; grad: string; tone: Tone }> = {
  system_admin: { label: '系统管理员', desc: '全平台最高权限，管理组织与系统配置；防自锁保护', grad: 'g1', tone: 'info' },
  organization_admin: { label: '组织管理员', desc: '组织用户 / 角色 / 同步与审计管理', grad: 'g6', tone: 'cyn' },
  project_admin: { label: '项目管理员', desc: '项目管理、成员管理与模板发布', grad: 'g4', tone: 'pur' },
  leader: { label: '领导', desc: '组织级看板与全局数据范围', grad: 'g2', tone: 'warn' },
  product_manager: { label: '产品经理', desc: '需求流程主导与验收', grad: 'g3', tone: 'suc' },
  developer: { label: '开发', desc: '研发节点处理（技能标签细分 backend/frontend/qa）', grad: 'g5', tone: 'info' },
  after_sales: { label: '售后', desc: '问题提报、分诊与闭环确认', grad: 'g1', tone: 'warn' },
  pre_sales: { label: '售前', desc: '方案导入与需求前置', grad: 'g2', tone: 'suc' },
  second_line: { label: '二线', desc: '问题深度排查与根因分析', grad: 'g4', tone: 'pur' },
}

/* ============ 权限中文名 ============ */
const permLabel: Record<string, string> = {
  'organization:user_manage': '用户管理', 'organization:role_manage': '角色管理',
  'project:create': '创建项目', 'project:read': '查看项目', 'project:update': '编辑项目',
  'project:archive': '归档项目', 'project:member_manage': '成员管理',
  'workflow_template:create': '创建模板', 'workflow_template:read': '查看模板', 'workflow_template:update': '编辑模板',
  'workflow_template:review': '评审模板', 'workflow_template:publish': '发布模板', 'workflow_template:archive': '归档模板',
  'workflow_instance:create': '发起流程', 'workflow_instance:read': '查看实例',
  'workflow_instance:pause': '暂停流程', 'workflow_instance:cancel': '取消流程',
  'task:claim': '认领任务', 'task:submit': '提交任务', 'task:return': '退回任务', 'task:transfer': '转办任务',
  'document:upload': '上传文档', 'document:read': '查看文档', 'document:download': '下载文档',
  'document:delete': '删除文档', 'document:restore': '恢复文档',
  'agent:register': '创建 Agent', 'agent:bind': '绑定 Agent', 'agent:invoke': '调用 Agent',
  'agent:authorize': '授权 Agent', 'agent:revoke': '吊销 Agent',
  'audit:read': '查看审计', 'audit:export': '导出审计',
  'dashboard:read': '查看看板',
}

/* ============ 权限分类（抽屉内分组展示） ============ */
const PERM_GROUPS: { label: string; icon: React.ReactNode; match: (p: string) => boolean }[] = [
  { label: '组织权限', icon: <Users className="h-3.5 w-3.5" />, match: (p) => p.startsWith('organization:') },
  { label: '项目权限', icon: <FolderKanban className="h-3.5 w-3.5" />, match: (p) => p.startsWith('project:') },
  { label: '流程模板', icon: <Workflow className="h-3.5 w-3.5" />, match: (p) => p.startsWith('workflow_template:') },
  { label: '流程实例', icon: <GitBranch className="h-3.5 w-3.5" />, match: (p) => p.startsWith('workflow_instance:') },
  { label: '任务处理', icon: <Inbox className="h-3.5 w-3.5" />, match: (p) => p.startsWith('task:') },
  { label: '文档管理', icon: <FileText className="h-3.5 w-3.5" />, match: (p) => p.startsWith('document:') },
  { label: 'Agent 管理', icon: <Bot className="h-3.5 w-3.5" />, match: (p) => p.startsWith('agent:') },
  { label: '审计', icon: <ScrollText className="h-3.5 w-3.5" />, match: (p) => p.startsWith('audit:') },
  { label: '领导看板', icon: <BarChart3 className="h-3.5 w-3.5" />, match: (p) => p.startsWith('dashboard:') },
]

/* ============ 角色权限状态 ============ */
interface RolePermState {
  id: string
  label: string
  desc: string
  builtin: boolean
  perms: Record<string, boolean>
}

const BUILTIN_IDS = new Set(['system_admin', 'organization_admin', 'project_admin', 'leader', 'product_manager', 'developer', 'after_sales', 'pre_sales', 'second_line'])

function buildRoles(roleOrder: string[], permMatrix: { perm: string; cells: boolean[] }[]): RolePermState[] {
  return roleOrder.map((rid, ci) => ({
    id: rid,
    label: roleMeta[rid]?.label ?? rid,
    desc: roleMeta[rid]?.desc ?? '',
    builtin: BUILTIN_IDS.has(rid),
    perms: Object.fromEntries(permMatrix.map((r) => [r.perm, r.cells[ci]])),
  }))
}

const grantedCount = (perms: Record<string, boolean>) => Object.values(perms).filter(Boolean).length

export function PermMatrixPage() {
  const { orgUsers, assignUserRoles } = useApp()
  /* 权限矩阵：来自后端 GET /matrix/roles（items + totalPerms + roleOrder + permMatrix） */
  const [roleOrder, setRoleOrder] = useState<string[]>([])
  const [permMatrix, setPermMatrix] = useState<{ perm: string; cells: boolean[] }[]>([])
  const [roles, setRoles] = useState<RolePermState[]>([])
  const [active, setActive] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [delTarget, setDelTarget] = useState<string | null>(null)
  const [memberPicker, setMemberPicker] = useState(false)
  const [saving, setSaving] = useState(false)
  /* 角色 → 成员用户：派生自全局用户表（与组织管理「用户编辑」双向实时同步，单一事实源在 app-store） */
  const roleMembers = useMemo(() => {
    const m: Record<string, string[]> = {}
    roleOrder.forEach((r) => { m[r] = orgUsers.filter((u) => u.roles.includes(r)).map((u) => u.id) })
    return m
  }, [orgUsers, roleOrder])

  /* 挂载时从后端加载角色（对齐后端 GET /matrix/roles 返回 {id,label,desc,builtin,perms}） */
  useEffect(() => {
    api.get<{ items: { id: string; label: string; desc: string; builtin: boolean; perms: Record<string, boolean> }[]; totalPerms: number; roleOrder: string[]; permMatrix: { perm: string; cells: boolean[] }[] }>('/api/v1/matrix/roles')
      .then((d) => {
        if (d.items.length) {
          setRoles(d.items.map((r) => ({ id: r.id, label: r.label, desc: r.desc, builtin: r.builtin, perms: r.perms })))
        }
        if (d.roleOrder?.length) setRoleOrder(d.roleOrder)
        if (d.permMatrix?.length) setPermMatrix(d.permMatrix)
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '权限矩阵加载失败'))
  }, [])

  useEffect(() => { setMemberPicker(false) }, [active])

  const current = roles.find((r) => r.id === active)
  const totalPerms = permMatrix.length || 34

  const memberNames = (roleId: string) =>
    (roleMembers[roleId] ?? []).map((uid) => orgUsers.find((u) => u.id === uid)?.name ?? uid)

  const toggleMember = async (roleId: string, uid: string) => {
    const u = orgUsers.find((x) => x.id === uid)
    if (!u) return
    const has = u.roles.includes(roleId)
    assignUserRoles(uid, has ? u.roles.filter((r) => r !== roleId) : [...u.roles, roleId])
    setDirty(true)
    try {
      // 后端角色成员同步（对齐 POST /matrix/roles/{id}/members）
      const next = [...(roleMembers[roleId] ?? [])]
      const idx = next.indexOf(uid)
      if (idx >= 0) next.splice(idx, 1)
      else next.push(uid)
      await api.post(`/api/v1/matrix/roles/${roleId}/members`, { user_ids: next })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '成员同步失败')
    }
  }

  const togglePerm = (roleId: string, perm: string) => {
    if (roleId === 'system_admin' && perm === 'organization:user_manage') {
      toast('系统管理员不可被降权（避免权限自锁）', { icon: <ShieldCheck className="h-4 w-4 text-amber-500" /> })
      return
    }
    setRoles((prev) => prev.map((r) => r.id === roleId
      ? { ...r, perms: { ...r.perms, [perm]: !r.perms[perm] } }
      : r))
    setDirty(true)
  }

  const addRole = async (label: string, id: string, desc: string, copyFrom: string) => {
    const source = roles.find((r) => r.id === copyFrom)
    const perms = source ? { ...source.perms } : Object.fromEntries((permMatrix.length ? permMatrix : []).map((r) => [r.perm, false]))
    try {
      await api.post('/api/v1/matrix/roles', { id, label, desc, copy_from: copyFrom || null, perms })
      setRoles((prev) => [...prev, { id, label, desc: desc || '自定义角色', builtin: false, perms }])
      setAddOpen(false)
      toast.success(`已创建自定义角色「${label}」（${id}）：${source ? `权限复制自「${source.label}」` : '初始权限为空白'}，可点击配置权限`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建角色失败')
    }
  }

  const deleteRole = async (id: string) => {
    const r = roles.find((x) => x.id === id)!
    try {
      await api.del(`/api/v1/matrix/roles/${id}`)
      setRoles((prev) => prev.filter((x) => x.id !== id))
      setDelTarget(null)
      if (active === id) setActive(null)
      toast.success(`已删除角色「${r.label}」${r.builtin ? '（内置）' : '（自定义）'}：引用该角色的用户将失去对应权限，节点绑定失效，历史审计保留`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除角色失败')
    }
  }

  const reset = () => { setRoles(buildRoles(roleOrder, permMatrix)); setDirty(false); toast('已恢复初始权限配置（未保存变更已丢弃）') }
  const save = async () => {
    setSaving(true)
    try {
      // 逐角色同步权限到后端（PATCH /matrix/roles/{id}）
      for (const r of roles) {
        await api.patch(`/api/v1/matrix/roles/${r.id}`, { id: r.id, label: r.label, desc: r.desc, perms: r.perms })
      }
      setDirty(false)
      toast.success(`权限配置已保存并审计：共 ${roles.length} 个角色 / ${totalPerms} 个权限点（含变更 before/after）`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page-container">
      <PageHeader
        title="权限矩阵"
        sub="角色以列表展示 · 点击行配置权限 / 分配用户（右侧抽屉）· 所有角色可删除 · 系统管理员「用户管理」防自锁"
        actions={
          <>
            <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={reset}>
              <RotateCcw className="h-4 w-4" />恢复
            </button>
            <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4" />添加角色
            </button>
            <button className={cn('rounded-lg px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all',
              dirty ? 'bg-blue-600 hover:bg-blue-700' : 'cursor-not-allowed bg-slate-300 dark:bg-slate-700')}
              disabled={!dirty || saving} onClick={save}>
              {saving ? '保存中…' : dirty ? '保存变更' : '已保存'}
            </button>
          </>
        }
      />

      <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-3 text-[12px] leading-relaxed text-slate-500 shadow-s dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-blue-500" />
        <span>
          所有角色（含内置）均可删除，删除需二次确认；管理员可<b className="text-slate-600 dark:text-slate-300">添加自定义角色</b>（支持复制现有角色权限）。
          每个角色可<b className="text-slate-600 dark:text-slate-300">分配用户成员</b>（与用户维度角色关联一致）；权限按 9 分类在右侧抽屉分组调整；系统管理员的「用户管理」受防自锁保护。
        </span>
      </div>

      {/* 角色列表（列表行） */}
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="hidden border-b border-slate-100 bg-slate-50 px-5 py-2.5 text-[11.5px] font-medium text-slate-400 md:grid dark:border-slate-800 dark:bg-slate-800/60"
          style={{ gridTemplateColumns: 'minmax(260px,1.4fr) minmax(180px,1fr) 220px 96px 130px' }}>
          <span>角色</span><span>职责</span><span>权限授权</span><span>类型</span><span className="text-right">操作</span>
        </div>
        <div className="divide-y divide-slate-100 dark:divide-slate-800">
          {roles.map((r) => {
            const meta = roleMeta[r.id]
            const granted = grantedCount(r.perms)
            const pct = Math.round((granted / totalPerms) * 100)
            return (
              <div key={r.id}
                className="group grid cursor-pointer items-center gap-x-4 gap-y-2 px-5 py-3.5 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40"
                style={{ gridTemplateColumns: 'minmax(260px,1.4fr) minmax(180px,1fr) 220px 96px 130px' }}
                onClick={() => setActive(r.id)}>
                {/* 角色 */}
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={r.id.slice(0, 1).toUpperCase()} grad={meta?.grad ?? 'g3'} size={34} rounded />
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="whitespace-nowrap text-[13.5px] font-semibold text-slate-800 dark:text-slate-100">{r.label}</span>
                      <span className="truncate font-mono text-[10.5px] text-slate-400">{r.id}</span>
                    </div>
                    <p className="mt-0.5 truncate text-[11.5px] text-slate-400">{r.desc}</p>
                  </div>
                </div>
                {/* 职责 */}
                <div className="hidden min-w-0 md:block">
                  <div className="flex flex-wrap gap-1">
                    {PERM_GROUPS.filter((g) => permMatrix.some((row) => g.match(row.perm) && r.perms[row.perm]))
                      .slice(0, 4).map((g) => (
                        <Badge key={g.label} tone="gry" className="!px-1.5 !text-[10px]">{g.label}</Badge>
                      ))}
                    {PERM_GROUPS.filter((g) => permMatrix.some((row) => g.match(row.perm) && r.perms[row.perm])).length === 0 && (
                      <span className="text-[11px] text-slate-300 dark:text-slate-600">无已授权权限</span>
                    )}
                  </div>
                </div>
                {/* 授权统计 */}
                <div className="min-w-0">
                  <div className="mb-1 flex items-center justify-between text-[11px]">
                    <span className="text-slate-400">已授权</span>
                    <span className="font-medium text-slate-600 dark:text-slate-300">{granted}/{totalPerms}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-600" style={{ width: `${pct}%` }} />
                  </div>
                </div>
                {/* 类型 + 成员 */}
                <div className="space-y-1">
                  {r.builtin
                    ? <Badge tone="info"><Shield className="h-3 w-3" />内置</Badge>
                    : <Badge tone="cyn">自定义</Badge>}
                  <div>
                    <button className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-500/15 dark:text-emerald-400"
                      onClick={(e) => { e.stopPropagation(); setActive(r.id) }} title="分配用户">
                      <Users className="h-2.5 w-2.5" />{(roleMembers[r.id] ?? []).length} 成员
                    </button>
                  </div>
                </div>
                {/* 操作 */}
                <div className="flex items-center justify-end gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="flex items-center gap-1 rounded-md px-2 py-1.5 text-[12px] font-medium text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-500/10"
                    onClick={() => setActive(r.id)}>
                    <Settings2 className="h-3.5 w-3.5" />配置
                  </button>
                  <button
                    className="flex items-center gap-1 rounded-md px-2 py-1.5 text-[12px] font-medium text-slate-400 transition-colors hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-500/10"
                    onClick={() => setDelTarget(r.id)}
                    title={r.builtin ? '删除内置角色（需二次确认）' : '删除角色'}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )
          })}
        </div>
        <div className="border-t border-slate-100 px-5 py-3 text-[12px] text-slate-400 dark:border-slate-800">
          <span>共 {roles.length} 个角色 · 内置 {roles.filter((r) => r.builtin).length} 个 · 自定义 {roles.filter((r) => !r.builtin).length} 个</span>
        </div>
      </div>

      {/* 右侧权限配置抽屉 */}
      <Sheet open={!!active} onOpenChange={(o) => { if (!o) setActive(null) }}>
        <SheetContent side="right" className="flex w-full max-w-[440px] flex-col gap-0 p-0">
          {current && (() => {
            const meta = roleMeta[current.id]
            const granted = grantedCount(current.perms)
            return (
              <>
                <SheetHeader className="border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                  <div className="flex items-center gap-3">
                    <Avatar name={current.id.slice(0, 1).toUpperCase()} grad={meta?.grad ?? 'g3'} size={40} rounded />
                    <div className="min-w-0 flex-1">
                      <SheetTitle className="flex min-w-0 items-center gap-2 text-[15px]">
                        <span className="whitespace-nowrap">{current.label}</span>
                        <span className="truncate font-mono text-[10.5px] font-normal text-slate-400">{current.id}</span>
                        {current.builtin
                          ? <Badge tone="info" className="flex-none"><Shield className="h-3 w-3" />内置</Badge>
                          : <Badge tone="cyn" className="flex-none">自定义</Badge>}
                      </SheetTitle>
                      <p className="mt-0.5 truncate text-[12px] text-slate-400">{current.desc}</p>
                    </div>
                    <Badge tone="info" className="flex-none">{granted}/{totalPerms} 已授权</Badge>
                  </div>
                </SheetHeader>

                <div className="flex-1 overflow-y-auto px-5 py-4">
                  <div className="space-y-4">
                    {/* 角色成员（用户） */}
                    <div>
                      <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-slate-500 dark:text-slate-400">
                        <Users className="h-3.5 w-3.5 text-emerald-500" />
                        角色成员（用户）
                        <span className="ml-auto rounded-full bg-emerald-50 px-1.5 text-[10px] font-medium text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400">
                          {(roleMembers[current.id] ?? []).length} 人
                        </span>
                      </div>
                      <div className="rounded-lg border border-slate-100 p-2.5 dark:border-slate-800">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {memberNames(current.id).map((n, i) => (
                            <span key={n + i} className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300">
                              <Avatar name={n} grad="g3" size={16} />{n}
                              <button onClick={() => toggleMember(current.id, orgUsers.find((u) => u.name === n)?.id ?? '')}
                                className="text-emerald-400 hover:text-red-500">×</button>
                            </span>
                          ))}
                          {(roleMembers[current.id] ?? []).length === 0 && (
                            <span className="text-[11px] text-slate-400">暂无成员 · 点击「分配用户」添加</span>
                          )}
                        </div>
                        <div className="mt-2 border-t border-slate-100 pt-2 dark:border-slate-800">
                          <button
                            className="mb-1.5 text-[11px] font-medium text-emerald-600 hover:underline"
                            onClick={() => setMemberPicker(!memberPicker)}>
                            {memberPicker ? '收起用户列表' : '+ 分配用户'}
                          </button>
                          {memberPicker && (
                            <div className="flex flex-wrap gap-1.5">
                              {orgUsers.filter((u) => u.status === 'active').map((u) => (
                                <button key={u.id}
                                  onClick={() => toggleMember(current.id, u.id)}
                                  className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors',
                                    (roleMembers[current.id] ?? []).includes(u.id)
                                      ? 'bg-emerald-600 text-white'
                                      : 'bg-slate-50 text-slate-500 hover:bg-emerald-50 hover:text-emerald-600 dark:bg-slate-800 dark:text-slate-400')}>
                                  {u.name}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>

                    {PERM_GROUPS.map((g) => {
                      const items = permMatrix.filter((r) => g.match(r.perm))
                      if (items.length === 0) return null
                      const onCount = items.filter((r) => current.perms[r.perm]).length
                      return (
                        <div key={g.label}>
                          <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-slate-500 dark:text-slate-400">
                            <span className="text-blue-500">{g.icon}</span>
                            {g.label}
                            <span className="ml-auto rounded-full bg-slate-100 px-1.5 text-[10px] font-medium text-slate-400 dark:bg-slate-800">
                              {onCount}/{items.length}
                            </span>
                          </div>
                          <div className="overflow-hidden rounded-lg border border-slate-100 dark:border-slate-800">
                            {items.map((row, idx) => {
                              const locked = current.id === 'system_admin' && row.perm === 'organization:user_manage'
                              const on = current.perms[row.perm]
                              return (
                                <div key={row.perm}
                                  className={cn('flex items-center justify-between gap-2 px-3 py-2.5 transition-colors',
                                    idx < items.length - 1 && 'border-b border-slate-50 dark:border-slate-800/60',
                                    locked && 'bg-amber-50/60 dark:bg-amber-500/5')}>
                                  <div className="min-w-0 flex-1">
                                    <div className="flex min-w-0 items-center gap-1.5 text-[12.5px] font-medium text-slate-700 dark:text-slate-200">
                                      <span className="whitespace-nowrap">{permLabel[row.perm] ?? row.perm}</span>
                                      {locked && (
                                        <span className="flex-none rounded bg-amber-100 px-1 py-0.5 text-[9.5px] font-semibold text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                                          <Lock className="mr-0.5 inline h-2.5 w-2.5" />防自锁
                                        </span>
                                      )}
                                    </div>
                                    <div className="truncate font-mono text-[10px] text-slate-400">{row.perm}</div>
                                  </div>
                                  <Switch
                                    className="flex-none"
                                    checked={on}
                                    disabled={locked}
                                    onCheckedChange={() => togglePerm(current.id, row.perm)}
                                    title={locked ? '系统管理员不可被降权（避免权限自锁）' : undefined}
                                  />
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <SheetFooter className="border-t border-slate-100 px-5 py-3.5 dark:border-slate-800">
                  <button className="rounded-lg border border-slate-300 px-4 py-2 text-[13px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300" onClick={() => setActive(null)}>
                    关闭
                  </button>
                  <button
                    className={cn('rounded-lg px-4 py-2 text-[13px] font-medium text-white transition-all',
                      dirty ? 'bg-blue-600 hover:bg-blue-700' : 'cursor-not-allowed bg-slate-300 dark:bg-slate-700')}
                    disabled={!dirty}
                    onClick={() => { save(); setActive(null) }}>
                    保存并关闭
                  </button>
                </SheetFooter>
              </>
            )
          })()}
        </SheetContent>
      </Sheet>

      {/* 添加角色弹窗 */}
      <AddRoleDialog open={addOpen} onClose={() => setAddOpen(false)} roles={roles} onAdd={addRole} />

      {/* 删除角色确认 */}
      <Dialog open={!!delTarget} onOpenChange={(o) => { if (!o) setDelTarget(null) }}>
        <DialogContent className="sm:max-w-[400px]">
          <DialogHeader>
            <DialogTitle className="text-[15px]">删除角色</DialogTitle>
          </DialogHeader>
          {delTarget && (() => {
            const r = roles.find((x) => x.id === delTarget)!
            return (
              <div className="space-y-3">
                <div className={cn('flex items-center gap-2.5 rounded-lg border p-3 text-[12.5px]',
                  r.builtin
                    ? 'border-red-300 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300'
                    : 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300')}>
                  <Trash2 className="h-4 w-4 flex-none" />
                  即将删除角色「<b>{r.label}</b>」（{r.id}）
                  {r.builtin && <Badge tone="err" className="flex-none">内置角色</Badge>}
                </div>
                <p className="text-[12px] leading-relaxed text-slate-400">
                  删除后：<b className="text-slate-600 dark:text-slate-300">引用该角色的 {memberNames(r.id).length} 位用户将失去对应权限（需重新分配）</b>；
                  项目节点绑定中引用该角色的解析失效（流转将找不到处理人）；
                  历史审计记录保留，不可恢复。
                </p>
                {r.builtin && (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[11.5px] leading-relaxed text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                    ⚠️ 该角色为<b>内置全局角色</b>（PRD §5.1），删除后系统不再提供此角色定义；请确认已为受影响用户分配替代角色。
                  </p>
                )}
              </div>
            )
          })()}
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setDelTarget(null)}>取消</Button>
            <Button variant="destructive" onClick={() => delTarget && deleteRole(delTarget)}>确认删除</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/* ============ 添加角色弹窗 ============ */
function AddRoleDialog({ open, onClose, roles, onAdd }: {
  open: boolean
  onClose: () => void
  roles: { id: string; label: string }[]
  onAdd: (label: string, id: string, desc: string, copyFrom: string) => void
}) {
  const [label, setLabel] = useState('')
  const [id, setId] = useState('')
  const [desc, setDesc] = useState('')
  const [copyFrom, setCopyFrom] = useState('')

  const slug = (s: string) => s.trim().toLowerCase().replace(/\s+/g, '_').replace(/[^\w\u4e00-\u9fa5-]/g, '')
  const idInvalid = !/^[a-z][a-z0-9_]*$/.test(id)
  const dup = roles.some((r) => r.id === id)
  const canCreate = label.trim().length > 0 && id.length > 0 && !idInvalid && !dup

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">添加自定义角色</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">角色名称 <span className="text-red-500">*</span></label>
            <Input value={label} onChange={(e) => { setLabel(e.target.value); if (!id) setId(slug(e.target.value)) }} placeholder="如：数据管理员" />
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">角色标识 <span className="text-red-500">*</span></label>
            <Input value={id} onChange={(e) => setId(slug(e.target.value))} placeholder="data_admin" className="font-mono" />
            <p className={cn('text-[11px]', idInvalid ? 'text-red-500' : dup ? 'text-red-500' : 'text-slate-400')}>
              {idInvalid ? '仅支持小写字母、数字与下划线，且以字母开头'
                : dup ? '该角色标识已存在'
                : '小写字母开头；不填时由名称自动生成'}
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">职责描述</label>
            <Input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="该角色的职责范围说明" />
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">初始权限</label>
            <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
              <option value="">空白（全部未授权）</option>
              {roles.map((r) => <option key={r.id} value={r.id}>复制自：{r.label}（{r.id}）</option>)}
            </select>
            <p className="text-[11px] text-slate-400">创建后可随时在右侧抽屉按分类调整权限。</p>
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={!canCreate} onClick={() => onAdd(label, id, desc, copyFrom)}>创建角色</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
