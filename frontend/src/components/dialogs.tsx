import { useEffect, useState } from 'react'
import { Bot } from 'lucide-react'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Textarea } from '../components/ui/textarea'
import { useApp, toast } from '../store/app-store'
import { api, ApiError, getStoredUser, setStoredUser } from '../lib/api'
import { Avatar, Badge, Kv, RiskTip } from './common'
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

/* ============ 4. Agent 操作确认（高风险） ============ */
interface ConfirmReqItem {
  id: string; agentId: string; agentName: string; taskId: string | null; nodeId: string;
  action: string; capability: string; opScope: string; authorizedUser: string;
  status: string; prompt: string; result: { text?: string } | null;
  expireAt: string; createdAt: string; decisionNote: string;
}

function AgentConfirmDialog() {
  const [list, setList] = useState<ConfirmReqItem[]>([])
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')

  /* 拉取真实待确认请求（GET /agents/confirm-requests?status=pending） */
  useEffect(() => {
    api.get<{ items: ConfirmReqItem[] }>('/api/v1/agents/confirm-requests?status=pending&page_size=10')
      .then((d) => setList(d.items))
      .catch(() => {})
  }, [])

  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(id)
    try {
      await api.post(`/api/v1/agents/confirm-requests/${id}/${decision}`, { note })
      setList((prev) => prev.filter((r) => r.id !== id))
      toast.success(decision === 'approve' ? '已批准：Agent 产出已生效并写入审计' : '已拒绝：Agent 产出已标记为拒绝')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <Shell title="Agent 操作确认" wide footer={<>
      <CancelBtn>关闭</CancelBtn>
    </>}>
      {list.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-200 p-6 text-center text-[12.5px] text-slate-400 dark:border-slate-700">
          当前没有待确认的 Agent 操作请求。<br />
          <span className="text-[11.5px]">Agent 调用 confirm 能力（如生成内容/写表单）时，会在此生成待确认请求。</span>
        </div>
      ) : (
        <div className="space-y-3">
          {list.map((r) => {
            const expired = r.expireAt && new Date(r.expireAt).getTime() < Date.now()
            return (
              <div key={r.id} className="rounded-xl border border-slate-200 bg-slate-50 p-4 dark:border-slate-700 dark:bg-slate-800/50">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-600 text-white">
                      <Bot className="h-4 w-4" />
                    </span>
                    <div>
                      <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">{r.agentName} · 请求 {r.action || r.capability}</div>
                      <div className="text-[11px] text-slate-400">能力：{r.capability} · 授权范围：{r.opScope} · 授权用户：{r.authorizedUser} · 创建：{r.createdAt}</div>
                    </div>
                  </div>
                  {expired ? <Badge tone="err">已过期</Badge> : <Badge tone="warn">待确认</Badge>}
                </div>
                {r.result?.text && (
                  <div className="mt-2.5 rounded-lg border border-violet-100 bg-white p-2.5 text-[12px] leading-relaxed text-slate-600 dark:border-violet-500/20 dark:bg-slate-900 dark:text-slate-300">
                    <div className="mb-1 text-[11px] font-semibold text-violet-600 dark:text-violet-400">Agent 产出预览</div>
                    {r.result.text.slice(0, 300)}{r.result.text.length > 300 ? '…' : ''}
                  </div>
                )}
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
        有效权限 = Agent 能力 ∩ 授权用户功能权限 ∩ 数据范围 ∩ 项目成员关系 ∩ 节点角色/技能 ∩ 节点绑定 ∩ 本次授权范围。批准后写入审计（含授权用户）。
      </RiskTip>
    </Shell>
  )
}

/* ============ 5. Agent 创建 ============ */
/* Agent 类型图标（可视化类型选择器） */
const TYPE_ICON: Record<string, string> = {
  data_analysis: '📊', test_case: '🧪', code_review: '🔍', doc_generate: '📝', notify_probe: '🔔', summary: '📋',
}
const MODE_ZH: Record<string, string> = { direct: '直执', confirm: '需确认', forbid: '禁止' }

function AgentCreateDialog() {
  const { bumpTask } = useApp()
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [toolId, setToolId] = useState('')
  const [agentType, setAgentType] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [secret, setSecret] = useState<string | null>(null)
  const [tools, setTools] = useState<{ id: string; name: string; engine: string; provider: string; models: string[] }[]>([])
  const [types, setTypes] = useState<{ id: string; code: string; label: string; desc: string; defaultCaps: Record<string, string>; systemPrompt: string; provider: string; model: string }[]>([])
  const [models, setModels] = useState<{ id: string; provider: string; model: string; label: string; desc: string; source?: string }[]>([])
  const [modelId, setModelId] = useState('')
  /* 添加自定义厂商模型（创建 Agent 时也可现场新增 provider） */
  const [addingModel, setAddingModel] = useState(false)
  const [newModel, setNewModel] = useState({ provider: '', model: '', label: '', baseUrl: '' })

  /* 客户端工具 + 类型（含系统提示词模板）+ 可选模型：来自后端配置 */
  useEffect(() => {
    api.get<{ items: { id: string; name: string; engine: string; provider: string; models: string[]; status: string }[] }>('/api/v1/agents/tools').then((d) => setTools(d.items.filter((t) => t.status === 'active'))).catch(() => {})
    api.get<{ items: { id: string; code: string; label: string; desc: string; defaultCaps: Record<string, string>; systemPrompt: string; provider: string; model: string }[] }>('/api/v1/agents/types').then((d) => setTypes(d.items)).catch(() => {})
    api.get<{ items: { id: string; provider: string; model: string; label: string; desc: string; source?: string }[] }>('/api/v1/agents/models').then((d) => setModels(d.items)).catch(() => {})
  }, [])

  const pickModel = (id: string) => {
    if (id === '__add_model__') { setAddingModel(true); return }
    setAddingModel(false)
    setModelId(id)
  }

  const addCustomModel = async () => {
    if (!newModel.provider.trim() || !newModel.model.trim() || !newModel.baseUrl.trim()) {
      toast('Provider / Model / Base URL 必填'); return
    }
    setBusy(true)
    try {
      await api.post('/api/v1/agents/models', {
        provider: newModel.provider.trim(), model: newModel.model.trim(),
        label: newModel.label.trim(), base_url: newModel.baseUrl.trim(),
      })
      const d = await api.get<{ items: { id: string; provider: string; model: string; label: string; desc: string }[] }>('/api/v1/agents/models')
      setModels(d.items)
      const added = d.items.find((m) => m.provider === newModel.provider.trim() && m.model === newModel.model.trim())
      if (added) setModelId(added.id)
      setAddingModel(false)
      setNewModel({ provider: '', model: '', label: '', baseUrl: '' })
      toast.success(`自定义厂商模型已添加：${newModel.provider.trim()}/${newModel.model.trim()}`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '添加失败')
    } finally {
      setBusy(false)
    }
  }

  const selectedTool = tools.find((t) => t.id === toolId)
  const providerModels = selectedTool
    ? selectedTool.models.map((model) => models.find((m) => m.provider === selectedTool.provider && m.model === model)
      ?? { id: `${selectedTool.id}:${model}`, provider: selectedTool.provider, model, label: model, desc: '' })
    : []
  const selectedModel = providerModels.find((m) => m.id === modelId)

  const create = async () => {
    if (!name.trim()) { toast('Agent 名称为必填项'); return }
    if (!toolId) { toast('请选择 Provider'); return }
    if (!selectedModel) { toast('请选择 Provider 下的模型'); return }
    setBusy(true)
    try {
      const d = await api.post<{ agent: { name: string; code: string }; secret: string }>('/api/v1/agents/register', {
        name: name.trim(), tool_id: toolId, agent_type: agentType,
        provider: selectedModel?.provider || selectedTool?.provider || '',
        model: selectedModel?.model || '',
        system_prompt: systemPrompt.trim(),
      })
      setSecret(d.secret)
      setSystemPrompt('')
      bumpTask()
      toast.success(`Agent 已创建：${d.agent.name}（${d.agent.code}）状态 pending，等待激活`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  const copySecret = async () => {
    if (!secret) return
    try {
      await navigator.clipboard.writeText(secret)
      toast.success('密钥已复制')
    } catch {
      toast('密钥已复制（手动复制）')
    }
  }

  return (
    <Shell title="创建 Agent" wide footer={<>
      <CancelBtn />
      <Button disabled={busy} onClick={create}>{busy ? '创建中…' : secret ? '再创建一个' : '创建'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Agent 名称 <span className="text-red-500">*</span></label>
        <Input placeholder="例如：FlowBot-DA 数据分析助手" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

       <div className="space-y-1.5">
         <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Provider <span className="text-red-500">*</span></label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={toolId} onChange={(e) => setToolId(e.target.value)}>
           <option value="">选择 Provider…（在「Provider 管理」Tab 中预先配置）</option>
           {tools.map((t) => (
             <option key={t.id} value={t.id}>{t.name}（{t.engine === 'api' ? 'API 直连' : 'CLI 引擎'} · {t.provider || '未配置 Provider'}）</option>
          ))}
        </select>
        {selectedTool && (
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-[11.5px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
             {selectedTool.engine === 'api' ? `API Provider：${selectedTool.provider}，执行时使用该 Provider 的 API Key` : `${selectedTool.name} CLI Provider：${selectedTool.provider}，使用平台配置的模型执行`}
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">模型 <span className="text-red-500">*</span></label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={modelId} onChange={(e) => pickModel(e.target.value)}>
           <option value="">选择 {selectedTool?.provider || 'Provider'} 下的模型…</option>
          {/* 按厂商分类（optgroup）展示模型 */}
           {providerModels.reduce<{ name: string; items: typeof providerModels }[]>((acc, m) => {
            const g = acc.find((x) => x.name === m.provider)
            if (g) g.items.push(m); else acc.push({ name: m.provider, items: [m] })
            return acc
          }, []).map((g) => (
            <optgroup key={g.name} label={`${g.name} 厂商`}>
              {g.items.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          ))}
        </select>
        {addingModel && (
          <div className="space-y-1.5 rounded-lg border border-blue-200 bg-blue-50/50 p-2.5 dark:border-blue-500/30 dark:bg-blue-500/5">
            <div className="grid grid-cols-2 gap-2">
              <Input placeholder="Provider（厂商名，如 硅基流动）" value={newModel.provider} onChange={(e) => setNewModel({ ...newModel, provider: e.target.value })} />
              <Input placeholder="Model（如 deepseek-v3）" value={newModel.model} onChange={(e) => setNewModel({ ...newModel, model: e.target.value })} />
            </div>
            <Input placeholder="Base URL（OpenAI 兼容，如 https://api.siliconflow.cn/v1）" value={newModel.baseUrl} onChange={(e) => setNewModel({ ...newModel, baseUrl: e.target.value })} />
            <div className="flex items-center gap-2">
              <Input placeholder="Label（可选，默认 provider/model）" value={newModel.label} onChange={(e) => setNewModel({ ...newModel, label: e.target.value })} />
              <Button size="sm" disabled={busy} onClick={addCustomModel}>{busy ? '添加中…' : '添加'}</Button>
              <Button variant="outline" size="sm" onClick={() => setAddingModel(false)}>取消</Button>
            </div>
          </div>
        )}
        {selectedModel && (
          <p className="text-xs text-slate-400">将使用 {selectedModel.label}（{selectedModel.provider} · {selectedModel.model}）执行</p>
        )}
      </div>

      <div>
        <div className="mb-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-300">Agent 类型 <span className="text-slate-400">（可选，带出系统提示词模板）</span></div>
        <div className="grid grid-cols-2 gap-2">
          {types.map((t) => {
            const active = agentType === t.code
            const caps = Object.entries(t.defaultCaps ?? {})
            return (
              <button key={t.id} type="button"
                className={cn('rounded-lg border p-2.5 text-left transition-colors',
                  active ? 'border-violet-500 bg-violet-50/60 ring-1 ring-violet-500 dark:bg-violet-500/10' : 'border-slate-200 hover:border-violet-300 dark:border-slate-700')}
                onClick={() => {
                   if (active) { setAgentType('') } else {
                     setAgentType(t.code)
                     setSystemPrompt(t.systemPrompt || '')   // 带出该类型系统提示词模板
                     const provider = tools.find((tool) => tool.provider === t.provider)
                     if (provider) {
                       setToolId(provider.id)
                       setModelId(t.model ? `${provider.id}:${t.model}` : '')
                     }
                   }
                }}>
                <div className="flex items-center gap-1.5">
                  <span className="text-base leading-none">{TYPE_ICON[t.code] ?? '🤖'}</span>
                  <span className="text-[12.5px] font-semibold text-slate-700 dark:text-slate-200">{t.label}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-slate-400">{t.desc}</p>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {caps.slice(0, 3).map(([k, v]) => (
                    <span key={k} className={cn('rounded px-1 py-0.5 text-[10px]', v === 'direct' ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400' : 'bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400')}>{k} · {MODE_ZH[v] ?? v}</span>
                  ))}
                  {caps.length > 3 && <span className="text-[10px] text-slate-400">+{caps.length - 3}</span>}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">系统提示词 <span className="text-red-500">*</span></label>
        <Textarea rows={5}
          placeholder={'你是 FlowHub 流程协同平台中的 {名称} Agent。\n职责：…（描述这个 Agent 做什么、擅长什么）\n输出要求：…（输出风格与格式要求）'}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)} />
        <p className="text-xs text-slate-400">作为该 Agent 的默认 system prompt，执行时注入给模型。选择类型会自动带出模板，可自由修改自定义。</p>
      </div>

      {secret ? (
        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">密钥（仅此一次展示，请立即保存）</label>
          <div className="flex gap-2">
            <Input className="font-mono" readOnly value={secret} />
            <Button variant="outline" size="sm" onClick={copySecret}>复制</Button>
          </div>
          <p className="text-xs text-slate-400">服务端仅保存密钥哈希；关闭本窗口后不可再次查看。</p>
        </div>
      ) : (
       <p className="text-xs leading-relaxed text-slate-400 dark:text-slate-500">创建后返回一次性密钥（仅展示一次）；状态为 pending，需在 Agent 管理页激活。Provider 负责连接配置，模型由当前 Agent 显式选择。</p>
      )}
    </Shell>
  )
}

/* ============ 5.1 Agent 客户端配置（第一类） ============ */
function ToolEditDialog() {
  const { closeDialog, bumpTask, dialogToolId } = useApp()
  const editing = !!dialogToolId   // 编辑模式：dialogToolId 非空
  const [busy, setBusy] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; latencyMs?: number; error?: string } | null>(null)
  const [name, setName] = useState('')
  const [engine, setEngine] = useState<'opencode' | 'api'>('opencode')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiProvider, setApiProvider] = useState('')
  const [apiModels, setApiModels] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [desc, setDesc] = useState('')
  // opencode 引擎特殊字段
  const [ocProviders, setOcProviders] = useState<{ provider: string; type: string; hasKey: boolean; baseUrl?: string; models?: string[] }[]>([])
  const [ocProvider, setOcProvider] = useState('')
  const [ocCustom, setOcCustom] = useState(false)
  const [ocCustomName, setOcCustomName] = useState('')
  const [ocBaseUrl, setOcBaseUrl] = useState('')
  const [ocModels, setOcModels] = useState('')

  useEffect(() => {
    // opencode 引擎：拉 auth.json 已配置 providers
    if (engine === 'opencode') {
      api.get<{ items: { provider: string; type: string; hasKey: boolean; baseUrl?: string; models?: string[] }[] }>('/api/v1/agents/tools/opencode/providers')
        .then((d) => setOcProviders(d.items))
        .catch(() => {})
    }
  }, [engine])

  useEffect(() => {
    /* 编辑模式：拉取工具列表，回填表单（api_key 不回显，留空=保持不变） */
    if (editing) {
      api.get<{ items: { id: string; name: string; engine: string; provider: string; models: string[]; baseUrl: string; desc: string }[] }>('/api/v1/agents/tools')
        .then((d) => {
          const t = d.items.find((x) => x.id === dialogToolId)
          if (t) {
            setName(t.name)
            setEngine(t.engine as 'opencode' | 'api')
             setBaseUrl(t.baseUrl || '')
             setApiProvider(t.provider || '')
              setApiModels((t.models || []).join(', '))
            setDesc(t.desc || '')
            if (t.engine === 'opencode') setOcProvider(t.provider || '')
          }
        })
        .catch(() => {})
    }
  }, [editing, dialogToolId])

  // 编辑已有 OpenCode Provider 时，工具记录和运行时 provider 列表是异步返回的；
  // 等 provider 详情到位后再回填 Base URL 与模型清单。
  useEffect(() => {
    if (engine !== 'opencode' || !ocProvider) return
    const provider = ocProviders.find((item) => item.provider === ocProvider)
    if (!provider) return
    setOcBaseUrl(provider.baseUrl || '')
    setOcModels((provider.models || []).join(', '))
  }, [engine, ocProvider, ocProviders])


  const testConnection = async () => {
     const model = apiModels.split(/[,，]/).map((item) => item.trim()).find(Boolean)
     if (!baseUrl.trim() || !apiKey.trim() || !model) { toast('请先填写 Provider、模型列表、Base URL 与 API Key'); return }
    setTesting(true)
    setTestResult(null)
    try {
      const d = await api.post<{ ok: boolean; latencyMs: number; error?: string }>('/api/v1/agents/test-connection', {
         base_url: baseUrl.trim(), api_key: apiKey.trim(), model,
      })
      setTestResult(d)
      toast.success(d.ok ? `连接成功（${d.latencyMs}ms）` : '连接失败，请检查 Key / Base URL')
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof ApiError ? e.message : '测试失败' })
      toast.error('连接测试失败')
    } finally {
      setTesting(false)
    }
  }

  /** opencode 引擎：直接写入 auth.json + upsert agent_tools opencode 记录 */
  const applyToOpencode = async () => {
    const provider = ocCustom ? ocCustomName.trim() : ocProvider.trim()
    if (!provider) { toast('请选择或输入 Provider'); return }
    if (!apiKey.trim() && !editing) { toast('请填写 API Key'); return }
    const baseUrl = ocBaseUrl || ''
    const models = (ocModels || '').split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    setBusy(true)
    try {
      await api.post('/api/v1/agents/tools/opencode/apply', { provider, api_key: apiKey.trim(), kind: 'api', base_url: baseUrl, models })
      bumpTask()
      closeDialog()
      toast.success(`opencode 客户端已配置（${provider}），auth.json 已写入；创建 Agent 时即可选择 opencode`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '写入失败')
    } finally {
      setBusy(false)
    }
  }

  const submit = async () => {
    if (engine === 'opencode') { applyToOpencode(); return }
    if (!name.trim()) { toast('客户端名称为必填项'); return }
    if (engine === 'api') {
      if (!baseUrl.trim()) { toast('Base URL 必填'); return }
      if (!editing && !apiKey.trim()) { toast('API Key 必填'); return }   // 编辑时 Key 可留空（保持不变）
    }
    setBusy(true)
    try {
      const payload = {
        name: name.trim(), engine,
        provider: engine === 'api' ? apiProvider.trim() : (ocCustom ? ocCustomName.trim() : ocProvider.trim()),
        model: '', models: engine === 'api'
          ? apiModels.split(/[,，]/).map((item) => item.trim()).filter(Boolean)
          : (ocModels || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean),
        base_url: baseUrl.trim(), api_key: apiKey.trim(), desc: desc.trim(),
      }
      if (editing) {
        await api.put(`/api/v1/agents/tools/${dialogToolId}`, payload)
        toast.success(`Agent 客户端「${name.trim()}」已更新`)
      } else {
        await api.post('/api/v1/agents/tools', payload)
        toast.success(`Agent 客户端「${name.trim()}」已配置，可在创建 Agent 时选择`)
      }
      bumpTask()
      closeDialog()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title={editing ? '编辑 Agent 客户端' : '新增 Agent 客户端'} wide footer={<>
      <CancelBtn />
      <Button disabled={busy} onClick={submit}>{busy ? '保存中…' : '保存'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">工具名称 {engine === 'opencode' ? '（opencode 固定）' : <span><span className="text-red-500">*</span></span>}</label>
        <Input
          placeholder={engine === 'opencode' ? 'opencode' : '例如：deepseek-api / openai-api'}
          value={name}
          disabled={engine === 'opencode'}
          onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">执行引擎</label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={engine} onChange={(e) => setEngine(e.target.value as 'opencode' | 'api')}>
          <option value="opencode">opencode（CLI 引擎，容器内置）</option>
          <option value="api">API 直连（OpenAI 兼容接口，需 API Key）</option>
        </select>
      </div>

      {engine === 'api' && (
        <>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Provider 名称 <span className="text-red-500">*</span></label>
            <Input placeholder="例如：DeepSeek / OpenAI / 内部网关" value={apiProvider} onChange={(e) => setApiProvider(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">模型列表 <span className="text-red-500">*</span></label>
            <Input placeholder="deepseek-chat, deepseek-reasoner" value={apiModels} onChange={(e) => setApiModels(e.target.value)} />
            <p className="text-xs text-slate-400">逗号分隔。Agent 创建与任务调用仅能选择此 Provider 已登记的模型。</p>
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Base URL <span className="text-red-500">*</span></label>
            <Input placeholder="https://api.deepseek.com/v1" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">API Key {editing ? '' : <span className="text-red-500">*</span>}</label>
            <Input type="password" placeholder={editing ? '留空保持不变，仅填写新 Key 时更新' : 'sk-...'} value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            <p className="text-xs text-slate-400">加密存储于服务端，仅在执行时解密使用，任何接口不回显。</p>
          </div>
          <div className="flex items-center gap-3">
            <Button variant="outline" size="sm" disabled={testing} onClick={testConnection}>{testing ? '测试中…' : '测试连接'}</Button>
            {testResult && (
              <span className={cn('text-[12px]', testResult.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500')}>
                {testResult.ok ? `✓ 连接成功（${testResult.latencyMs}ms）` : `✗ ${testResult.error ?? '连接失败'}`}
              </span>
            )}
          </div>
        </>
      )}

      {engine === 'opencode' && (
        <div className="space-y-2.5 rounded-lg border border-emerald-200 bg-emerald-50/50 p-3 dark:border-emerald-500/30 dark:bg-emerald-500/5">
          <div className="flex items-center gap-1.5 text-[12.5px] font-semibold text-emerald-700 dark:text-emerald-400">
            <Bot className="h-4 w-4" />opencode 客户端（直接写入 auth.json）
          </div>
          <p className="text-[11.5px] text-emerald-600/80 dark:text-emerald-400/80">opencode 的模型厂商由容器内 opencode 配置决定；如需配置 opencode 未收录的自定义模型厂商，请在下方选择「＋ 新增 Provider」并填写 provider 名与 API Key（写入 auth.json）。</p>
          {ocProviders.length > 0 ? (
            <div className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[11.5px] dark:border-slate-700 dark:bg-slate-900">
              <span className="text-slate-500 dark:text-slate-400">已配置 providers：</span>
              {ocProviders.map((p) => (
                <span key={p.provider} className={cn('mx-1 inline-block rounded px-1.5 py-0.5', p.hasKey ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400' : 'bg-amber-50 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400')}>
                  {p.provider}{p.hasKey ? ' ✓' : ' ⚠ 无 key'}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-400">暂未读取到 auth.json，请确认 opencode 已安装</p>
          )}
          <div className="space-y-1.5">
            <label className="text-[12.5px] font-medium text-slate-600 dark:text-slate-300">Provider <span className="text-red-500">*</span></label>
            {ocCustom ? (
              <Input placeholder="输入新 provider 名（如 anthropic / google / 自定义）" value={ocCustomName} onChange={(e) => setOcCustomName(e.target.value)} autoFocus />
            ) : (
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={ocProvider} onChange={(e) => {
                if (e.target.value === '__add_provider__') { setOcCustom(true); setOcProvider(''); return }
                setOcProvider(e.target.value)
                const sel = ocProviders.find((x) => x.provider === e.target.value)
                setOcBaseUrl(sel?.baseUrl || '')
                setOcModels((sel?.models || []).join(', '))
              }}>
                <option value="">选择 opencode provider…</option>
                {ocProviders.map((p) => <option key={p.provider} value={p.provider}>{p.provider}{p.hasKey ? '（已配置 Key）' : '（未配置 Key）'}</option>)}
                <option value="__add_provider__">＋ 新增 Provider…</option>
              </select>
            )}
            {ocCustom && (
              <button className="text-[11.5px] text-blue-500 hover:text-blue-600" onClick={() => { setOcCustom(false); setOcCustomName('') }}>← 返回选择已有 Provider</button>
            )}
          </div>
          <div className="space-y-1.5">
            <label className="text-[12.5px] font-medium text-slate-600 dark:text-slate-300">Base URL <span className="text-slate-400">（OpenAI 兼容端点，自定义 provider 必填；内置 provider 可留空）</span></label>
            <Input placeholder={ocProvider ? '未配置：使用 OpenCode 内置端点' : 'https://api.example.com/v1'} value={ocBaseUrl} onChange={(e) => setOcBaseUrl(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12.5px] font-medium text-slate-600 dark:text-slate-300">模型列表 <span className="text-slate-400">（逗号分隔；留空用 opencode 内置模型）</span></label>
            <Input placeholder={ocProvider ? '未配置：使用 OpenCode 运行时模型列表' : 'model-a, model-b'} value={ocModels} onChange={(e) => setOcModels(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label className="text-[12.5px] font-medium text-slate-600 dark:text-slate-300">API Key <span className="text-red-500">*</span></label>
            <Input type="password" placeholder="sk-..." value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
            <p className="text-xs text-slate-400">点击下方「保存」后，API Key 写入容器内 opencode 配置（auth.json），opencode 引擎立即可用。</p>
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">描述</label>
        <Input placeholder="工具用途说明" value={desc} onChange={(e) => setDesc(e.target.value)} />
      </div>
      <p className="text-xs leading-relaxed text-slate-400 dark:text-slate-500">
        {engine === 'opencode' ? 'opencode 客户端全局唯一，已配置则其他客户端可正常添加；点击保存直接写入容器内 opencode 配置。' : '工具配置完成后，在「创建 Agent」中选择该工具即可，Agent 无需再填模型与密钥。'}
      </p>
    </Shell>
  )
}

/* ============ 6. 发布校验（阻断） ============ */
function ValidateDialog() {
  const { closeDialog, locateCanvasNode, canvasTarget } = useApp()
  const [problems, setProblems] = useState<{ node_id: string; message: string }[]>([])
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [noDraft, setNoDraft] = useState(false)
  const [targetVersion, setTargetVersion] = useState<string>('')
  const [canvasData, setCanvasData] = useState<{ nodes: unknown[]; edges: [string, string][]; fallbacks: [string, string][] } | null>(null)

  /* 挂载：目标 = 最新草稿版本（与「保存草稿」落点一致）→ 读取画布 → 调后端校验。
     已发布版本只读，不参与发布校验；无草稿时提示先保存草稿。 */
  useEffect(() => {
    if (!canvasTarget) return
    let cancelled = false
    setBusy(true)
    setProblems([]); setChecked(false); setNoDraft(false); setTargetVersion(''); setCanvasData(null)
    const tplId = canvasTarget.templateId
    ;(async () => {
      try {
        const d = await api.get<{ items: { version: string; status: string }[] }>(`/api/v1/templates/${tplId}/versions`)
        const drafts = d.items.filter((i) => i.status === 'draft')
        if (!drafts.length) {
          if (!cancelled) { setNoDraft(true); setChecked(true) }
          return
        }
        const dv = drafts[0].version  // 局部变量，避免异步闭包读到旧 state
        if (cancelled) return
        setTargetVersion(dv)
        const canvas = await api.get<{ nodes: unknown[]; edges: [string, string][]; fallbacks: [string, string][] }>(
          `/api/v1/templates/${tplId}/versions/${dv}/canvas`,
        )
        if (cancelled) return
        setCanvasData({ nodes: canvas.nodes, edges: canvas.edges, fallbacks: canvas.fallbacks })
        const res = await api.post<{ ok: boolean; errors: { node_id: string; message: string }[] }>(
          `/api/v1/templates/${tplId}/versions/${dv}/canvas/validate`,
          { nodes: canvas.nodes, edges: canvas.edges, fallbacks: canvas.fallbacks },
        )
        if (!cancelled) { setProblems(res.errors); setChecked(true) }
      } catch (e) {
        if (!cancelled) { setChecked(true); toast.error(e instanceof ApiError ? e.message : '发布校验失败') }
      } finally {
        if (!cancelled) setBusy(false)
      }
    })()
    return () => { cancelled = true }
  }, [canvasTarget])

  const passed = checked && !busy && problems.length === 0

  const publish = async () => {
    if (!canvasTarget || !canvasData) return
    setPublishing(true)
    try {
      // 发布最新草稿：save-and-publish 自动创建新版本 → 保存画布 → 校验 → 发布。
      // 已发布版本只读不可发布；本弹框目标恒为最新草稿，不会触发"该版本已发布"
      const r = await api.post<{ version: string; status: string }>(
        `/api/v1/templates/${canvasTarget.templateId}/versions/save-and-publish`,
        canvasData,
      )
      closeDialog()
      toast.success(`已发布新版本 ${r.version}：静态校验通过，进入 published 状态`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  return (
    <Shell title={`发布校验 · ${canvasTarget?.templateName ?? ''} ${targetVersion || canvasTarget?.version || ''}（草稿）`} wide footer={<>
      <CancelBtn>关闭</CancelBtn>
      {busy ? (
        <Button disabled>校验中…</Button>
      ) : noDraft ? (
        <Button disabled title="请先保存草稿">发布（无草稿）</Button>
      ) : passed ? (
        <Button disabled={publishing} onClick={publish}>{publishing ? '发布中…' : '发布版本'}</Button>
      ) : (
        <Button disabled title="存在阻断问题">发布（被阻断）</Button>
      )}
    </>}>
      {!canvasTarget ? (
        <p className="text-sm text-slate-400">缺少目标版本信息，请从流程画布进入发布校验。</p>
      ) : busy ? (
        <p className="text-sm text-slate-400">正在执行发布校验（读取最新草稿画布 + 静态规则）…</p>
      ) : noDraft ? (
        <div className="flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-500/30 dark:bg-amber-500/10">
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-amber-500 font-bold text-white">!</span>
          <div>
            <div className="text-[13.5px] font-semibold text-amber-600 dark:text-amber-400">暂无草稿版本可发布</div>
            <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">已发布版本只读；请先在画布「进入编辑模式」修改后点「保存草稿」，再回来发布</div>
          </div>
        </div>
      ) : passed ? (
        <div className="flex items-center gap-3 rounded-xl border border-green-200 bg-green-50 p-4 dark:border-green-500/30 dark:bg-green-500/10">
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-green-500 font-bold text-white">✓</span>
          <div>
            <div className="text-[13.5px] font-semibold text-green-600 dark:text-green-400">校验通过：拓扑、连通性、回退目标、Agent 绑定均合法</div>
            <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">将发布「最新草稿 {targetVersion}」：点「发布版本」自动创建新版本并发布（已发布版本不会被覆盖）</div>
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-500/30 dark:bg-red-500/10">
            <span className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-red-500 font-bold text-white">✕</span>
            <div>
              <div className="text-[13.5px] font-semibold text-red-600 dark:text-red-400">校验未通过：{problems.length} 个阻断问题</div>
              <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">非法拓扑无法发布，需在画布修复后重新打开校验</div>
            </div>
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold text-slate-500">问题清单</div>
            <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
              {problems.map((p, i) => (
                <div key={i} className={cn('flex items-start gap-2.5 p-3', i < problems.length - 1 && 'border-b border-slate-100 dark:border-slate-800')}>
                  <Badge tone="err">阻断</Badge>
                  <div className="min-w-0 flex-1 text-[13px] text-slate-700 dark:text-slate-200">{p.message}</div>
                  <Button variant="outline" size="sm" disabled={!p.node_id || p.node_id === '—'} onClick={() => { closeDialog(); locateCanvasNode(p.node_id) }}>
                    定位
                  </Button>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </Shell>
  )
}

/* ============ 7. 通知重试 ============ */
function RetryNotifyDialog() {
  const { closeDialog } = useApp()
  const [busy, setBusy] = useState(false)

  const retry = async () => {
    setBusy(true)
    try {
      // 重试第一条失败通知（对齐通知中心真实数据）
      const n = await api.get<{ items: { id: string }[] }>('/api/v1/notifications?page_size=20')
      const failed = n.items.find((x) => x.id) ?? n.items[0]
      if (!failed) throw new ApiError(40401, '暂无失败通知', 404)
      const d = await api.post<{ retries: number }>(`/api/v1/notifications/${failed.id}/retry`)
      closeDialog()
      if (d.retries >= 2) toast('重试失败：渠道不可达（已记录审计）')
      else toast.success('已人工重试：企微通知发送成功，记录已更新')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '重试失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="通知重试 · 企微渠道" footer={<>
      <CancelBtn>关闭</CancelBtn>
      <Button disabled={busy} onClick={retry}>{busy ? '重试中…' : '立即重试'}</Button>
    </>}>
      <div className="kv-grid">
        <Kv k="通知事件" v="task_transferred" />
        <Kv k="目标" v="孙琳 · 测试组" />
        <Kv k="请求状态" v={<Badge tone="err">failed（第 2 次重试）</Badge>} />
        <Kv k="失败原因" v="企微接口 5xx · 超时 12s" />
        <Kv k="首次发送" v="08-16 16:12" />
        <Kv k="下次重试" v="自动退避 15 分钟后" />
      </div>
      <p className="text-xs leading-relaxed text-slate-400">
        通知是异步副作用：发送失败不回滚已成功的业务流转（PRD §11）。系统持久化请求/响应/重试次数/失败原因，可人工重试；单渠道故障不影响另一渠道与人工流程（PRD §15.1）。
      </p>
    </Shell>
  )
}

/* ============ 8. 注册本地账号 ============ */
function RegisterAccountDialog() {
  const { closeDialog } = useApp()
  const [busy, setBusy] = useState(false)
  const [account, setAccount] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [dept, setDept] = useState('售后服务部 / 深圳二部')
  const [password, setPassword] = useState('')

  const register = async () => {
    if (!account.trim() || !name.trim() || !email.trim() || !password) { toast('邮箱、用户名、姓名、密码为必填项'); return }
    setBusy(true)
    try {
      await api.post('/api/v1/auth/register', {
        account: account.trim(), name: name.trim(), email: email.trim(),
        dept, role_id: 'after_sales', password,
      })
      closeDialog()
      toast.success('注册申请已提交（待审批）：管理员将在 组织管理 → 注册审批 处理，通过后邮件+站内通知')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '注册失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="注册本地账号" footer={<>
      <CancelBtn />
      <Button disabled={busy} onClick={register}>{busy ? '提交中…' : '提交注册'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">邮箱 <span className="text-red-500">*</span></label>
        <Input placeholder="you@corp.cn（登录凭证，全局唯一）" value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">用户名 <span className="text-red-500">*</span></label>
        <Input placeholder="用于登录，组织内唯一" value={account} onChange={(e) => setAccount(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">姓名 <span className="text-red-500">*</span></label>
        <Input placeholder="显示姓名" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">部门</label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={dept} onChange={(e) => setDept(e.target.value)}>
          <option>售后服务部 / 深圳二部</option>
          <option>产品中心</option>
          <option>研发中心 / 前端组</option>
        </select>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">密码 <span className="text-red-500">*</span></label>
        <Input type="password" placeholder="≥10 位，含大小写字母与数字" value={password} onChange={(e) => setPassword(e.target.value)} />
        <p className="text-xs text-slate-400">密码策略：≥10 位（含大小写字母+数字）；有效期 90 天；不与最近 5 次密码相同（R-154）</p>
      </div>
      <p className="text-xs leading-relaxed text-slate-400">提交后进入待审批状态（invited），管理员审批通过后邮件+站内通知（R-152/R-157）。</p>
    </Shell>
  )
}

/* ============ 9. 首次登录强制改密 ============ */
function ChangePwdDialog() {
  const { closeDialog } = useApp()
  const [busy, setBusy] = useState(false)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')

  const change = async () => {
    if (!newPwd || newPwd !== confirmPwd) { toast('新密码与确认密码不一致'); return }
    setBusy(true)
    try {
      await api.post('/api/v1/auth/change-password', { old_password: oldPwd, new_password: newPwd })
      // 同步更新本地缓存的强制改密标记，避免下次登录（未刷新页面）再次弹出改密框
      const u = getStoredUser() as ({ mustChangePassword?: boolean } | null)
      if (u) {
        u.mustChangePassword = false
        setStoredUser(u)
      }
      closeDialog()
      toast.success('改密成功：must_change_password 已清除，进入工作台')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '改密失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="首次登录强制改密" footer={<>
      <CancelBtn>稍后</CancelBtn>
      <Button disabled={busy} onClick={change}>{busy ? '提交中…' : '确认改密'}</Button>
    </>}>
      <RiskTip>账号由管理员创建或重置密码，首次登录必须修改密码；未改密前仅可登录与改密（R-155）。</RiskTip>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">旧密码</label>
        <Input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">新密码 <span className="text-red-500">*</span></label>
        <Input type="password" placeholder="≥10 位，含大小写字母与数字" value={newPwd} onChange={(e) => setNewPwd(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">确认新密码 <span className="text-red-500">*</span></label>
        <Input type="password" value={confirmPwd} onChange={(e) => setConfirmPwd(e.target.value)} />
      </div>
    </Shell>
  )
}

/* ============ 10. 创建本地用户 ============ */
function CreateUserDialog() {
  const { closeDialog, bumpTask } = useApp()
  const [busy, setBusy] = useState(false)
  const [account, setAccount] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [dept, setDept] = useState('售后服务部 / 深圳二部')
  const [roleId, setRoleId] = useState('after_sales')
  const [password, setPassword] = useState('')

  const create = async () => {
    if (!account.trim() || !name.trim() || !password) { toast('用户名、姓名、密码为必填项'); return }
    setBusy(true)
    try {
      await api.post('/api/v1/org/users', {
        account: account.trim(), name: name.trim(), email,
        dept, role_id: roleId, skills: [], password,
      })
      closeDialog()
      bumpTask()
      toast.success('已创建本地用户：初始密码生效，邮件+站内通知已发送，首登强制改密')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell title="创建本地用户" footer={<>
      <CancelBtn />
      <Button disabled={busy} onClick={create}>{busy ? '创建中…' : '创建'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">用户名 <span className="text-red-500">*</span></label>
        <Input placeholder="登录用户名，组织内唯一" value={account} onChange={(e) => setAccount(e.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">姓名</label>
          <Input placeholder="显示姓名" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">邮箱</label>
          <Input placeholder="you@corp.cn" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">部门 / 角色 / 技能</label>
        <div className="grid grid-cols-3 gap-2">
          <select className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={dept} onChange={(e) => setDept(e.target.value)}>
            <option>售后服务部 / 深圳二部</option><option>研发后端</option><option>测试组</option>
          </select>
          <select className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={roleId} onChange={(e) => setRoleId(e.target.value)}>
            <option value="after_sales">after_sales</option><option value="developer">developer</option><option value="product_manager">product_manager</option>
          </select>
          <select className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200">
            <option>无技能</option><option>qa</option><option>backend</option>
          </select>
        </div>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">初始密码</label>
        <Input placeholder="系统生成或管理员指定" value={password} onChange={(e) => setPassword(e.target.value)} />
        <p className="text-xs text-slate-400">首次登录强制改密（R-155）；创建后邮件+站内通知（R-157）</p>
      </div>
    </Shell>
  )
}

/* ============ 11. 注册审批通过（单条 / 多选 / 全部） ============ */
function RegisterApproveDialog() {
  const { closeDialog, bumpTask, refreshOrgUsers, approvalTargetId } = useApp()
  const [busy, setBusy] = useState(false)
  const [list, setList] = useState<{ id: string; name: string; email: string; dept: string; role: string; applied: string }[]>([])
  const [synced, setSynced] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  /* 挂载时拉后端真实待审批（invited 用户），后端有数据则优先 */
  useEffect(() => {
    api.get<{ items: { id: string; name: string; email: string; dept: string; role: string }[] }>('/api/v1/auth/approvals')
      .then((d) => {
        if (d.items.length) {
          setList(d.items.map((r) => ({
            id: r.id, name: r.name, email: r.email, dept: r.dept, role: r.role, applied: '待审批',
          })))
          setSynced(true)
        }
      })
      .catch(() => { /* 保持演示数据 */ })
  }, [])

  /* 单条审批模式：仅显示目标用户；批量模式：显示全部 */
  const shown = approvalTargetId ? list.filter((r) => r.id === approvalTargetId) : list
  const title = approvalTargetId
    ? `审批申请 · ${shown[0]?.name ?? '—'}`
    : `注册审批（${list.length} 条待处理）`

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const toggleAll = () => {
    setSelected((prev) => (prev.size === shown.length ? new Set() : new Set(shown.map((r) => r.id))))
  }

  const approve = async (ids: string[]) => {
    if (!ids.length) return
    setBusy(true)
    try {
      const results = await Promise.allSettled(ids.map((id) => api.post(`/api/v1/auth/approvals/${id}/approve`)))
      const ok = results.filter((r) => r.status === 'fulfilled').length
      const fail = results.length - ok
      // 本地移除已通过的（失败项保留，可再次尝试）
      setList((prev) => prev.filter((r) => results[ids.indexOf(r.id)]?.status !== 'fulfilled'))
      setSelected(new Set())
      closeDialog()
      bumpTask()
      // 刷新用户管理列表（新审批的用户立即出现在组织管理 → 用户管理）
      refreshOrgUsers()
      if (fail === 0) {
        toast.success(`已审批通过 ${ok} 位申请者：创建 user + 本地凭证（同事务），邮件+站内通知申请者（R-152）`)
      } else {
        toast.warning(`审批完成：通过 ${ok} 位，失败 ${fail} 位（已通过的不受影响，可重试）`)
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '审批失败')
    } finally {
      setBusy(false)
    }
  }

  const approveSingle = () => { if (shown[0]) void approve([shown[0].id]) }
  const approveSelected = () => void approve([...selected])
  const approveAll = () => void approve(list.map((r) => r.id))

  return (
    <Shell title={title} footer={<>
      <CancelBtn>关闭</CancelBtn>
      {approvalTargetId ? (
        <Button disabled={busy || !shown.length} onClick={approveSingle}>{busy ? '审批中…' : '通过该申请'}</Button>
      ) : (
        <>
          <Button variant="outline" disabled={busy || !selected.size} onClick={approveSelected}>{busy ? '审批中…' : `通过选中（${selected.size}）`}</Button>
          <Button disabled={busy || !list.length} onClick={approveAll}>{busy ? '审批中…' : '全部通过'}</Button>
        </>
      )}
    </>}>
      <div className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800/60">
              {!approvalTargetId && (
                <th className="w-8 px-2 py-2">
                  <input type="checkbox" className="accent-blue-600" checked={shown.length > 0 && selected.size === shown.length} onChange={toggleAll} title="全选" />
                </th>
              )}
              <th className="px-3 py-2 font-medium">申请人</th><th className="px-3 py-2 font-medium">部门 / 角色</th><th className="px-3 py-2 font-medium">申请时间</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className="border-t border-slate-100 dark:border-slate-800">
                {!approvalTargetId && (
                  <td className="px-2 py-2 text-center">
                    <input type="checkbox" className="accent-blue-600" checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                  </td>
                )}
                <td className="px-3 py-2"><div className="font-medium text-slate-700 dark:text-slate-200">{r.name}</div><div className="text-[11px] text-slate-400">{r.email}</div></td>
                <td className="px-3 py-2 text-slate-500 dark:text-slate-400">{r.dept}<br /><span className="text-[11px]">{r.role}</span></td>
                <td className="px-3 py-2 text-slate-400">{r.applied}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400">共 {shown.length} 条待处理{synced ? '（后端真实数据）' : '（演示数据）'}；通过后创建 user + 本地凭证（同事务），失败自动回滚；部分失败可重试。</p>
    </Shell>
  )
}

/* ============ 12. 权限不足演示（五层交集拒绝） ============ */
function PermissionDemoDialog() {
  const scenarios: [string, string][] = [
    ['场景 A：孙磊（disabled）点「认领」', '功能权限 task:claim ✓ → 数据范围 ✓ → 项目成员 ✓ → 节点角色/技能（qa）✓ → 资源状态 ✗（disabled）→ 拒绝：账号已停用，任务需转办或管理员接管（PRD §3.2）'],
    ['场景 B：吴凡（非项目成员）打开 REQ-2026-0241', '功能权限 workflow_instance:read ✓ → 数据范围 ✗（不在 member_projects）→ 拒绝：无项目读取权限，返回 NOT_FOUND（R-83 隐藏资源存在性）'],
    ['场景 C：赵岩（无 qa 技能）点「认领」测试节点', '功能权限 task:claim ✓ → 数据范围 ✓ → 项目成员 ✓ → 节点角色/技能 ✗（节点要求 developer/qa，赵岩仅 backend）→ 拒绝：不在节点候选集（契约④ §9）'],
  ]
  return (
    <Shell title="权限不足演示 · 五层交集拒绝" wide footer={<CancelBtn>关闭</CancelBtn>}>
      <RiskTip>同一节点、同一动作，不同身份得到不同裁决 —— 裁决 = 功能权限 ∩ 数据范围 ∩ 项目成员 ∩ 节点角色/技能 ∩ 资源状态（契约② R5）。</RiskTip>
      <div className="space-y-3">
        {scenarios.map(([t, d]) => (
          <div key={t} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
            <div className="mb-1 text-[12.5px] font-semibold text-slate-700 dark:text-slate-200">{t}</div>
            <div className="text-xs leading-relaxed text-slate-500 dark:text-slate-400">{d}</div>
          </div>
        ))}
      </div>
    </Shell>
  )
}

/* ============ 13. 停用用户任务接管 ============ */
function UserTakeoverDialog() {
  const { closeDialog } = useApp()
  /* 停用用户 + 候选接管人：来自后端（disabled 用户及其未完成任务，接管人为 active 用户） */
  const [disabledUsers, setDisabledUsers] = useState<{ id: string; name: string; dept: string }[]>([])
  const [takeoverUsers, setTakeoverUsers] = useState<{ id: string; name: string; dept: string; skills: string[]; load?: number }[]>([])
  useEffect(() => {
    api.get<{ items: { id: string; name: string; dept: string; status: string; skills: string[]; load?: number }[] }>('/api/v1/org/users')
      .then((d) => {
        setDisabledUsers(d.items.filter((u) => u.status === 'disabled'))
        setTakeoverUsers(d.items.filter((u) => u.status === 'active'))
      })
      .catch(() => {})
  }, [])
  const [target, setTarget] = useState('')
  const targetUser = takeoverUsers.find((u) => u.id === target)
  return (
    <Shell title="停用用户 · 任务接管" footer={<>
      <CancelBtn>稍后处理</CancelBtn>
      <Button disabled={!target} onClick={() => { closeDialog(); toast.success(`已接管：${disabledUsers[0]?.name ?? '停用用户'} 的任务转给 ${targetUser?.name ?? ''}，原处理人历史保留（PRD §3.2）`) }}>确认接管</Button>
    </>}>
      <RiskTip>外部平台停用用户时，本地用户置为 disabled，已有任务必须转办或管理员接管（PRD §3.2）。</RiskTip>
      <div className="space-y-2">
        {disabledUsers.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 p-3 text-center text-[12px] text-slate-400 dark:border-slate-700">当前无停用用户</div>
        ) : disabledUsers.map((u) => (
          <div key={u.id} className="flex items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/60">
            <Avatar name={u.name} grad="g6" size={32} />
            <div className="flex-1">
              <div className="text-[13px] font-medium text-slate-700 dark:text-slate-200">{u.name} · 已停用（disabled）</div>
              <div className="text-[11.5px] text-slate-400">{u.dept || '未分配部门'} · 未完成任务待接管</div>
            </div>
          </div>
        ))}
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">接管人</label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">请选择接管人…</option>
          {takeoverUsers.map((u) => (
            <option key={u.id} value={u.id}>{u.name}（{u.dept || '未分配部门'} · 负载 {u.load ?? 0}）</option>
          ))}
        </select>
        <p className="text-xs text-slate-400">接管后生成转办事件并通知，原历史不可修改。</p>
      </div>
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

  /* 项目 + 模板池：来自后端 */
  useEffect(() => {
    api.get<{ items: { id: string; name: string; code: string; status: string; templateBindings: { templateId: string; name: string; type: string; version: string; status: string }[] }[] }>('/api/v1/projects').then((d) => setProjects(d.items)).catch(() => {})
    api.get<{ items: { id: string; name: string; type: string; versions: string[]; startSchema: unknown[] }[] }>('/api/v1/templates/pool').then((d) => setTplPool(d.items)).catch(() => {})
  }, [])

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
      await api.post('/api/v1/work-items', { project_id: projId, template_id: templateId, start_values: values })
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

/* ============ 15. Agent 详情（调用历史 + 确认请求） ============ */
function AgentDetailDialog() {
  const { dialogAgentId } = useApp()
  const [invocations, setInvocations] = useState<{ id: string; capability: string; mode: string; status: string; elapsedMs: number; action: string; authorizedUser: string; createdAt: string; error: string }[]>([])
  const [tab, setTab] = useState<'inv' | 'cf'>('inv')
  const [confirms, setConfirms] = useState<{ id: string; capability: string; status: string; authorizedUser: string; createdAt: string; expireAt: string }[]>([])

  useEffect(() => {
    if (!dialogAgentId) return
    api.get<{ items: typeof invocations }>(`/api/v1/agents/${dialogAgentId}/invocations?page_size=20`)
      .then((d) => setInvocations(d.items))
      .catch(() => {})
    api.get<{ items: typeof confirms }>(`/api/v1/agents/confirm-requests?status=all&page_size=20`)
      .then((d) => setConfirms(d.items.filter((r) => r.id)))
      .catch(() => {})
  }, [dialogAgentId])

  return (
    <Shell title="Agent 详情 · 调用历史" wide footer={<CancelBtn>关闭</CancelBtn>}>
      <div className="mb-3 flex gap-2">
        {([['inv', '调用记录'], ['cf', '确认请求']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
              tab === k ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 hover:text-blue-600 dark:bg-slate-800 dark:text-slate-400')}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'inv' ? (
        invocations.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 p-5 text-center text-[12px] text-slate-400 dark:border-slate-700">暂无调用记录（Agent 激活并绑定节点后，调用会在此留痕）</div>
        ) : (
          <div className="space-y-2">
            {invocations.map((i) => (
              <div key={i.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-2.5 text-[12px] dark:border-slate-700">
                <Badge tone={i.status === 'success' ? 'suc' : i.status === 'draft' ? 'warn' : i.status === 'failed' ? 'err' : 'gry'}>{i.status}</Badge>
                <span className="font-medium text-slate-700 dark:text-slate-200">{i.capability}</span>
                <span className="text-slate-400">{i.action || '—'}</span>
                <span className="ml-auto text-slate-400">{i.elapsedMs ? `${(i.elapsedMs / 1000).toFixed(1)}s` : '—'} · {i.createdAt}</span>
              </div>
            ))}
          </div>
        )
      ) : (
        confirms.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 p-5 text-center text-[12px] text-slate-400 dark:border-slate-700">暂无确认请求记录</div>
        ) : (
          <div className="space-y-2">
            {confirms.map((c) => (
              <div key={c.id} className="flex items-center gap-3 rounded-lg border border-slate-200 p-2.5 text-[12px] dark:border-slate-700">
                <Badge tone={c.status === 'approved' ? 'suc' : c.status === 'rejected' ? 'gry' : c.status === 'expired' ? 'err' : 'warn'}>{c.status}</Badge>
                <span className="font-medium text-slate-700 dark:text-slate-200">{c.capability}</span>
                <span className="text-slate-400">授权 {c.authorizedUser}</span>
                <span className="ml-auto text-slate-400">{c.createdAt}</span>
              </div>
            ))}
          </div>
        )
      )}
    </Shell>
  )
}

function AgentEditDialog() {
  const { closeDialog, bumpTask, dialogAgentId } = useApp()
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [name, setName] = useState('')
  const [agentType, setAgentType] = useState('')
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [providers, setProviders] = useState<{ id: string; provider: string; engine: string; models: string[]; status: string }[]>([])
  const [types, setTypes] = useState<{ code: string; label: string; systemPrompt: string; provider: string; model: string }[]>([])

  useEffect(() => {
    if (!dialogAgentId) return
    Promise.all([
      api.get<{ agent: { id: string; name: string; provider: string; model: string; agentType: string; systemPrompt: string } }>(`/api/v1/agents/${dialogAgentId}`),
      api.get<{ items: typeof providers }>('/api/v1/agents/tools'),
      api.get<{ items: typeof types }>('/api/v1/agents/types'),
    ]).then(([agentData, providerData, typeData]) => {
      setProviders(providerData.items.filter((item) => item.status === 'active'))
      setTypes(typeData.items)
      const current = agentData.agent
      if (current) {
        setName(current.name)
        setAgentType(current.agentType || '')
        setProvider(current.provider || '')
        setModel(current.model || '')
        setSystemPrompt(current.systemPrompt || '')
      }
      setLoaded(true)
    }).catch(() => {
      setLoaded(true)
      toast.error('加载 Agent 配置失败')
    })
  }, [dialogAgentId])

  const selectedProvider = providers.find((item) => item.provider === provider)
  const availableModels = selectedProvider?.models || []

  const selectType = (code: string) => {
    setAgentType(code)
    const type = types.find((item) => item.code === code)
    if (!type) return
    setSystemPrompt(type.systemPrompt || systemPrompt)
    if (type.provider) setProvider(type.provider)
    if (type.model) setModel(type.model)
  }

  const save = async () => {
    if (!dialogAgentId || !name.trim() || !provider || !model) {
      toast('名称、Provider、模型均为必填项')
      return
    }
    setBusy(true)
    try {
      await api.put(`/api/v1/agents/${dialogAgentId}`, {
        name: name.trim(), agent_type: agentType, provider, model, system_prompt: systemPrompt.trim(),
      })
      bumpTask()
      closeDialog()
      toast.success('Agent 配置已更新')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '更新失败')
    } finally {
      setBusy(false)
    }
  }

  if (!loaded) {
    return (
      <Shell title="编辑 Agent" wide footer={<CancelBtn />}>
        <div className="flex min-h-48 items-center justify-center text-sm text-slate-400">
          正在加载 Agent 配置…
        </div>
      </Shell>
    )
  }

  return (
    <Shell title="编辑 Agent" wide footer={<>
      <CancelBtn />
      <Button disabled={busy || !loaded} onClick={save}>{busy ? '保存中…' : '保存'}</Button>
    </>}>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Agent 名称 <span className="text-red-500">*</span></label>
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">Provider <span className="text-red-500">*</span></label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={provider} onChange={(e) => { setProvider(e.target.value); setModel('') }}>
          <option value="">选择 Provider…（在「Provider 管理」Tab 中预先配置）</option>
          {providers.map((item) => <option key={item.id} value={item.provider}>{item.provider}（{item.engine === 'api' ? 'API 直连' : 'CLI 引擎'}）</option>)}
        </select>
        {selectedProvider && <div className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2 text-[11.5px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
          {selectedProvider.engine === 'api' ? `API Provider：${selectedProvider.provider}，执行时使用该 Provider 的 API Key` : `${selectedProvider.provider} OpenCode Provider：使用平台配置的模型执行`}
        </div>}
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">模型 <span className="text-red-500">*</span></label>
        <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">选择 {provider || 'Provider'} 下的模型…</option>
          {availableModels.map((item) => <option key={item} value={item}>{item}</option>)}
          {model && !availableModels.includes(model) && <option value={model}>{model}（当前值）</option>}
        </select>
      </div>
      <div>
        <div className="mb-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-300">Agent 类型 <span className="text-slate-400">（可选，带出系统提示词模板）</span></div>
        <div className="grid grid-cols-2 gap-2">
          {types.map((type) => {
            const active = agentType === type.code
            return (
              <button key={type.code} type="button"
                className={cn('rounded-lg border p-2.5 text-left transition-colors',
                  active ? 'border-violet-500 bg-violet-50/60 ring-1 ring-violet-500 dark:bg-violet-500/10' : 'border-slate-200 hover:border-violet-300 dark:border-slate-700')}
                onClick={() => active ? setAgentType('') : selectType(type.code)}>
                <div className="flex items-center gap-1.5">
                  <span className="text-base leading-none">{TYPE_ICON[type.code] ?? '🤖'}</span>
                  <span className="text-[12.5px] font-semibold text-slate-700 dark:text-slate-200">{type.label}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-[11px] leading-snug text-slate-400">{type.systemPrompt || '未配置系统提示词模板'}</p>
                {type.provider && type.model && <span className="mt-1.5 inline-block rounded bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-600 dark:bg-violet-500/10 dark:text-violet-300">{type.provider} · {type.model}</span>}
              </button>
            )
          })}
        </div>
      </div>
      <div className="space-y-1.5">
        <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">系统提示词</label>
        <Textarea rows={7} value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} />
      </div>
    </Shell>
  )
}

/* ============ 注册表 ============ */
const DIALOGS: Record<string, () => React.ReactElement> = {
  submit: SubmitDialog,
  return: ReturnDialog,
  transfer: TransferDialog,
  agentConfirm: AgentConfirmDialog,
  agentCreate: AgentCreateDialog,
  validate: ValidateDialog,
  retryNotify: RetryNotifyDialog,
  registerAccount: RegisterAccountDialog,
  changePwd: ChangePwdDialog,
  createUser: CreateUserDialog,
  registerApprove: RegisterApproveDialog,
  permissionDemo: PermissionDemoDialog,
  userTakeover: UserTakeoverDialog,
  agentDetail: AgentDetailDialog,
  agentEdit: AgentEditDialog,
  toolEdit: ToolEditDialog,
}

export function DialogHost() {
  const { dialog } = useApp()
  if (!dialog) return null
  const Comp = DIALOGS[dialog]
  return Comp ? <Comp /> : null
}
