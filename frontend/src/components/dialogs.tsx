import { useEffect, useState } from 'react'
import { Bot } from 'lucide-react'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Textarea } from '../components/ui/textarea'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Avatar, Badge, RiskTip } from './common'
import { SchemaForm, validateSchema, type SchemaValues } from './schema-form'
import { cn } from '../lib/utils'
import type { NodeAssignment, Project, ProjectTemplateBinding } from '../types'

/* 节点可绑定的角色 / 技能候选（PRD §5.1：角色 + 技能标签） */
const ASSIGN_ROLES = ['developer', 'product_manager', 'qa', 'after_sales', 'pre_sales', 'second_line', 'backend', 'frontend', 'devops', 'project_admin']

/* 统一弹窗外壳 */
function Shell({ title, children, footer, wide }: {
  title: string; children: React.ReactNode; footer: React.ReactNode; wide?: boolean
}) {
  const { closeDialog } = useApp()
  return (
    <Dialog open onOpenChange={() => closeDialog()}>
      <DialogContent className={cn('max-h-[88vh] overflow-y-auto', wide ? 'sm:max-w-[560px]' : 'sm:max-w-[440px]')}>
        <DialogHeader>
          <DialogTitle className="text-[15px]">{title}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">{children}</div>
        <DialogFooter className="gap-2 sm:gap-2">{footer}</DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

const CancelBtn = ({ children = '取消' }: { children?: React.ReactNode }) => {
  const { closeDialog } = useApp()
  return <Button variant="outline" onClick={closeDialog}>{children}</Button>
}

/* ============ 1. 提交节点 ============ */
function SubmitDialog() {
  const { closeDialog, bumpTask } = useApp()
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('全部用例通过，测试报告已上传（v1.4）。')

  const submit = async () => {
    setBusy(true)
    try {
      // 取当前用户第一条任务提交（真实流转：提交 → 绑定解析 → 自动分配下一节点）
      const tasks = await api.get<{ items: { id: string }[] }>('/api/v1/tasks?page_size=1')
      const tid = tasks.items[0]?.id
      if (!tid) throw new ApiError(40401, '暂无待提交任务（请先新建工作项）', 404)
      const d = await api.post<{ next_node?: { label?: string } | null; next_assignees?: { name: string }[] }>(
        `/api/v1/tasks/${tid}/actions`, { action: 'submit', form_values: { note } },
      )
      closeDialog()
      bumpTask()
      const next = d.next_node?.label
      const names = (d.next_assignees ?? []).map((a) => a.name).join('、')
      toast.success(`提交成功：流程已推进至「${next ?? '下一节点'}」${names ? `，自动分配处理人：${names}` : ''}`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '提交失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="提交节点 · 测试 → 产品验收" footer={<>
      <CancelBtn />
      <Button disabled={busy} onClick={submit}>{busy ? '提交中…' : '确认提交'}</Button>
    </>}>
      <RiskTip>
        提交后流程将推进至「产品验收」并通知其处理人；本操作不可撤回，但可按模板回退目标回退。测试结论为「失败」时应选择退回而非提交。
      </RiskTip>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">测试结论</label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
          <option>通过（38/38 用例通过）</option>
          <option>失败：需退回</option>
        </select>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">提交说明</label>
        <Textarea className="min-h-[72px]" value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <p className="text-xs leading-relaxed text-slate-400 dark:text-slate-500">
        幂等保护：同一节点仅可成功提交一次；重复请求将返回原操作结果，不产生第二个下游任务（PRD §12）。
      </p>
    </Shell>
  )
}

/* ============ 2. 退回节点 ============ */
function ReturnDialog() {
  const { closeDialog, bumpTask } = useApp()
  const [busy, setBusy] = useState(false)
  const [target, setTarget] = useState('n4') // 需求拆分（mock 回退目标节点 ID）
  const [reason, setReason] = useState('测试失败：分派规则未覆盖周末值班场景，3 条用例失败（TC-201/202/203），需要需求拆分补充 AC-02 边界描述。')

  const doReturn = async () => {
    setBusy(true)
    try {
      const tasks = await api.get<{ items: { id: string }[] }>('/api/v1/tasks?page_size=1')
      const tid = tasks.items[0]?.id
      if (!tid) throw new ApiError(40401, '暂无待退回任务', 404)
      await api.post(`/api/v1/tasks/${tid}/actions`, { action: 'return', to_node_id: target, reason })
      closeDialog()
      bumpTask()
      toast.success('已退回至「需求拆分」：生成新一轮节点处理事件，目标处理人已通知')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '退回失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="退回节点" footer={<>
      <CancelBtn />
      <Button variant="destructive" disabled={busy} onClick={doReturn}>{busy ? '退回中…' : '确认退回'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">回退目标 <span className="text-red-500">*</span></label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="n4">需求拆分（子项需重新拆分）</option>
          <option value="n5">后端开发</option>
          <option value="n6">前端开发</option>
        </select>
        <p className="text-xs text-slate-400">仅允许模板配置的回退目标：需求拆分 · 后端开发 · 前端开发（PRD §6.2）</p>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">退回原因 <span className="text-red-500">*</span></label>
        <Textarea className="min-h-[88px]" value={reason} onChange={(e) => setReason(e.target.value)} />
        <p className="text-xs text-slate-400">退回原因将随事件通知目标节点处理人，原历史记录不可修改（PRD §6.2）</p>
      </div>
    </Shell>
  )
}

/* ============ 3. 转办 ============ */
function TransferDialog() {
  const { closeDialog } = useApp()
  const [busy, setBusy] = useState(false)
  /* 候选人：来自后端用户（匹配 qa 技能 / developer 角色的 active 用户） */
  const [candidates, setCandidates] = useState<{ id: string; name: string; dept: string; grad?: string; skills: string[]; roles: string[]; load?: number; status: string }[]>([])
  useEffect(() => {
    api.get<{ items: { id: string; name: string; dept: string; avatarGrad?: string; skills: string[]; roles: string[]; load?: number; status: string }[] }>('/api/v1/org/users')
      .then((d) => setCandidates(d.items
        .filter((u) => u.status === 'active' && (u.skills.includes('qa') || u.roles.includes('developer')))
        .map((u) => ({ ...u, grad: u.avatarGrad }))))
      .catch(() => {})
  }, [])

  const transfer = async (c: { id: string; name: string }) => {
    setBusy(true)
    try {
      const tasks = await api.get<{ items: { id: string }[] }>('/api/v1/tasks?page_size=1')
      const tid = tasks.items[0]?.id
      if (!tid) throw new ApiError(40401, '暂无待转办任务', 404)
      await api.post(`/api/v1/tasks/${tid}/actions`, { action: 'transfer', to_user_id: c.id })
      closeDialog()
      toast.success(`已转办给 ${c.name}：转办事件已审计，原处理人历史保留（PRD §4.5）`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '转办失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="转办任务" footer={<CancelBtn />}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">转办方式</label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
          <option>按角色候选人（qa）</option>
          <option>指定负责人</option>
        </select>
      </div>
      <div>
        <div className="mb-2 text-xs font-semibold text-slate-500 dark:text-slate-400">候选人（匹配：技能 qa / 角色 developer）</div>
        <div className="space-y-2">
          {candidates.map((c) => (
            <div key={c.id} className={cn('flex items-center gap-3 rounded-lg border p-2.5',
              'border-slate-200 dark:border-slate-700')}>
              <Avatar name={c.name} grad={c.grad} size={28} />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-slate-700 dark:text-slate-200">{c.name} · {c.dept || '未分配部门'}</div>
                <div className="text-[11.5px] text-slate-400">负载 {c.load ?? 0} 项 · {c.status}</div>
              </div>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => transfer(c)}>
                {busy ? '转办中…' : '转办'}
              </Button>
            </div>
          ))}
          {candidates.length === 0 && <div className="rounded-lg border border-dashed border-slate-200 p-3 text-center text-[12px] text-slate-400 dark:border-slate-700">暂无匹配候选人（后端无 active 的 qa 技能用户）</div>}
        </div>
        <p className="mt-2 text-xs text-slate-400">每次负责人变化都生成事件，不能覆盖历史（PRD §4.5）。</p>
      </div>
    </Shell>
  )
}

/* ============ 4. Expert Approval（LangGraph interrupt） ============ */
interface ConfirmReqItem {
  id: string; runId: string; tool: string; risk: string; scope: string; status: string; expiresAt: string;
}

function ExpertApprovalDialog() {
  const [list, setList] = useState<ConfirmReqItem[]>([])
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')

  /* LangGraph interrupt 产生的待审批请求。 */
  useEffect(() => {
    api.get<{ items: ConfirmReqItem[] }>('/api/v1/expert-approvals?status=pending')
      .then((d) => setList(d.items))
      .catch(() => {})
  }, [])

  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(id)
    try {
      await api.post(`/api/v1/expert-approvals/${id}/${decision}`, { note })
      setList((prev) => prev.filter((r) => r.id !== id))
      toast.success(decision === 'approve' ? '已批准：LangGraph Run 已恢复并写入审计' : '已拒绝：Run 已终止，未执行写入')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <Shell title="Expert Approval" wide footer={<>
      <CancelBtn>关闭</CancelBtn>
    </>}>
      {list.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 p-6 text-center text-[12.5px] text-slate-400 dark:border-slate-700">
          当前没有待审批的 Expert Run。<br />
          <span className="text-[11.5px]">LangGraph 在受治理写入节点 interrupt 后，会在此创建审批请求。</span>
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((r) => {
            const expired = r.expiresAt && new Date(r.expiresAt).getTime() < Date.now()
            return (
              <div key={r.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800/50">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-600 text-white">
                      <Bot className="h-4 w-4" />
                    </span>
                    <div>
                      <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">Expert Run {r.runId} · 请求执行 {r.tool}</div>
                      <div className="text-[11px] text-slate-400">风险：{r.risk} · 作用范围：{r.scope}</div>
                    </div>
                  </div>
                  {expired ? <Badge tone="err">已过期</Badge> : <Badge tone="warn">待确认</Badge>}
                </div>
                {!expired && (
                  <div className="mt-3 flex items-center gap-2">
                    <Input className="h-8 flex-1 text-[12px]" placeholder="审批意见（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
                    <Button size="sm" variant="outline" disabled={busy === r.id} onClick={() => decide(r.id, 'reject')}>{busy === r.id ? '处理中…' : '拒绝'}</Button>
                    <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" disabled={busy === r.id} onClick={() => decide(r.id, 'approve')}>{busy === r.id ? '处理中…' : '确认执行'}</Button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
      <RiskTip>
        有效权限 = Expert Deployment 策略 ∩ 授权用户功能权限 ∩ 数据范围 ∩ 项目成员关系 ∩ 本次审批范围。批准后通过 Command(resume) 恢复同一 LangGraph Run，并写入审计。
      </RiskTip>
    </Shell>
  )
}

/* ============ 14. 项目新建 / 编辑（含多模板绑定） ============ */
export function ProjectDialog({ mode, project, onSave, onClose }: {
  mode: 'create' | 'edit'
  project?: Project
  onSave: (p: Project) => void
  onClose: () => void
}) {
  /* 模板池 + 用户：来自后端（新建工作项/项目绑定选择） */
  const [tplPool, setTplPool] = useState<{ id: string; name: string; type: string; versions: string[]; nodes: { id: string; label: string; type: string }[] }[]>([])
  const [allUsers, setAllUsers] = useState<{ id: string; name: string; dept: string; status: string; roles: string[]; skills: string[] }[]>([])
  useEffect(() => {
    api.get<{ items: { id: string; name: string; type: string; versions: string[]; nodes: { id: string; label: string; type: string }[] }[] }>('/api/v1/templates/pool').then((d) => setTplPool(d.items)).catch(() => {})
    api.get<{ items: { id: string; name: string; dept: string; status: string; roles: string[]; skills: string[] }[] }>('/api/v1/org/users').then((d) => setAllUsers(d.items)).catch(() => {})
  }, [])
  const [name, setName] = useState(project?.name ?? '')
  const [code, setCode] = useState(project?.code ?? '')
  const [status, setStatus] = useState(project?.status ?? 'draft')
  const [desc, setDesc] = useState(project?.desc ?? '')
  const [manager, setManager] = useState(project?.manager ?? '')
  const [bindings, setBindings] = useState<ProjectTemplateBinding[]>(project?.templateBindings ?? [])
  /* 节点配置区默认收起（用户手动展开） */
  const [bindExpand, setBindExpand] = useState<Record<string, boolean>>({})
  const [picker, setPicker] = useState<Record<string, 'u' | 'r' | null>>({})

  const toggleTemplate = (tplId: string) => {
    const tpl = tplPool.find((t) => t.id === tplId)!
    setBindings((prev) => prev.some((b) => b.templateId === tplId)
      ? prev.filter((b) => b.templateId !== tplId)
      : [...prev, { templateId: tplId, name: tpl.name, type: tpl.type, version: tpl.versions[tpl.versions.length - 1], status: 'active', assignments: [] } as ProjectTemplateBinding])
  }
  const setVersion = (tplId: string, version: string) =>
    setBindings((prev) => prev.map((b) => b.templateId === tplId ? { ...b, version } : b))
  const toggleStatus = (tplId: string) =>
    setBindings((prev) => prev.map((b) => b.templateId === tplId ? { ...b, status: b.status === 'active' ? 'disabled' : 'active' } : b))

  /* ---------- 节点处理人绑定 ---------- */
  const updateAssignments = (tplId: string, nodeId: string, patch: Partial<NodeAssignment>) =>
    setBindings((prev) => prev.map((b) => {
      if (b.templateId !== tplId) return b
      const tpl = tplPool.find((t) => t.id === tplId)!
      const node = tpl.nodes.find((n) => n.id === nodeId)
      return {
        ...b,
        assignments: b.assignments.some((a) => a.nodeId === nodeId)
          ? b.assignments.map((a) => (a.nodeId === nodeId ? { ...a, ...patch } : a))
          : [...b.assignments, { nodeId, nodeLabel: node?.label ?? nodeId, users: [], roles: [], ...patch }],
      }
    }))
  const toggleUser = (tplId: string, nodeId: string, uid: string) => {
    const b = bindings.find((x) => x.templateId === tplId)
    const a = b?.assignments.find((x) => x.nodeId === nodeId)
    const list = a?.users ?? []
    updateAssignments(tplId, nodeId, { users: list.includes(uid) ? list.filter((u) => u !== uid) : [...list, uid] })
  }
  const toggleRole = (tplId: string, nodeId: string, role: string) => {
    const b = bindings.find((x) => x.templateId === tplId)
    const a = b?.assignments.find((x) => x.nodeId === nodeId)
    const list = a?.roles ?? []
    updateAssignments(tplId, nodeId, { roles: list.includes(role) ? list.filter((r) => r !== role) : [...list, role] })
  }
  const userName = (uid: string) => allUsers.find((u) => u.id === uid)?.name ?? uid
  const usersOfRole = (role: string) => allUsers
    .filter((u) => u.status === 'active' && (u.roles.includes(role) || u.skills.includes(role)))
    .map((u) => u.name)

  const save = () => {
    if (!name.trim() || !code.trim()) { toast('项目名称与编码为必填项'); return }
    onSave({
      id: project?.id ?? `p${Date.now()}`,
      name: name.trim(), code: code.trim().toUpperCase(), status, desc,
      members: project?.members ?? 0, workItems: project?.workItems ?? 0, progress: project?.progress ?? 0,
      manager, owner: project?.owner ?? '平台研发部', updated: '08-21', readOnly: false,
      templateBindings: bindings,
    })
    onClose()
    toast.success(`${mode === 'create' ? '已创建项目' : '已保存项目配置'}：模板绑定变更已写入审计（含 before/after）`)
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">{mode === 'create' ? '新建项目' : '编辑项目'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">项目名称 <span className="text-red-500">*</span></label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：订单中心重构" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">项目编码 <span className="text-red-500">*</span></label>
              <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="ORDER" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">状态</label>
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={status} onChange={(e) => setStatus(e.target.value as Project['status'])}>
                <option value="draft">草稿</option><option value="active">进行中</option>
                <option value="paused">已暂停</option><option value="completed">已完成</option>
                <option value="cancelled">已取消</option><option value="archived">已归档</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">项目管理员</label>
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={manager} onChange={(e) => setManager(e.target.value)}>
                {allUsers.length === 0 && <option value="">暂无用户（请先在组织管理创建）</option>}
                {allUsers.filter((u) => u.status === 'active').map((u) => (
                  <option key={u.id} value={u.name}>{u.name}（{u.dept || '未分配部门'}）</option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">项目描述</label>
            <Textarea className="min-h-[56px]" value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="项目目标与范围说明" />
          </div>

          {/* 模板绑定配置 + 节点处理人绑定 */}
          <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[13px] font-semibold text-slate-700 dark:text-slate-200">流程模板绑定</span>
              <Badge tone="info">{bindings.length}/{tplPool.length} 已绑定</Badge>
            </div>
            <p className="mb-3 text-[11.5px] leading-relaxed text-slate-400">
              一个项目可绑定多条流程线（如需求开发 + 售后问题处理）；绑定后为每个节点绑定处理人（用户 / 角色，可多个），
              流转时自动分配给绑定用户或角色所在用户（PRD §4.5）。
            </p>
            <div className="space-y-2">
              {tplPool.map((tpl) => {
                const b = bindings.find((x) => x.templateId === tpl.id)
                const checked = !!b
                const configured = (b?.assignments ?? []).filter((a) => a.users.length > 0 || a.roles.length > 0).length
                return (
                  <div key={tpl.id}>
                    {/* 模板行 */}
                    <div className={cn('flex items-center gap-3 rounded-lg border p-2.5 transition-colors',
                      checked ? 'border-blue-200 bg-blue-50/50 dark:border-blue-500/30 dark:bg-blue-500/5' : 'border-slate-200 dark:border-slate-700')}>
                      <button
                        onClick={() => toggleTemplate(tpl.id)}
                        className={cn('flex h-[16px] w-[16px] flex-none items-center justify-center rounded border transition-all',
                          checked ? 'border-blue-600 bg-blue-600' : 'border-slate-300 bg-white dark:border-slate-600 dark:bg-slate-800')}
                        title={checked ? '取消绑定' : '绑定此模板'}>
                        {checked && (
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                        )}
                      </button>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className={cn('text-[13px] font-medium', checked ? 'text-slate-800 dark:text-slate-100' : 'text-slate-400')}>{tpl.name}</span>
                          <Badge tone={tpl.type === 'requirement' ? 'info' : tpl.type === 'issue' ? 'warn' : 'pur'} className="!px-1.5 !text-[10px]">
                            {tpl.type === 'requirement' ? '需求线' : tpl.type === 'issue' ? '问题线' : '变更线'}
                          </Badge>
                          {checked && <Badge tone={configured > 0 ? 'suc' : 'gry'} className="!px-1.5 !text-[10px]">{configured}/{tpl.nodes.length} 节点已绑定</Badge>}
                        </div>
                        <div className="text-[11px] text-slate-400">可用版本：{tpl.versions.join(' / ')} · {tpl.nodes.length} 个节点</div>
                      </div>
                      {checked && (
                        <div className="flex flex-none items-center gap-2">
                          <select
                            className="h-8 rounded-md border border-slate-300 bg-white px-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                            value={b!.version} onChange={(e) => setVersion(tpl.id, e.target.value)} title="绑定版本">
                            {tpl.versions.map((v) => <option key={v} value={v}>{v}</option>)}
                          </select>
                          <button
                            onClick={() => toggleStatus(tpl.id)}
                            className={cn('rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
                              b!.status === 'active'
                                ? 'bg-emerald-50 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-500/15 dark:text-emerald-400'
                                : 'bg-slate-100 text-slate-400 hover:bg-slate-200 dark:bg-slate-800')}
                            title="启用/停用绑定（停用不影响历史实例）">
                            {b!.status === 'active' ? '启用中' : '已停用'}
                          </button>
                        </div>
                      )}
                    </div>

                    {/* 节点处理人绑定区 */}
                    {checked && b!.status === 'active' && (
                      <div className="mt-1.5 space-y-1.5 rounded-lg border border-blue-100 bg-blue-50/30 p-2.5 pl-5 dark:border-blue-500/20 dark:bg-blue-500/5">
                        <div className="flex items-center justify-between">
                          <span className="text-[11.5px] font-semibold text-slate-600 dark:text-slate-300">节点处理人绑定（流转自动分配）</span>
                          <button className="text-[11px] font-medium text-blue-600 hover:underline"
                            onClick={() => setBindExpand((p) => ({ ...p, [tpl.id]: !p[tpl.id] }))}>
                            {bindExpand[tpl.id] ? '收起' : '展开配置'}
                          </button>
                        </div>
                        {bindExpand[tpl.id] && (
                          <div className="space-y-1.5">
                            {tpl.nodes.map((node) => {
                              const a = b!.assignments.find((x) => x.nodeId === node.id)
                              const pk = `${tpl.id}:${node.id}`
                              const pick = picker[pk]
                              const hasBind = (a?.users.length ?? 0) + (a?.roles.length ?? 0) > 0
                              return (
                                <div key={node.id} className="rounded-md border border-slate-100 bg-white p-2 dark:border-slate-800 dark:bg-slate-900/60">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-[12px] font-medium text-slate-700 dark:text-slate-200">{node.label}</span>
                                    <Badge tone="gry" className="!px-1.5 !text-[10px]">{node.type}</Badge>
                                    <div className="ml-auto flex flex-none items-center gap-1">
                                      <button
                                        className={cn('rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
                                          pick === 'u' ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-blue-100 hover:text-blue-600 dark:bg-slate-800 dark:text-slate-400')}
                                        onClick={() => setPicker((p) => ({ ...p, [pk]: pick === 'u' ? null : 'u' }))}>
                                        + 用户
                                      </button>
                                      <button
                                        className={cn('rounded-md px-2 py-1 text-[11px] font-medium transition-colors',
                                          pick === 'r' ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-500 hover:bg-blue-100 hover:text-blue-600 dark:bg-slate-800 dark:text-slate-400')}
                                        onClick={() => setPicker((p) => ({ ...p, [pk]: pick === 'r' ? null : 'r' }))}>
                                        + 角色
                                      </button>
                                    </div>
                                  </div>
                                  {/* 已绑定 chips */}
                                  <div className="mt-1.5 flex flex-wrap items-center gap-1">
                                    {(a?.users ?? []).map((uid) => (
                                      <span key={uid} className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:bg-blue-500/20 dark:text-blue-300">
                                        {userName(uid)}
                                        <button onClick={() => toggleUser(tpl.id, node.id, uid)} className="text-blue-400 hover:text-red-500">×</button>
                                      </span>
                                    ))}
                                    {(a?.roles ?? []).map((role) => (
                                      <span key={role} className="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                                        {role}
                                        <button onClick={() => toggleRole(tpl.id, node.id, role)} className="text-violet-400 hover:text-red-500">×</button>
                                      </span>
                                    ))}
                                    {!hasBind && <span className="text-[11px] text-slate-400">未绑定（流转时需手动指定处理人）</span>}
                                  </div>
                                  {/* 用户选择器 */}
                                  {pick === 'u' && (
                                    <div className="mt-1.5 flex flex-wrap gap-1.5 border-t border-slate-100 pt-1.5 dark:border-slate-800">
                                      {allUsers.filter((u) => u.status === 'active').map((u) => (
                                        <button key={u.id}
                                          onClick={() => toggleUser(tpl.id, node.id, u.id)}
                                          className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors',
                                            a?.users.includes(u.id)
                                              ? 'bg-blue-600 text-white'
                                              : 'bg-slate-50 text-slate-500 hover:bg-blue-50 hover:text-blue-600 dark:bg-slate-800 dark:text-slate-400')}>
                                          {u.name} · {u.dept.split(' / ')[0]}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                  {/* 角色选择器 */}
                                  {pick === 'r' && (
                                    <div className="mt-1.5 border-t border-slate-100 pt-1.5 dark:border-slate-800">
                                      <div className="flex flex-wrap gap-1.5">
                                        {ASSIGN_ROLES.map((role) => (
                                          <button key={role}
                                            onClick={() => toggleRole(tpl.id, node.id, role)}
                                            className={cn('rounded-full px-2 py-0.5 text-[11px] font-medium transition-colors',
                                              a?.roles.includes(role)
                                                ? 'bg-violet-600 text-white'
                                                : 'bg-slate-50 text-slate-500 hover:bg-violet-50 hover:text-violet-600 dark:bg-slate-800 dark:text-slate-400')}>
                                            {role}
                                          </button>
                                        ))}
                                      </div>
                                      {(a?.roles ?? []).length > 0 && (
                                        <p className="mt-1.5 text-[10.5px] leading-relaxed text-slate-400">
                                          角色解析：{(a?.roles ?? []).map((r) => `${r} → ${usersOfRole(r).join('、') || '无在职用户'}`).join('；')}
                                        </p>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button onClick={save}>{mode === 'create' ? '创建项目' : '保存配置'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 15. 新建工作项（按模板起始节点 Schema 渲染硬性表单） ============ */
export function CreateWorkItemDialog({ onClose }: { onClose: () => void }) {
  const { bumpTask } = useApp()
  const [projId, setProjId] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [values, setValues] = useState<SchemaValues>({})
  const [busy, setBusy] = useState(false)
  const [projects, setProjects] = useState<{ id: string; name: string; code: string; status: string; templateBindings: { templateId: string; name: string; type: string; version: string; status: string }[] }[]>([])
  const [tplPool, setTplPool] = useState<{ id: string; name: string; type: string; versions: string[]; startSchema: unknown[] }[]>([])
  /* 最新版本起始表单：来自后端 GET /templates/{id}/start-schema（最新 published 版本画布 start 节点） */
  const [latestSchema, setLatestSchema] = useState<unknown[]>([])
  const [latestVersion, setLatestVersion] = useState<string>('')
  const [latestStatus, setLatestStatus] = useState<string>('')
  const [schemaLoading, setSchemaLoading] = useState(false)
  /* 预定义标签（如版本号）：创建时绑定，列表页可按 tag 分类筛选 */
  const [tags, setTags] = useState<{ id: string; name: string; color: string }[]>([])
  const [selLabels, setSelLabels] = useState<string[]>([])

  /* 项目 + 模板池 + 标签：来自后端 */
  useEffect(() => {
    api.get<{ items: { id: string; name: string; code: string; status: string; templateBindings: { templateId: string; name: string; type: string; version: string; status: string }[] }[] }>('/api/v1/projects').then((d) => setProjects(d.items)).catch(() => {})
    api.get<{ items: { id: string; name: string; type: string; versions: string[]; startSchema: unknown[] }[] }>('/api/v1/templates/pool').then((d) => setTplPool(d.items)).catch(() => {})
    api.get<{ items: { id: string; name: string; color: string }[] }>('/api/v1/tags').then((d) => setTags(d.items)).catch(() => {})
  }, [])

  const toggleLabel = (name: string) =>
    setSelLabels((prev) => (prev.includes(name) ? prev.filter((l) => l !== name) : [...prev, name]))

  /* 选择模板 → 拉取该模板最新版本的硬性要求表单（跟随最新发布版本，而非模板级静态 startSchema） */
  useEffect(() => {
    setLatestSchema([])
    setLatestVersion('')
    setLatestStatus('')
    if (!templateId) return
    setSchemaLoading(true)
    api.get<{ version: string | null; status: string | null; schema: unknown[]; fallback: boolean }>(`/api/v1/templates/${templateId}/start-schema`)
      .then((d) => {
        setLatestSchema(d.schema)
        if (d.version) setLatestVersion(d.version)
        if (d.status) setLatestStatus(d.status)
      })
      .catch(() => { /* 后端不可用：保持空表单 */ })
      .finally(() => setSchemaLoading(false))
  }, [templateId])

  const project = projects.find((p) => p.id === projId)
  const available = (project?.templateBindings ?? []).filter((b) => b.status === 'active')
  const tpl = tplPool.find((t) => t.id === templateId)
  const rawFields = (latestSchema.length ? latestSchema : (tpl?.startSchema ?? [])) as Parameters<typeof validateSchema>[0]
  /* 兜底：表单必须包含「标题」字段（后端创建流程强制 title 必填）——
     无论 schema 为空还是缺 title 字段，都补到最前，保证界面始终有标题输入框 */
  const needsTitle = !rawFields.some((f) => f.key === 'title')
  const fields = (needsTitle ? [{ key: 'title', label: '标题', type: 'input', required: true }, ...rawFields] : rawFields) as Parameters<typeof validateSchema>[0]
  const missing = validateSchema(fields, values)
  const canSubmit = !!project && !!tpl && missing.length === 0 && !busy && !schemaLoading

  const pickProject = (id: string) => {
    setProjId(id)
    setTemplateId('')
    setValues({})
  }

  const submit = async () => {
    const bind = available.find((b) => b.templateId === templateId)!
    setBusy(true)
    try {
      // 真实创建：POST /work-items（标题去重、发起流程实例）
      await api.post('/api/v1/work-items', { project_id: projId, template_id: templateId, start_values: values, labels: selLabels })
      toast.success(`已创建${tpl!.type === 'requirement' ? '需求' : tpl!.type === 'issue' ? '问题' : '变更'}并启动「${bind.templateId} ${bind.status}」：${String(values.title || '未命名')}`)
      bumpTask()  // 触发「我的任务」列表刷新（起始节点任务 + 下一节点待办可见）
      onClose()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建工作项失败')
    } finally {
      setBusy(false)
    }
  }

  const typeLabel = tpl
    ? tpl.type === 'requirement' ? '需求线' : tpl.type === 'issue' ? '问题线' : '变更线'
    : ''
  const typeTone = tpl?.type === 'requirement' ? 'info' : tpl?.type === 'issue' ? 'warn' : 'pur'

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">新建工作项</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">所属项目 <span className="text-red-500">*</span></label>
            <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              value={projId} onChange={(e) => pickProject(e.target.value)}>
              <option value="">请选择项目</option>
              {projects.filter((p) => p.status === 'active').map((p) => (
                <option key={p.id} value={p.id}>{p.name}（{p.code}）</option>
              ))}
            </select>
          </div>

          {project && (
            <>
              <div className="space-y-1.5">
                <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">流程模板（{project.name} 已绑定）</label>
                {available.length > 0 ? (
                  <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                    value={templateId} onChange={(e) => { setTemplateId(e.target.value); setValues({}) }}>
                    <option value="">请选择模板（决定工作项类型线）</option>
                    {available.map((b) => (
                      <option key={b.templateId} value={b.templateId}>{b.name} {b.version}（{b.type === 'requirement' ? '需求线' : b.type === 'issue' ? '问题线' : '变更线'}）</option>
                    ))}
                  </select>
                ) : (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-[12px] leading-relaxed text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                    该项目未绑定任何可用模板，无法创建工作项。请先在<b> 项目编辑 → 流程模板绑定 </b>中配置后再试。
                  </div>
                )}
                <p className="text-[11.5px] text-slate-400">模板即决定了工作项类型线（需求 / 问题 / 变更）；模板停用不影响历史实例（PRD §4.3）。</p>
              </div>

              {tpl && (
                <div className="rounded-xl border border-slate-200 p-4 dark:border-slate-700">
                  <div className="mb-3 flex items-center gap-2">
                    <span className="text-[13px] font-semibold text-slate-700 dark:text-slate-200">硬性要求表单</span>
                    <Badge tone={typeTone}>{typeLabel}</Badge>
                    <span className="text-[11px] text-slate-400">
                      {schemaLoading ? '加载中…' : latestVersion ? `来自「${tpl.name}」最新版本 ${latestVersion}（${latestStatus === 'published' ? '已发布' : '草稿'}）` : `来自「${tpl.name}」起始节点`}
                    </span>
                  </div>
                  <SchemaForm fields={fields} values={values} onChange={setValues} project={project.name} />
                </div>
              )}
            </>
          )}

          {tags.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">绑定标签 <span className="text-[11.5px] font-normal text-slate-400">（预定义，可多选，如迭代版本号）</span></label>
              <div className="flex flex-wrap gap-1.5">
                {tags.map((t) => (
                  <button key={t.id} type="button"
                    className={cn('rounded-full border px-2.5 py-1 text-[11.5px] transition-colors',
                      selLabels.includes(t.name)
                        ? 'border-blue-500 bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300'
                        : 'border-slate-200 text-slate-500 hover:border-blue-300 hover:text-blue-500 dark:border-slate-700 dark:text-slate-400')}
                    onClick={() => toggleLabel(t.name)}>
                    {t.name}
                  </button>
                ))}
              </div>
              {tags.length === 0 && <p className="text-[11.5px] text-slate-400">暂无预定义标签，可在「工作项」页面管理。</p>}
            </div>
          )}
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose}>取消</Button>
          <Button disabled={!canSubmit} onClick={submit} title={missing.length > 0 ? `还有 ${missing.length} 个必填项未填写` : undefined}>
            创建并启动流程
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 发布校验（画布「发布校验」按钮）：调后端校验接口，问题清单 + 定位跳转 ============ */
function ValidateDialog() {
  const { closeDialog, canvasTarget, setCheckProblems, locateCanvasNode, openCanvas } = useApp()
  const tplId = canvasTarget?.templateId ?? 'tpl-req'
  const tplName = canvasTarget?.templateName ?? '需求流程'
  const version = canvasTarget?.version ?? 'v3'
  const [busy, setBusy] = useState(true)
  const [result, setResult] = useState<{ ok: boolean; errors: { node_id: string; message: string }[] } | null>(null)
  const [error, setError] = useState('')

  const run = () => {
    setBusy(true)
    setError('')
    api.get<{ nodes: unknown[]; edges: unknown[]; fallbacks: unknown[] }>(`/api/v1/templates/${tplId}/versions/${version}/canvas`)
      .then((canvas) => api.post<{ ok: boolean; errors: { node_id: string; message: string }[] }>(
        `/api/v1/templates/${tplId}/versions/${version}/canvas/validate`,
        { nodes: canvas.nodes, edges: canvas.edges, fallbacks: canvas.fallbacks },
      ))
      .then((data) => {
        setResult(data)
        setCheckProblems((data.errors ?? []).map((e) => ({ title: e.message, nodeId: e.node_id, desc: e.message })))
      })
      .catch((e) => setError(e instanceof Error ? e.message : '校验请求失败'))
      .finally(() => setBusy(false))
  }
  useEffect(() => { run() }, []) // eslint-disable-line react-hooks/exhaustive-deps -- 打开即校验一次

  const locate = (nodeId: string) => {
    closeDialog()
    openCanvas(tplId, tplName, version)
    locateCanvasNode(nodeId)
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) closeDialog() }}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">发布校验 · {tplName} {version}</DialogTitle>
        </DialogHeader>
        {busy && <p className="py-6 text-center text-[12.5px] text-slate-400">正在校验画布拓扑与节点配置…</p>}
        {!busy && error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] text-red-600 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">{error}</div>
        )}
        {!busy && result && (
          <div className="space-y-3">
            {result.ok ? (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[12.5px] text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
                ✓ 校验通过：开始/结束节点、入出边、悬空、回退与 Expert 绑定均合法，可执行「发布」。
              </div>
            ) : (
              <>
                <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-[12.5px] text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
                  ✗ 发现 {result.errors.length} 个阻断问题，修复后才能发布：
                </div>
                <div className="max-h-[280px] space-y-1.5 overflow-y-auto">
                  {result.errors.map((e, i) => (
                    <div key={i} className="flex items-start gap-2 rounded-lg border border-slate-200 px-3 py-2 dark:border-slate-700">
                      <span className="mt-1.5 h-1.5 w-1.5 flex-none rounded-full bg-red-400" />
                      <span className="flex-1 text-[12px] leading-relaxed text-slate-600 dark:text-slate-300">{e.message}</span>
                      {e.node_id && e.node_id !== '—' && (
                        <button className="flex-none text-[11px] font-medium text-blue-600 hover:underline" onClick={() => locate(e.node_id)}>定位</button>
                      )}
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-slate-400">「定位」跳转到画布并高亮问题节点。</p>
              </>
            )}
          </div>
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={closeDialog}>关闭</Button>
          <Button disabled={busy} onClick={run}>重新校验</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 创建本地用户（组织管理）：POST /org/users，初始状态 invited ============ */
function CreateUserDialog() {
  const { closeDialog, refreshOrgUsers } = useApp()
  const [form, setForm] = useState({ account: '', name: '', email: '', dept: '', role_id: 'developer', password: '' })
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((prev) => ({ ...prev, [k]: e.target.value }))

  const submit = async () => {
    if (!form.account.trim() || !form.name.trim() || form.password.length < 8) {
      toast.error('请填写账号/姓名，密码至少 8 位'); return
    }
    setBusy(true)
    try {
      await api.post('/api/v1/org/users', { ...form, account: form.account.trim(), name: form.name.trim() })
      toast.success(`已创建本地用户「${form.name.trim()}」（待激活），首登强制改密`)
      refreshOrgUsers()
      closeDialog()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '创建失败')
    } finally { setBusy(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) closeDialog() }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader><DialogTitle className="text-[15px]">创建本地用户</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">账号 <span className="text-red-500">*</span></label>
              <Input value={form.account} onChange={set('account')} placeholder="登录账号（唯一）" />
            </div>
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">姓名 <span className="text-red-500">*</span></label>
              <Input value={form.name} onChange={set('name')} placeholder="真实姓名" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">邮箱</label>
              <Input value={form.email} onChange={set('email')} placeholder="name@company.com" />
            </div>
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">部门</label>
              <Input value={form.dept} onChange={set('dept')} placeholder="如 平台研发部 / 平台组" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">初始角色</label>
            <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              value={form.role_id} onChange={set('role_id')}>
              {['system_admin', 'organization_admin', 'project_admin', 'leader', 'product_manager', 'developer', 'after_sales', 'pre_sales', 'second_line'].map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">初始密码 <span className="text-red-500">*</span></label>
            <Input type="password" value={form.password} onChange={set('password')} placeholder="至少 8 位，首登强制改密" />
          </div>
          <p className="text-[11px] text-slate-400">创建后为「待激活（invited）」状态，管理员在注册审批列表中激活。</p>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={closeDialog}>取消</Button>
          <Button disabled={busy} onClick={submit}>{busy ? '创建中…' : '创建'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 注册本地账号（登录页自注册）：POST /auth/register ============ */
function RegisterAccountDialog() {
  const { closeDialog } = useApp()
  const [form, setForm] = useState({ account: '', name: '', email: '', dept: '', role_id: 'developer', password: '' })
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((prev) => ({ ...prev, [k]: e.target.value }))

  const submit = async () => {
    if (!form.account.trim() || !form.name.trim() || form.password.length < 8) {
      toast.error('请填写账号/姓名，密码至少 8 位'); return
    }
    setBusy(true)
    try {
      await api.post('/api/v1/auth/register', { ...form, account: form.account.trim(), name: form.name.trim() })
      toast.success('注册成功，等待管理员审批激活')
      closeDialog()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '注册失败')
    } finally { setBusy(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) closeDialog() }}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader><DialogTitle className="text-[15px]">注册本地账号</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">账号 <span className="text-red-500">*</span></label>
              <Input value={form.account} onChange={set('account')} placeholder="登录账号（唯一）" />
            </div>
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">姓名 <span className="text-red-500">*</span></label>
              <Input value={form.name} onChange={set('name')} placeholder="真实姓名" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">邮箱 <span className="text-red-500">*</span></label>
            <Input value={form.email} onChange={set('email')} placeholder="name@company.com" />
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">部门</label>
              <Input value={form.dept} onChange={set('dept')} placeholder="如 平台研发部 / 平台组" />
            </div>
            <div>
              <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">申请角色</label>
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                value={form.role_id} onChange={set('role_id')}>
                {['developer', 'product_manager', 'after_sales', 'pre_sales', 'second_line', 'leader'].map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">密码 <span className="text-red-500">*</span></label>
            <Input type="password" value={form.password} onChange={set('password')} placeholder="至少 8 位" />
          </div>
          <p className="text-[11px] text-slate-400">提交后生成待审批申请，管理员在「注册审批」中激活后即可登录。</p>
        </div>
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={closeDialog}>取消</Button>
          <Button disabled={busy} onClick={submit}>{busy ? '提交中…' : '提交注册'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 注册表 ============ */
const DIALOGS: Record<string, () => React.ReactElement> = {
  submit: SubmitDialog,
  return: ReturnDialog,
  transfer: TransferDialog,
  expertApproval: ExpertApprovalDialog,
  validate: ValidateDialog,
  createUser: CreateUserDialog,
  registerAccount: RegisterAccountDialog,
}

export function DialogHost() {
  const { dialog } = useApp()
  if (!dialog) return null
  const Comp = DIALOGS[dialog]
  return Comp ? <Comp /> : null
}
