import { useCallback, useEffect, useMemo, useState } from 'react'
import { Building2, RefreshCw, UserPlus, ShieldAlert, Lock, CheckCircle2 } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Avatar, Badge, KpiCard, PageHeader, SectionCard, type Tone } from '../components/common'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { cn } from '../lib/utils'
import type { User } from '../types'

const statusMap: Record<string, [Tone, string]> = {
  active: ['suc', '正常'], disabled: ['err', '已停用'], locked: ['warn', '已锁定'], invited: ['info', '待激活'],
}

/* 角色 / 技能候选（PRD §5.1：内置全局角色 + 技能标签） */
const ROLE_OPTIONS = ['system_admin', 'organization_admin', 'project_admin', 'leader', 'product_manager', 'developer', 'after_sales', 'pre_sales', 'second_line']
const SKILL_OPTIONS = ['backend', 'frontend', 'qa', 'devops']
const roleName: Record<string, string> = {
  system_admin: '系统管理员', organization_admin: '组织管理员', project_admin: '项目管理员',
  leader: '领导', product_manager: '产品经理', developer: '开发', after_sales: '售后',
  pre_sales: '售前', second_line: '二线',
}

export function OrgPage() {
  const { openDialog, openApproval, dialog, orgUsers: users, updateUser, refreshOrgUsers } = useApp()
  const [tab, setTab] = useState<'users' | 'approve' | 'sync'>('users')
  const [editing, setEditing] = useState<User | null>(null)
  /* 概览/部门树/待审批：来自后端聚合接口 */
  const [overview, setOverview] = useState({ departments: 0, users: 0, syncedDing: 0, syncedWecom: 0, pendingApprove: 0 })
  /* 部门树：由用户部门聚合（数据来自后端 /org/users） */
  const deptTree = useMemo(() => {
    const m: Record<string, number> = {}
    users.forEach((u) => { const d = u.dept.split(' / ')[0] || '未分配'; m[d] = (m[d] ?? 0) + 1 })
    return Object.entries(m).map(([name, cnt]) => ({ name, users: cnt, sync: '钉钉 ✓ 企微 ✓' }))
  }, [users])
  const [approvals, setApprovals] = useState<{ id: string; name: string; email: string; dept: string; role: string; applied: string }[]>([])

  /* 拉取待审批列表（注册审批弹框关闭后自动刷新，保证列表与审批结果一致） */
  const loadApprovals = useCallback(() => {
    api.get<{ items: { id: string; name: string; email: string; dept: string; role: string }[] }>('/api/v1/auth/approvals')
      .then((d) => setApprovals(d.items.map((r) => ({ ...r, applied: '待审批' }))))
      .catch(() => {})
  }, [])

  /* 挂载：概览 + 部门树（由用户部门聚合）+ 待审批 */
  useEffect(() => {
    api.get<{ departments: number; users: number; active: number; pending_approve: number }>('/api/v1/org/overview')
      .then((d) => setOverview({ departments: d.departments, users: d.users, syncedDing: d.active, syncedWecom: d.active, pendingApprove: d.pending_approve ?? 0 }))
      .catch(() => {})
    loadApprovals()
  }, [users.length, loadApprovals])

  /* 审批弹框关闭后刷新待审批列表 */
  useEffect(() => {
    if (!dialog) loadApprovals()
  }, [dialog, loadApprovals])

  const saveUser = async (updated: User) => {
    try {
      await api.patch(`/api/v1/org/users/${updated.id}`, {
        dept: updated.dept, email: updated.email, status: updated.status, roles: updated.roles, skills: updated.skills,
      })
      updateUser(updated)
      setEditing(null)
      toast.success(`已保存用户「${updated.name}」的配置（变更已审计）`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存用户失败')
    }
  }

  const disableUser = async (u: User) => {
    if (!window.confirm(`停用「${u.name}」？停用后不可登录、不参与任务分配（已有任务须转办）。`)) return
    try {
      await api.patch(`/api/v1/org/users/${u.id}`, { status: 'disabled' })
      updateUser({ ...u, status: 'disabled' })
      toast.success(`已停用「${u.name}」`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '停用失败')
    }
  }

  const enableUser = async (u: User) => {
    try {
      await api.patch(`/api/v1/org/users/${u.id}`, { status: 'active' })
      updateUser({ ...u, status: 'active' })
      toast.success(`已启用「${u.name}」`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '启用失败')
    }
  }

  const deleteUser = async (u: User) => {
    if (!window.confirm(`删除「${u.name}（${u.account}）」？用户将立即不可登录并从列表移除（软删，审计记录保留）。`)) return
    try {
      await api.del(`/api/v1/org/users/${u.id}`)
      refreshOrgUsers()
      toast.success(`已删除用户「${u.name}」`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const unlockUser = async (u: User) => {
    try {
      await api.post(`/api/v1/org/users/${u.id}/unlock`)
      updateUser({ ...u, status: 'active' })
      toast.success(`已解锁 ${u.name}：解锁人已记录审计`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '解锁失败')
    }
  }

  const syncOrg = () => {
    api.post('/api/v1/org/sync')
      .then(() => toast('立即同步：调用钉钉与企业微信组织接口（后端已记录审计）'))
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '同步失败'))
    setTab('sync')
  }

  return (
    <div className="page-container">
      <PageHeader
        title="组织管理"
        sub="钉钉 / 企业微信组织同步 · 同步失败保留已有数据，不删除不破坏（PRD §3.2）"
        actions={
          <>
            <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              onClick={syncOrg}>
              <RefreshCw className="h-4 w-4" />立即同步
            </button>
            <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={() => openDialog('createUser')}>
              <UserPlus className="h-4 w-4" />创建本地用户
            </button>
          </>
        }
      />

      <div className="mb-5 grid grid-cols-2 gap-4 xl:grid-cols-5">
        <KpiCard label="部门数" value={overview.departments} icon={<Building2 className="h-4 w-4" />} tone="blue" />
        <KpiCard label="用户总数" value={overview.users} icon={<Building2 className="h-4 w-4" />} tone="green" />
        <KpiCard label="钉钉已同步" value={overview.syncedDing} icon={<Building2 className="h-4 w-4" />} tone="blue" />
        <KpiCard label="企微已同步" value={overview.syncedWecom} icon={<Building2 className="h-4 w-4" />} tone="violet" />
        <KpiCard label="注册待审批" value={overview.pendingApprove} icon={<Building2 className="h-4 w-4" />} tone="amber" />
      </div>

      <div className="mb-4 flex items-center gap-2 border-b border-slate-200 dark:border-slate-700">
        {([
          ['users', '用户管理'], ['approve', '注册审批'], ['sync', '同步状态'],
        ] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('-mb-px border-b-2 px-4 py-2.5 text-[13.5px] font-medium transition-colors',
              tab === k ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300')}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'users' && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700 dark:bg-slate-900">
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800/60">
                  <th className="px-4 py-3 font-medium">用户</th>
                  <th className="px-4 py-3 font-medium">部门</th>
                  <th className="px-4 py-3 font-medium">角色 / 技能</th>
                  <th className="px-4 py-3 font-medium">外部身份</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium">负载</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const [tone, label] = statusMap[u.status]
                  return (
                    <tr key={u.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2.5">
                          <Avatar name={u.name} grad={u.avatarGrad} size={28} />
                          <div>
                            <div className="font-medium text-slate-700 dark:text-slate-200">{u.name}</div>
                            <div className="font-mono text-[11px] text-slate-400">{u.account}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{u.dept}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {u.roles.map((r) => <Badge key={r} tone="info" className="!px-1.5 !text-[10.5px]">{roleName[r] ?? r}</Badge>)}
                          {u.skills.map((s) => <Badge key={s} tone="cyn" className="!px-1.5 !text-[10.5px]">{s}</Badge>)}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-[11.5px]">
                        <div className="flex flex-col gap-0.5">
                          <span className={u.dingTalk ? 'text-slate-500 dark:text-slate-400' : 'text-slate-300'}>钉钉 {u.dingTalk ? '✓ 已绑定' : '—'}</span>
                          <span className={u.wecom ? 'text-slate-500 dark:text-slate-400' : 'text-slate-300'}>企微 {u.wecom ? '✓ 已绑定' : '—'}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3"><Badge tone={tone} dot>{label}</Badge></td>
                      <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{u.status === 'active' ? `${u.load} 项` : '—'}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          {u.status === 'disabled' ? (
                            <>
                              <button className="rounded-md border border-amber-300 px-2.5 py-1 text-[11.5px] font-medium text-amber-600 hover:bg-amber-50 dark:border-amber-500/40 dark:text-amber-400" onClick={() => openDialog('userTakeover')}>任务接管</button>
                              <button className="rounded-md border border-emerald-300 px-2.5 py-1 text-[11.5px] font-medium text-emerald-600 hover:bg-emerald-50 dark:border-emerald-500/40 dark:text-emerald-400" onClick={() => enableUser(u)}>启用</button>
                            </>
                          ) : u.status === 'locked' ? (
                            <button className="rounded-md border border-slate-300 px-2.5 py-1 text-[11.5px] font-medium text-slate-500 hover:border-amber-400 dark:border-slate-700" onClick={() => unlockUser(u)}>解锁</button>
                          ) : (
                            <button className="rounded-md border border-slate-300 px-2.5 py-1 text-[11.5px] font-medium text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-400" onClick={() => setEditing(u)}>编辑</button>
                          )}
                          {u.status !== 'disabled' && u.status !== 'locked' && (
                            <button className="rounded-md border border-slate-300 px-2.5 py-1 text-[11.5px] font-medium text-slate-500 hover:border-amber-400 hover:text-amber-600 dark:border-slate-700 dark:text-slate-400" onClick={() => disableUser(u)}>停用</button>
                          )}
                          <button className="rounded-md border border-red-200 px-2.5 py-1 text-[11.5px] font-medium text-red-500 hover:bg-red-50 dark:border-red-500/40 dark:hover:bg-red-500/10" onClick={() => deleteUser(u)}>删除</button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-[12px] text-slate-400 dark:border-slate-800">
            <span>共 {users.length} 人 · 外部平台停用用户置为 disabled，已有任务必须转办或管理员接管（PRD §3.2）</span>
          </div>
        </div>
      )}

      {tab === 'approve' && (
        <SectionCard title={<span className="flex items-center gap-2">注册审批 <Badge tone="warn">{approvals.length} 待处理</Badge></span>}
          extra={<button className="rounded-lg border border-blue-300 px-3 py-1.5 text-[12.5px] font-medium text-blue-600 hover:bg-blue-50 dark:border-blue-500/40 dark:text-blue-300 dark:hover:bg-blue-500/10" onClick={() => openApproval()}>批量审批</button>}>
          <div className="space-y-2.5">
            {approvals.map((r) => (
              <div key={r.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-3.5 dark:border-slate-700">
                <Avatar name={r.name} grad="g2" size={34} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13.5px] font-medium text-slate-700 dark:text-slate-200">{r.name} <span className="font-mono text-[11px] text-slate-400">{r.email}</span></div>
                  <div className="mt-0.5 text-[12px] text-slate-400">{r.dept} · <Badge tone="info" className="!px-1.5 !text-[10.5px]">{r.role}</Badge> · 申请于 {r.applied}</div>
                </div>
                <button className="rounded-lg bg-blue-600 px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-blue-700" onClick={() => openApproval(r.id)}>通过</button>
                <button className="rounded-lg border border-slate-300 px-3 py-1.5 text-[12.5px] font-medium text-slate-500 hover:border-red-300 hover:text-red-500 dark:border-slate-700 dark:text-slate-400" onClick={() => toast('拒绝需填写原因，并通知申请者（R-152）')}>拒绝</button>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {tab === 'sync' && (
        <div className="space-y-4">
          <div className="flex items-start gap-2.5 rounded-lg border border-blue-200 bg-blue-50 p-3.5 text-[12px] leading-relaxed text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300">
            <ShieldAlert className="mt-0.5 h-4 w-4 flex-none" />
            <span>最近同步：钉钉 08-17 09:58:07 · 变更 3 / 停用 1 · 失败保留已有本地数据；身份冲突不得静默覆盖，需管理员手工处理（PRD §3.2）。</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {deptTree.map((d) => (
              <div key={d.name} className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-s dark:border-slate-700 dark:bg-slate-900">
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-slate-500 to-slate-700 text-white"><Building2 className="h-4 w-4" /></span>
                  <div>
                    <div className="text-[13.5px] font-medium text-slate-700 dark:text-slate-200">{d.name}</div>
                    <div className="text-[11.5px] text-slate-400">{d.users} 人</div>
                  </div>
                </div>
                <div className="text-[11.5px] text-slate-400">
                  {d.sync.split(' ').map((s) => s.includes('✓') ? (
                    <span key={s} className="mr-1.5 inline-flex items-center gap-0.5 text-emerald-600"><CheckCircle2 className="h-3 w-3" />{s.replace('✓', '')}</span>
                  ) : (
                    <span key={s} className="mr-1.5 inline-flex items-center gap-0.5 text-red-500"><Lock className="h-3 w-3" />{s.replace('✗', '')}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 用户编辑：分配角色 / 技能 */}
      {editing && (
        <EditUserDialog
          user={editing}
          onSave={saveUser}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

/* ============ 编辑用户：基础信息（部门/邮箱回显可改）+ 角色 / 技能 + 停用 ============ */
function EditUserDialog({ user, onSave, onClose }: {
  user: User
  onSave: (u: User) => void
  onClose: () => void
}) {
  const { orgUsers } = useApp()
  const [roles, setRoles] = useState<string[]>(user.roles)
  const [skills, setSkills] = useState<string[]>(user.skills)
  const [dept, setDept] = useState(user.dept)
  const [email, setEmail] = useState(user.email ?? '')
  const [status, setStatus] = useState<User['status']>(user.status)

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  const save = () => {
    onSave({ ...user, roles, skills, dept, email, status })
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">编辑用户 · {user.name}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {/* 用户信息 */}
          <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
            <Avatar name={user.name} grad={user.avatarGrad} size={38} />
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-medium text-slate-700 dark:text-slate-200">{user.name}</div>
              <div className="font-mono text-[11px] text-slate-400">{user.account} · 钉钉 {user.dingTalk ? '✓' : '—'} · 企微 {user.wecom ? '✓' : '—'}</div>
            </div>
            <Badge tone={statusMap[status][0]} dot>{statusMap[status][1]}</Badge>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">部门</label>
              <input className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                value={dept} onChange={(e) => setDept(e.target.value)} placeholder="如 平台研发部 / 平台组" />
              <p className="text-[10.5px] text-slate-400">当前：{user.dept || '（空）'}</p>
            </div>
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">邮箱</label>
              <input className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
              <p className="text-[10.5px] text-slate-400">通知与找回凭证联系方式</p>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">账号状态</label>
            <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={status} onChange={(e) => setStatus(e.target.value as User['status'])}>
              <option value="active">正常（active）</option><option value="disabled">停用（disabled）</option>
              <option value="locked">锁定（locked）</option><option value="invited">待激活（invited）</option>
            </select>
          </div>

          {/* 角色多选 */}
          <div className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-300">
              角色 <span className="text-[10.5px] font-normal text-slate-400">可多选 · 节点流转按角色解析处理人</span>
            </label>
            <div className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-700">
              <div className="flex flex-wrap gap-1.5">
                {ROLE_OPTIONS.map((r) => (
                  <button key={r} onClick={() => toggle(roles, setRoles, r)}
                    className={cn('whitespace-nowrap rounded-full px-2.5 py-1 text-[11.5px] font-medium transition-colors',
                      roles.includes(r)
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-50 text-slate-500 hover:bg-blue-50 hover:text-blue-600 dark:bg-slate-800 dark:text-slate-400')}>
                    {roleName[r]}
                    <span className={cn('ml-1 font-mono text-[9.5px]', roles.includes(r) ? 'text-blue-200' : 'text-slate-300 dark:text-slate-600')}>{r}</span>
                  </button>
                ))}
              </div>
              {roles.length > 0 && (
                <p className="mt-2 border-t border-slate-100 pt-2 text-[10.5px] leading-relaxed text-slate-400 dark:border-slate-800">
                  角色解析：{roles.map((r) => `${roleName[r]} → ${orgUsers.filter((u) => u.status === 'active' && u.roles.includes(r)).map((u) => u.name).join('、') || '暂无'}`).join('；')}
                </p>
              )}
            </div>
          </div>

          {/* 技能多选 */}
          <div className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-300">
              技能标签 <span className="text-[10.5px] font-normal text-slate-400">可多选 · 节点技能要求匹配</span>
            </label>
            <div className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-700">
              <div className="flex flex-wrap gap-1.5">
                {SKILL_OPTIONS.map((s) => (
                  <button key={s} onClick={() => toggle(skills, setSkills, s)}
                    className={cn('rounded-full px-2.5 py-1 text-[11.5px] font-medium transition-colors',
                      skills.includes(s)
                        ? 'bg-violet-600 text-white'
                        : 'bg-slate-50 text-slate-500 hover:bg-violet-50 hover:text-violet-600 dark:bg-slate-800 dark:text-slate-400')}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <p className="rounded-lg bg-slate-50 p-2.5 text-[11.5px] leading-relaxed text-slate-400 dark:bg-slate-800/60">
            用户可同时拥有多个角色与技能（PRD §5.1，如 孙琳 = developer + qa）。节点流转时按「项目×模板」节点绑定的角色/技能反查所在在职用户自动分配任务（PRD §4.5）。
          </p>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={save}>保存角色关联</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
