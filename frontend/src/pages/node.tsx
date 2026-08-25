import { useEffect, useState } from 'react'
import {
  ArrowLeft, ArrowRight, Bot, FileText, Info, Users, Plus,
  Send, Undo2, UserPlus, PauseCircle, ShieldAlert, ChevronRight,
} from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import {
  Badge, FlowSteps, SectionCard, Timeline, priorityBadge,
} from '../components/common'
import { SchemaForm, validateSchema, type SchemaValues } from '../components/schema-form'
import { api, ApiError } from '../lib/api'
import { cn } from '../lib/utils'
import type { FormField, TaskItem, WorkItem } from '../types'

/* ---------- Agent 能力边界默认（后端无数据时兜底展示） ---------- */
const CAPABILITY_DEFAULT: { name: string; mode: string }[] = [
  { name: 'read_context', mode: 'direct' }, { name: 'read_documents', mode: 'direct' }, { name: 'read_history', mode: 'direct' },
  { name: 'generate_content', mode: 'confirm' }, { name: 'write_form', mode: 'confirm' }, { name: 'append_form', mode: 'confirm' },
  { name: 'upload_document', mode: 'confirm' }, { name: 'create_subtask', mode: 'confirm' },
  { name: 'submit_task', mode: 'forbid' }, { name: 'return_task', mode: 'forbid' }, { name: 'transfer_task', mode: 'forbid' },
  { name: 'pause_workflow', mode: 'forbid' }, { name: 'resume_workflow', mode: 'forbid' }, { name: 'close_work_item', mode: 'forbid' },
]

interface CanvasNodeLite { id: string; label: string; type?: string; cfg?: { schema?: never[] } }

function fieldLabel(schema: FormField[] | undefined, key: string): string {
  return schema?.find((field) => field.key === key)?.label ?? key
}

export function NodeProcessPage() {
  const { navigate, openDialog, openTask, openWorkItem, activeTaskId, currentUser } = useApp()
  const [formValues, setFormValues] = useState<SchemaValues>({})
  /* 真实数据：当前任务 → 所属工作项 → 流程实例 → 模板画布 */
  const [task, setTask] = useState<TaskItem | null>(null)
  const [wi, setWi] = useState<WorkItem | null>(null)
  const [instance, setInstance] = useState<{ id: string; templateId: string; version: string; currentNode: string; state: string } | null>(null)
  const [startValues, setStartValues] = useState<Record<string, unknown>>({})
  const [candidates, setCandidates] = useState<{ name: string; dept?: string }[]>([])
  const [curSchema, setCurSchema] = useState<never[]>([])
  const [curNodeType, setCurNodeType] = useState<string>('')
  const [agentCaps, setAgentCaps] = useState(CAPABILITY_DEFAULT)
  const [flowSteps, setFlowSteps] = useState<{ name: string; status: string; assignee: string; time: string }[]>([])
  const [timeline, setTimeline] = useState<{ id: string; time: string; title: string; desc: string; by: string; kind: string }[]>([])
  const [suggestions, setSuggestions] = useState<{ id: string; title: string; body: string; status: string; time: string; agentId: string }[]>([])
  /* 继承上下文：起始表单 + 已执行前序节点表单（来自 GET /tasks/{id} upstream）；按节点折叠、默认收起 */
  type UpstreamSeg = {
    node: string; task_id?: string; values: Record<string, unknown>; assignee?: string
    schema?: FormField[]; appends?: { id: string; appender: string; time: string; values: Record<string, unknown> }[]
  }
  const [upstream, setUpstream] = useState<UpstreamSeg[]>([])
  /* 前序节点补充信息弹窗 */
  const [appendFor, setAppendFor] = useState<{ taskId: string; node: string; schema: FormField[] } | null>(null)
  const [appendValues, setAppendValues] = useState<SchemaValues>({})
  const [appendBusy, setAppendBusy] = useState(false)
  const [expandedUp, setExpandedUp] = useState<Set<number>>(new Set())
  const toggleUp = (idx: number) => setExpandedUp((prev) => {
    const next = new Set(prev)
    if (next.has(idx)) next.delete(idx)
    else next.add(idx)
    return next
  })
  const [loading, setLoading] = useState(true)

  /* 挂载：任务详情(+Agent 建议 + 继承上下文) → 工作项详情(实例/时间线) → 模板画布(流程步骤 + 当前节点表单) + 候选处理人 */
  useEffect(() => {
    if (!activeTaskId) { navigate('tasks'); return }
    let curNodeId = ''
    let instRef: typeof instance = null
    api.get<{ task: TaskItem & { nodeId?: string }; upstream?: typeof upstream; suggestions?: typeof suggestions }>(`/api/v1/tasks/${activeTaskId}`)
      .then((td) => {
        setTask(td.task)
        curNodeId = td.task.nodeId ?? ''
        if (td.upstream?.length) setUpstream(td.upstream)
        else setUpstream([])
        if (td.suggestions?.length) setSuggestions(td.suggestions)
        return api.get<{ item: WorkItem; instance: typeof instance; startValues: Record<string, unknown>; tasks: { id: string; node: string; status: string; assignee: string; due: string }[] }>(`/api/v1/work-items/${td.task.wiId}`)
      })
      .then((wd) => {
        instRef = wd.instance
        setWi(wd.item); setInstance(wd.instance); setStartValues(wd.startValues ?? {})
        setTimeline(wd.tasks.map((t) => ({
          id: t.id, time: t.due, title: `节点「${t.node}」`, desc: `任务 ${t.id} · 处理人 ${t.assignee} · ${t.status}`, by: t.assignee || '系统', kind: 'user',
        })))
        if (!wd.instance) return null
        return api.get<{ nodes: CanvasNodeLite[] }>(`/api/v1/templates/${wd.instance.templateId}/versions/${wd.instance.version}/canvas`)
      })
      .then((canvas) => {
        if (canvas) {
          // 流程步骤：以【流程实例真实位置】为准（而非当前打开的任务节点）——
          // 仅 closed（流程真正结束）→ 全部完成；archived（归档冻结）保持冻结时的真实进度，
          // 按实例 current_node 显示，不因归档把进度改成"全部完成"
          const closed = instRef?.state === 'closed'
          const progressId = closed ? '' : (instRef?.currentNode ?? curNodeId)
          const curIdx = closed ? canvas.nodes.length : canvas.nodes.findIndex((n) => n.id === progressId)
          setFlowSteps(canvas.nodes.map((n, i) => ({
            name: n.label,
            status: closed || i < curIdx ? 'done' : i === curIdx ? 'current' : 'wait',
            assignee: '', time: '',
          })))
          // 当前节点表单 Schema（与画布节点一致）
          const cur = canvas.nodes.find((n) => n.id === curNodeId) ?? canvas.nodes.find((n) => n.type === 'task')
          if (cur?.cfg?.schema) setCurSchema(cur.cfg.schema)
          setCurNodeType(cur?.type ?? '')
        }
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '任务数据加载失败'))
      .finally(() => setLoading(false))
    api.get<{ users: { name: string; dept?: string }[] }>(`/api/v1/tasks/${activeTaskId}/candidates`)
      .then((d) => setCandidates(d.users))
      .catch(() => {})
    api.get<{ items: { name: string; mode: string }[] }>('/api/v1/agents/capabilities').then((d) => { if (d.items.length) setAgentCaps(d.items) }).catch(() => {})
  }, [activeTaskId, navigate])

  const submitTask = async () => {
    if (!activeTaskId) { toast.error('暂无真实任务可提交（请先新建工作项）'); return }
    try {
      // 起始节点任务：表单已在发起时提交，提交时携带起始表单值，无需重填
      const form = curNodeType === 'start' ? startValues : formValues
      const d = await api.post<{
        next_node?: { label?: string } | null; next_assignees?: { name: string }[]
        next_task_id?: string | null; next_tasks?: { id: string; node: string }[]
        parallel?: boolean; waiting_join?: boolean; closed?: boolean
      }>(`/api/v1/tasks/${activeTaskId}/actions`, { action: 'submit', form_values: form })
      const next = d.next_node?.label
      const names = (d.next_assignees ?? []).map((a) => a.name).join('、')
      if (d.parallel) {
        toast.success(`提交成功：已并行拆分 ${d.next_tasks?.length ?? 0} 个分支任务（${d.next_tasks?.map((x) => x.node).join('、')}）`)
        if (d.next_tasks?.length) openTask(d.next_tasks[0].id, task?.wiId)
        else if (task?.wiId) openWorkItem(task.wiId)
      } else if (d.waiting_join) {
        toast.success('提交成功：等待其他并行分支完成后自动汇合到「' + (next ?? '下一节点') + '」')
        if (task?.wiId) openWorkItem(task.wiId)
      } else if (d.next_task_id && !d.closed) {
        toast.success(`提交成功：已自动流转至「${next ?? '下一节点'}」${names ? `→ 候选处理人：${names}` : ''}（绑定解析分配）`)
        openTask(d.next_task_id, task?.wiId)
      } else if (task?.wiId) {
        toast.success(`提交成功：流程已全部完成${next ? `（${next}）` : ''}`)
        openWorkItem(task.wiId)
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '提交失败')
    }
  }

  const requestInfo = async () => {
    if (!activeTaskId) { toast.error('暂无任务可请求补充信息'); return }
    try {
      await api.post(`/api/v1/tasks/${activeTaskId}/actions`, { action: 'request_info' })
      toast.success('补充信息请求已发送：SLA 暂停计时')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '请求失败')
    }
  }

  /* 前序节点补充信息：原处理人按该节点表单 schema 结构化追加，提交后刷新继承上下文 */
  const submitAppend = async () => {
    if (!appendFor) return
    const missing = validateSchema(appendFor.schema, appendValues)
    if (missing.length) { toast.error(`请填写：${missing.join('、')}`); return }
    try {
      setAppendBusy(true)
      await api.post(`/api/v1/tasks/${appendFor.taskId}/appends`, { values: appendValues })
      toast.success(`已补充节点「${appendFor.node}」信息（原提交不变，已通知当前节点处理人）`)
      setAppendFor(null); setAppendValues({})
      if (activeTaskId) {
        const td = await api.get<{ upstream?: UpstreamSeg[] }>(`/api/v1/tasks/${activeTaskId}`)
        if (td.upstream?.length) setUpstream(td.upstream)
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '补充失败')
    } finally { setAppendBusy(false) }
  }

  /* 动作可用性说明（不可用动作说明原因而非静默隐藏）；归档冻结任务全部只读 */
  const actions = task?.frozen
    ? [
        { label: '提交', icon: <Send className="h-4 w-4" />, disabled: true, reason: '项目已归档，流程已冻结（只读）', onClick: () => {} },
        { label: '退回', icon: <Undo2 className="h-4 w-4" />, disabled: true, reason: '项目已归档，流程已冻结（只读）', onClick: () => {} },
        { label: '转办', icon: <ArrowRight className="h-4 w-4" />, disabled: true, reason: '项目已归档，流程已冻结（只读）', onClick: () => {} },
      ]
    : [
        { label: '认领', icon: <UserPlus className="h-4 w-4" />, disabled: true, reason: `已认领（${task?.assignee || '—'}）`, onClick: () => {} },
        { label: '提交', icon: <Send className="h-4 w-4" />, tone: 'primary' as const, onClick: submitTask },
        { label: '退回', icon: <Undo2 className="h-4 w-4" />, tone: 'danger' as const, onClick: () => openDialog('return') },
        { label: '转办', icon: <ArrowRight className="h-4 w-4" />, onClick: () => openDialog('transfer') },
        { label: '暂停流程', icon: <PauseCircle className="h-4 w-4" />, disabled: true, reason: '仅项目管理员可暂停', onClick: () => {} },
      ]

  return (
    <div className="page-container">
      <button className="mb-4 flex items-center gap-1.5 text-[13px] font-medium text-slate-500 transition-colors hover:text-blue-600" onClick={() => (wi ? openWorkItem(wi.id) : navigate('tasks'))}>
        <ArrowLeft className="h-4 w-4" />{wi ? '返回工作项' : '返回任务'}
      </button>

      {/* 顶栏信息（真实任务数据） */}
      <div className="mb-5 flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400"><FileText className="h-5 w-5" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={task?.type === 'issue' ? 'warn' : 'info'}>{task?.type === 'issue' ? '问题' : '需求'}</Badge>
            <span className="font-mono text-[11.5px] text-slate-400">{task?.wiId ?? '加载中…'}</span>
            <span className="text-[14px] font-medium text-slate-800 dark:text-slate-100">{task?.title ?? '加载中…'}</span>
            {task && priorityBadge(task.priority)}
          </div>
          <div className="mt-1 text-[12px] text-slate-400">
            {loading ? '加载任务数据…' : `当前节点：${task?.node ?? '—'} · 处理人：${task?.assignee ?? '—'} · SLA ${task?.slaHours ?? '—'}h${task?.overdue ? ' · 已超时' : ''}`}
          </div>
        </div>
        <div className="flex flex-none items-center gap-2">
          <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-[12.5px] font-medium text-slate-600 transition-colors hover:border-amber-400 hover:text-amber-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={requestInfo}>
            <Info className="h-4 w-4" />请求补充信息
          </button>
          <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-[12.5px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
            onClick={() => openDialog('agentConfirm')}>
            <Bot className="h-4 w-4" />Agent 待确认
            <span className="rounded-full bg-violet-100 px-1.5 text-[10.5px] font-semibold text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">{suggestions.filter((s) => s.status !== 'applied' && s.status !== 'rejected').length}</span>
          </button>
        </div>
      </div>

      {/* 节点处理人（真实候选） */}
      <div className="mb-5 rounded-xl border border-blue-200 bg-blue-50/60 p-4 text-[12px] leading-relaxed text-slate-600 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-slate-300">
        <div className="mb-1 flex items-center gap-1.5 font-semibold text-blue-700 dark:text-blue-300">
          <Users className="h-4 w-4" />节点处理人绑定（{wi?.project || '—'} × 流程 {instance ? `v${instance.version.replace(/\D/g, '')}` : '—'}）
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span>
            本节点候选处理人：
            <b className="text-slate-800 dark:text-slate-100">
              {candidates.length ? candidates.map((c) => c.name).join('、') : '未绑定（需手动指定）'}
            </b>
            {candidates.length > 0 && <span className="ml-1 text-slate-400">（后端绑定解析）</span>}
          </span>
          <span className="text-slate-400">可绑定多个用户或角色；角色解析为所在在职用户（PRD §4.5）</span>
        </div>
      </div>

      {/* 三栏主体 */}
      <div className="grid gap-5 lg:grid-cols-[240px_1fr_340px]">
        {/* 左栏：流程进度 */}
        <SectionCard title="流程进度" bodyClassName="p-4" className="self-start">
          <FlowSteps nodes={flowSteps as never[]} />
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[11.5px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
            <b className="mb-1 block">回退目标</b>退回须从模板允许目标中选择（PRD §6.2）
          </div>
        </SectionCard>

        {/* 中栏：表单 + 继承上下文 + Agent 结果 */}
        <div className="space-y-5">
          {curNodeType === 'start' ? (
            /* 起始节点：表单已在发起时提交，只读回显，不重复要求填写 */
            <SectionCard title={`${task?.node ?? '起始节点'} 表单`} extra={<Badge tone="suc">发起时已提交</Badge>}>
              <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3.5 text-[12px] leading-relaxed text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300">
                起始表单已在<b>新建工作项</b>时提交，无需重复填写；确认无误后可直接「提交」流转到下一节点。
              </div>
              <div className="mt-3 space-y-2">
                {Object.entries(startValues).filter(([, v]) => v).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                    <div className="text-[12px] font-medium text-slate-600 dark:text-slate-300">{k}</div>
                    <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{String(v)}</p>
                  </div>
                ))}
                {Object.keys(startValues).length === 0 && (
                  <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                    无起始表单数据
                  </div>
                )}
              </div>
            </SectionCard>
          ) : (
            <SectionCard title={`${task?.node ?? '当前节点'} 表单`} extra={<Badge tone="cyn">Schema 驱动 · 与画布节点一致</Badge>}>
              <SchemaForm fields={curSchema} values={formValues} onChange={setFormValues} workItemId={task?.wiId} project={task?.project} />
              {curSchema.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                  该节点未配置表单字段
                </div>
              )}
            </SectionCard>
          )}

          {/* 继承上下文：已执行节点（含起始节点）表单，按节点折叠、默认收起 */}
          {curNodeType !== 'start' && (
            <SectionCard title="继承上下文" extra={
              <button className="text-[11px] font-medium text-blue-600 hover:underline" onClick={() => setExpandedUp(new Set(upstream.length ? [...Array(upstream.length).keys()] : []))}>
                {expandedUp.size === upstream.length && upstream.length > 0 ? '全部收起' : '全部展开'}
              </button>}>
              <div className="space-y-2">
                {upstream.length > 0 ? (
                  upstream.map((seg, idx) => {
                    const fieldCount = Object.entries(seg.values ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== '').length
                    const appendCount = seg.appends?.length ?? 0
                    const open = expandedUp.has(idx)
                    /* 补充权限：仅该节点原处理人 / 系统管理员（前序节点已完成后可追加） */
                    const canAppend = !!seg.task_id && (seg.assignee === currentUser?.name || currentUser?.name === '系统管理员')
                    return (
                      <div key={`${seg.node}-${idx}`} className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                        <button
                          className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/60"
                          onClick={() => toggleUp(idx)}>
                          <span className="flex min-w-0 items-center gap-2">
                            <ChevronRight className={cn('h-4 w-4 flex-none text-slate-400 transition-transform', open && 'rotate-90')} />
                            <span className="text-[12px] font-semibold text-slate-600 dark:text-slate-300">{seg.node}</span>
                            <span className="text-[10.5px] text-slate-400">{fieldCount} 项字段{appendCount > 0 ? ` · ${appendCount} 条补充` : ''}</span>
                          </span>
                          <Badge tone="gry">已执行</Badge>
                        </button>
                        {open && (
                          <div className="space-y-1 border-t border-slate-100 px-3 py-2.5 dark:border-slate-800">
                            {Object.entries(seg.values ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => (
                              <div key={k} className="flex gap-2 text-[12px] leading-relaxed">
                                <span className="w-24 flex-none text-slate-400">{fieldLabel(seg.schema, k)}</span>
                                <span className="min-w-0 break-all text-slate-600 dark:text-slate-300">{Array.isArray(v) ? v.join('、') : String(v)}</span>
                              </div>
                            ))}
                            {/* 追加记录：原处理人补充的信息，独立留痕 */}
                            {(seg.appends ?? []).map((ap) => (
                              <div key={ap.id} className="mt-1.5 rounded-lg border border-blue-100 bg-blue-50/50 p-2.5 dark:border-blue-500/20 dark:bg-blue-500/5">
                                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-blue-600 dark:text-blue-300">
                                  <Plus className="h-3 w-3" />补充 · {ap.appender} · {ap.time}
                                </div>
                                <div className="mt-1 space-y-0.5">
                                  {Object.entries(ap.values ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== '').map(([k, v]) => (
                                    <div key={k} className="flex gap-2 text-[11.5px] leading-relaxed">
                                      <span className="w-24 flex-none text-slate-400">{fieldLabel(seg.schema, k)}</span>
                                      <span className="min-w-0 break-all text-slate-600 dark:text-slate-300">{Array.isArray(v) ? v.join('、') : String(v)}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ))}
                            {/* 补充入口：仅原处理人/管理员可见 */}
                            {canAppend && (
                              <button
                                className="mt-2 flex items-center gap-1 rounded-lg border border-blue-200 px-2.5 py-1 text-[11px] font-medium text-blue-600 transition-colors hover:bg-blue-50 dark:border-blue-500/30 dark:text-blue-300 dark:hover:bg-blue-500/10"
                                onClick={() => { setAppendFor({ taskId: seg.task_id!, node: seg.node, schema: seg.schema ?? [] }); setAppendValues({}) }}>
                                <Plus className="h-3 w-3" />补充信息
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })
                ) : (
                  <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                    暂无继承表单数据
                  </div>
                )}
              </div>
            </SectionCard>
          )}

          {/* Agent 结果 */}
          <SectionCard title="Agent 结果" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => openDialog('agentConfirm')}>Agent 确认</button>}>
            <div className="space-y-3">
              {suggestions.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                  暂无 Agent 产出（可在 Agent 管理注册并激活后，在本节点调用 Agent 生成建议）
                </div>
              )}
              {suggestions.map((s) => (
                <div key={s.id} className="rounded-lg border border-violet-100 bg-violet-50/50 p-3.5 dark:border-violet-500/20 dark:bg-violet-500/5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-violet-700 dark:text-violet-300">
                      <Bot className="h-4 w-4" />{s.title}
                    </span>
                    <Badge tone={s.status === 'applied' ? 'suc' : s.status === 'rejected' ? 'gry' : 'pur'}>
                      {s.status === 'suggestion' ? '建议（未执行）' : s.status === 'applied' ? '已应用' : s.status === 'rejected' ? '已忽略' : s.status}
                    </Badge>
                  </div>
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-slate-600 dark:text-slate-300">{s.body}</p>
                  <div className="mt-1.5 text-[11px] text-slate-400">{s.time} · Agent 结果默认是草稿或建议，不自动执行（PRD §8.4）</div>
                </div>
              ))}
            </div>
            <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
              <div className="mb-2 text-[12px] font-semibold text-slate-500 dark:text-slate-400">本节点 Agent 能力配置</div>
              <div className="flex flex-wrap gap-1.5">
                {agentCaps.slice(0, 8).map((c) => (
                  <span key={c.name} className={cn('cap-tag', c.mode === 'direct' ? 'cap-direct' : c.mode === 'confirm' ? 'cap-confirm' : 'cap-forbid')}>
                    {c.name} · {c.mode === 'direct' ? 'direct' : c.mode === 'confirm' ? '需确认' : '禁止'}
                  </span>
                ))}
                <span className="cap-tag cap-forbid">submit_task · 禁止</span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-slate-400">节点配置只能限制 Agent，不能扩大授权用户权限（PRD §8.3）。</p>
            </div>
          </SectionCard>
        </div>

        {/* 右栏：文档 + 历史 + 动作 */}
        <div className="space-y-5">
          <SectionCard title="节点文档" bodyClassName="p-3">
            <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
              当前节点暂无上传文档（可在工作项详情上传）
            </div>
          </SectionCard>

          <SectionCard title="处理历史" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('audit')}>审计</button>} bodyClassName="p-4">
            <Timeline events={timeline} />
          </SectionCard>

          {/* 动作区 */}
          <SectionCard title="节点动作" bodyClassName="p-4">
            <div className="space-y-2">
              {actions.map((a) => (
                <div key={a.label}>
                  <button
                    disabled={a.disabled}
                    onClick={a.onClick}
                    className={cn(
                      'flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-[13.5px] font-medium transition-all',
                      a.disabled
                        ? 'cursor-not-allowed bg-slate-50 text-slate-400 dark:bg-slate-800/60 dark:text-slate-500'
                        : a.tone === 'primary'
                          ? 'bg-blue-600 text-white shadow-sm hover:bg-blue-700'
                          : a.tone === 'danger'
                            ? 'bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20'
                            : 'border border-slate-300 bg-white text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300',
                    )}
                  >
                    {a.icon}{a.label}
                  </button>
                  {a.disabled && <div className="mt-1 text-center text-[11px] text-slate-400">不可用原因：{a.reason}</div>}
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-start gap-1.5 rounded-lg bg-slate-50 p-2.5 text-[11px] leading-relaxed text-slate-400 dark:bg-slate-800/60">
              <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />
              幂等保护：同一节点仅可成功提交一次；重复请求返回原操作结果（PRD §12）
            </div>
          </SectionCard>
        </div>
      </div>

      {/* 前序节点补充信息弹窗：按该节点表单 schema 结构化追加 */}
      {appendFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setAppendFor(null)}>
          <div className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-5 shadow-l dark:border-slate-700 dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <b className="text-sm text-slate-800 dark:text-slate-100">补充「{appendFor.node}」信息</b>
              <button className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200" onClick={() => setAppendFor(null)}>✕</button>
            </div>
            <p className="mb-3 text-[11.5px] leading-relaxed text-slate-400">
              按该节点表单字段补充（必填校验与提交一致）；原提交保持不变，追加内容独立留痕，并通知当前节点处理人。
            </p>
            <SchemaForm fields={appendFor.schema} values={appendValues} onChange={setAppendValues} workItemId={task?.wiId} project={task?.project} />
            {appendFor.schema.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-200 p-3 text-center text-[11.5px] text-slate-400 dark:border-slate-700">
                该节点未配置补充字段（可在画布节点表单中添加）
              </div>
            )}
            <div className="mt-4 flex justify-end gap-2">
              <button
                className="rounded-lg border border-slate-300 px-3.5 py-1.5 text-[12px] font-medium text-slate-600 hover:border-slate-400 dark:border-slate-600 dark:text-slate-300"
                onClick={() => setAppendFor(null)}>取消</button>
              <button
                className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-[12px] font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-50"
                disabled={appendBusy} onClick={submitAppend}>
                {appendBusy ? '提交中…' : '提交补充'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
