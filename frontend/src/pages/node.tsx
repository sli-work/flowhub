import { useEffect, useState } from 'react'
import {
  ArrowLeft, ArrowRight, Bot, ClipboardList, FileText, Info, Layers, LoaderCircle, Users, Plus,
  Send, Sparkles, Undo2, UserPlus, PauseCircle, ShieldAlert, ChevronRight, Trash2,
} from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import {
  Badge, FlowSteps, SectionCard, Timeline, priorityBadge,
} from '../components/common'
import { SchemaForm, validateSchema, type SchemaValues } from '../components/schema-form'
import { MarkdownView } from '../components/markdown'
import { api, ApiError } from '../lib/api'
import { DocumentViewerDrawer, type ViewerDoc } from '../components/document-viewer-drawer'
import { cn } from '../lib/utils'
import type { AcceptanceChecks, FormField, NodeDeliverable, TaskItem, WorkItem } from '../types'

/* ---------- Expert Runtime 状态（后端无数据时兜底展示） ---------- */
const CAPABILITY_DEFAULT: { name: string; mode: string }[] = [
  { name: 'read_context', mode: 'direct' }, { name: 'read_documents', mode: 'direct' }, { name: 'read_history', mode: 'direct' },
  { name: 'generate_content', mode: 'confirm' }, { name: 'write_form', mode: 'confirm' }, { name: 'append_form', mode: 'confirm' },
  { name: 'upload_document', mode: 'confirm' }, { name: 'create_subtask', mode: 'confirm' },
  { name: 'submit_task', mode: 'forbid' }, { name: 'return_task', mode: 'forbid' }, { name: 'transfer_task', mode: 'forbid' },
  { name: 'pause_workflow', mode: 'forbid' }, { name: 'resume_workflow', mode: 'forbid' }, { name: 'close_work_item', mode: 'forbid' },
]

interface CanvasNodeLite {
  id: string
  label: string
  type?: string
  cfg?: {
    schema?: FormField[]
    purpose?: string
    handler?: string
    sla?: string
    deliverable?: NodeDeliverable
    split?: { mode?: 'off' | 'manual' | 'ai_assist' | 'ai_auto' }
  }
}

interface SubtaskBrief { id: string; title: string; node: string; status: string; assignee: string; due: string }
interface SplitRow { title: string; note: string; assignee: string }

/* 只读值展示：textarea 类型的长文本按 Markdown 渲染（Expert 产出/人工填写常用）；
   文档引用对象显示为附件名；其余原样 */
function ReadOnlyValue({ schema, k, v }: { schema?: FormField[]; k: string; v: unknown }) {
  const fmt = (item: unknown): string => {
    if (item === null || item === undefined) return ''
    if (typeof item === 'object') {
      const o = item as { name?: string; label?: string; title?: string; id?: string }
      return o.name ?? o.label ?? o.title ?? o.id ?? JSON.stringify(o)
    }
    return String(item)
  }
  if (Array.isArray(v)) return <>{v.map(fmt).filter(Boolean).join('、')}</>
  if (v !== null && typeof v === 'object') return <>{fmt(v)}</>
  const s = String(v)
  const ftype = schema?.find((f) => f.key === k)?.type
  if (ftype === 'textarea' && s.trim()) return <MarkdownView text={s} className="text-[12.5px] leading-relaxed" />
  return <>{s}</>
}

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
  const [curSchema, setCurSchema] = useState<FormField[]>([])
  const [curNodeType, setCurNodeType] = useState<string>('')
  /* 节点产出契约与拆分配置（来自画布 cfg） */
  const [curCfg, setCurCfg] = useState<{ purpose?: string; handler?: string; sla?: string; deliverable?: NodeDeliverable; splitMode?: string }>({})
  const [acceptance, setAcceptance] = useState<AcceptanceChecks>({})
  /* 子任务拆分 */
  const [subtasks, setSubtasks] = useState<SubtaskBrief[]>([])
  const [splitOpen, setSplitOpen] = useState(false)
  const [splitRows, setSplitRows] = useState<SplitRow[]>([{ title: '', note: '', assignee: '' }])
  const [suggestBusy, setSuggestBusy] = useState(false)
  const [splitBusy, setSplitBusy] = useState(false)
  const [expertCaps] = useState(CAPABILITY_DEFAULT)
  const [flowSteps, setFlowSteps] = useState<{ name: string; status: string; assignee: string; time: string }[]>([])
  const [timeline, setTimeline] = useState<{ id: string; time: string; title: string; desc: string; by: string; kind: string }[]>([])
  const [expertRuns, setExpertRuns] = useState<{ id: string; status: string; output: string; error: string; startedAt: string }[]>([])
  const [aiFilledKeys, setAiFilledKeys] = useState<string[]>([])
  const [fillBusy, setFillBusy] = useState(false)
  const [adoptBusy, setAdoptBusy] = useState<string | null>(null)
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
  const [docs, setDocs] = useState<ViewerDoc[]>([])
  const [viewer, setViewer] = useState<{ open: boolean; initialId?: string }>({ open: false })

  /* Expert Run 后台执行：run 进行中或自动节点处理中 → 轮询任务详情（3s），完成后自动刷新状态 */
  const expertProcessing = expertRuns[0]?.status === 'running' || task?.status === 'pending_confirmation'
  useEffect(() => {
    if (!activeTaskId || !expertProcessing) return
    const timer = setInterval(() => {
      api.get<{
        task: TaskItem; expertRuns?: { id: string; status: string; output: string; error: string; startedAt: string }[]
      }>(`/api/v1/tasks/${activeTaskId}`)
        .then((td) => {
          setTask((prev) => (prev && prev.id === td.task.id ? td.task : prev))
          if (td.expertRuns?.length) setExpertRuns(td.expertRuns)
        })
        .catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [activeTaskId, expertProcessing])

  /* 挂载：任务详情(+Expert Run 摘要 + 继承上下文) → 工作项详情 → 模板画布。 */
  useEffect(() => {
    if (!activeTaskId) { navigate('tasks'); return }
    // 切换任务时重置所有任务级状态：避免上一个任务的表单/节点配置/验收清单残留显示
    setTask(null); setFormValues({}); setAiFilledKeys([]); setExpertRuns([])
    setUpstream([]); setSubtasks([]); setTimeline([]); setFlowSteps([]); setDocs([])
    setCandidates([]); setStartValues({}); setWi(null); setInstance(null)
    setCurCfg({}); setCurSchema([]); setCurNodeType(''); setAcceptance({})
    setExpandedUp(new Set()); setSubmitBusy(false)
    setLoading(true)
    let curNodeId = ''
    let instRef: typeof instance = null
    // 引擎视角配置（nodeCfg）优先；画布链路仅在其缺失时兜底
    let engineCfgApplied = false
    api.get<{
      task: TaskItem & { nodeId?: string; acceptanceChecks?: AcceptanceChecks }
      upstream?: typeof upstream
      subtasks?: SubtaskBrief[]
      expertRuns?: { id: string; status: string; output: string; error: string; startedAt: string }[]
      nodeCfg?: { purpose?: string; handler?: string; sla?: string; schema?: FormField[]; deliverable?: NodeDeliverable; split?: { mode?: string } }
    }>(`/api/v1/tasks/${activeTaskId}`)
      .then((td) => {
        setTask(td.task)
        curNodeId = td.task.nodeId ?? ''
        if (td.upstream?.length) setUpstream(td.upstream)
        else setUpstream([])
        if (td.expertRuns?.length) setExpertRuns(td.expertRuns)
        setSubtasks(td.subtasks ?? [])
        // 引擎视角的节点配置（最新 published 画布）：任务书/表单/拆分与流转校验同源
        if (td.nodeCfg) {
          engineCfgApplied = true
          setCurCfg({
            purpose: td.nodeCfg.purpose, handler: td.nodeCfg.handler, sla: td.nodeCfg.sla,
            deliverable: td.nodeCfg.deliverable, splitMode: td.nodeCfg.split?.mode ?? 'off',
          })
          if (td.nodeCfg.schema?.length) setCurSchema(td.nodeCfg.schema)
          const accList = td.nodeCfg.deliverable?.acceptance ?? []
          if (accList.length) {
            setAcceptance(Object.fromEntries(accList.map((item) => [item.key, { text: item.text, checked: td.task?.acceptanceChecks?.[item.key]?.checked ?? false }])))
          } else setAcceptance({})
        }
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
          // 当前节点表单 Schema（与画布节点一致）；历史任务节点在新版画布中不存在时
          // 保留引擎返回的 nodeCfg schema，禁止兜底成第一个 task 节点（会显示别的节点的表单）
          const cur = canvas.nodes.find((n) => n.id === curNodeId)
          if (cur?.cfg?.schema) setCurSchema(cur.cfg.schema)
          setCurNodeType(cur?.type ?? '')
          if (!engineCfgApplied) {
            setCurCfg({
              purpose: cur?.cfg?.purpose, handler: cur?.cfg?.handler, sla: cur?.cfg?.sla,
              deliverable: cur?.cfg?.deliverable, splitMode: cur?.cfg?.split?.mode ?? 'off',
            })
          }
          // 验收清单初始化（保留已有勾选快照——例如提交失败后重进页面）
          if (!engineCfgApplied) {
            const list = cur?.cfg?.deliverable?.acceptance ?? []
            if (list.length) {
              setAcceptance((prev) => {
                if (Object.keys(prev).length) return prev
                const next: AcceptanceChecks = {}
                for (const item of list) next[item.key] = { text: item.text, checked: false }
                return next
              })
            } else setAcceptance({})
          }
        }
      })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '任务数据加载失败'))
      .finally(() => setLoading(false))
    api.get<{ users: { name: string; dept?: string }[] }>(`/api/v1/tasks/${activeTaskId}/candidates`)
      .then((d) => setCandidates(d.users))
      .catch(() => {})
    if (activeTaskId) {
      // 当前工作项全部文档（表单上传 + Expert 生成 + 聊天产出），供预览抽屉与文档卡
      api.get<{ items: ViewerDoc[] }>(`/api/v1/documents?wi=${activeTaskId}&page_size=100`)
        .then((d) => setDocs(d.items))
        .catch(() => {})
    }
  }, [activeTaskId, navigate])

  const [submitBusy, setSubmitBusy] = useState(false)
  const submitTask = async () => {
    if (submitBusy) return
    if (!activeTaskId) { toast.error('暂无真实任务可提交（请先新建工作项）'); return }
    if (task?.status === 'completed' || task?.status === 'cancelled') { toast.error('历史任务不可重复提交'); return }
    // 验收清单：节点配置了验收标准时必须逐条勾选（后端 advance 再次强校验）
    // 注意：本地校验失败直接 return，不进入 busy 状态（否则按钮永久 loading）
    if (curNodeType !== 'start' && Object.keys(acceptance).length) {
      const unchecked = Object.values(acceptance).filter((c) => !c.checked)
      if (unchecked.length) { toast.error(`验收标准未全部确认（剩 ${unchecked.length} 项）：${unchecked[0].text}`); return }
    }
    setSubmitBusy(true)
    try {
      // 起始节点任务：表单已在发起时提交，提交时携带起始表单值，无需重填
      const form = curNodeType === 'start' ? startValues : formValues
      const d = await api.post<{
        next_node?: { label?: string } | null; next_assignees?: { name: string }[]
        next_task_id?: string | null; next_tasks?: { id: string; node: string }[]
        parallel?: boolean; waiting_join?: boolean; closed?: boolean
      }>(`/api/v1/tasks/${activeTaskId}/actions`, { action: 'submit', form_values: form, acceptance_checks: acceptance })
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
    } finally { setSubmitBusy(false) }
  }

  /* ---------- Expert 节点统一面板：任务到达即有 Run —— 成功后「采纳」回填表单，执行中显示 loading，失败可重新生成 ---------- */
  const isExpertNode = curNodeType === 'task' && (curCfg.handler ?? '').includes('Expert')
  const isAutoNode = (curCfg.handler ?? '') === 'Expert 自动'
  const canExpert = isExpertNode && curSchema.length > 0 && !!activeTaskId && !task?.frozen && task?.status !== 'completed'
  const latestRun = expertRuns[0]

  const applyExpertValues = (values: Record<string, unknown>, warnings: string[], message: string) => {
    setFormValues((prev) => ({ ...prev, ...values }))
    setAiFilledKeys(Object.keys(values))
    if (warnings.length) toast.warning(`部分字段未生成：${warnings.join('；')}`)
    else toast.success(message)
  }

  const adoptRun = async (runId: string) => {
    if (!activeTaskId) return
    setAdoptBusy(runId)
    try {
      const d = await api.post<{ values: Record<string, unknown>; warnings: string[] }>(`/api/v1/tasks/${activeTaskId}/adopt-run`, { run_id: runId })
      applyExpertValues(d.values, d.warnings, '已采纳 Expert 产出，请审核后提交')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '采纳失败')
    } finally { setAdoptBusy(null) }
  }

  const regenerateRun = async () => {
    if (!activeTaskId) return
    setFillBusy(true)
    try {
      const d = await api.post<{ values: Record<string, unknown>; warnings: string[]; runId: string }>(`/api/v1/tasks/${activeTaskId}/ai-fill`, {})
      applyExpertValues(d.values, d.warnings, 'Expert 已重新生成表单草稿，请审核后提交')
      const td = await api.get<{ expertRuns: { id: string; status: string; output: string; error: string; startedAt: string }[] }>(`/api/v1/tasks/${activeTaskId}`)
      if (td.expertRuns) setExpertRuns(td.expertRuns)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Expert 生成失败')
    } finally { setFillBusy(false) }
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

  /* ---------- 子任务拆分（独立流转语义） ---------- */
  const canSplit = curNodeType === 'task' && curCfg.splitMode !== 'off' && !!activeTaskId && !task?.frozen && task?.status !== 'completed' && !task?.parentTaskId

  const suggestSplit = async () => {
    if (!activeTaskId) return
    setSuggestBusy(true)
    try {
      const d = await api.post<{ proposals: { title: string; note: string; assigneeHint?: string }[]; parseError?: string }>(`/api/v1/tasks/${activeTaskId}/split-suggest`, {})
      if (d.proposals.length) {
        setSplitRows(d.proposals.map((p) => ({ title: p.title, note: p.note, assignee: p.assigneeHint ?? '' })))
        toast.success(`AI 建议拆分为 ${d.proposals.length} 个子任务，请确认或修改后创建`)
      } else {
        toast.error(d.parseError || 'AI 未返回有效建议，请手动填写')
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'AI 拆分建议失败')
    } finally { setSuggestBusy(false) }
  }

  const submitSplit = async () => {
    if (!activeTaskId) return
    const rows = splitRows.filter((r) => r.title.trim())
    if (!rows.length) { toast.error('请至少填写一个子任务标题'); return }
    setSplitBusy(true)
    try {
      const d = await api.post<{ children: { id: string }[] }>(`/api/v1/tasks/${activeTaskId}/split`, {
        children: rows.map((r) => ({ title: r.title.trim(), note: r.note, assignee: r.assignee.trim() })),
      })
      toast.success(`已拆分为 ${d.children.length} 个子任务，均在下一节点独立流转`)
      setSplitOpen(false)
      // 拆分后父任务已完成：定位到第一条子线继续处理
      if (d.children[0]?.id) openTask(d.children[0].id, task?.wiId)
      else if (task?.wiId) openWorkItem(task.wiId)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '拆分失败')
    } finally { setSplitBusy(false) }
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

  /* 动作可用性说明（不可用动作说明原因而非静默隐藏）；归档冻结任务全部只读；
     completed/cancelled 历史任务同样只读回看，不允许再提交/退回/转办 */
  const readonlyTask = !!task?.frozen || task?.status === 'completed' || task?.status === 'cancelled'
  const readonlyReason = task?.frozen
    ? '项目已归档，流程已冻结（只读）'
    : '历史任务（已完成/已取消），仅供回看不可操作'
  const actions = readonlyTask
    ? [
        { label: '提交', icon: <Send className="h-4 w-4" />, disabled: true, reason: readonlyReason, onClick: () => {} },
        { label: '退回', icon: <Undo2 className="h-4 w-4" />, disabled: true, reason: readonlyReason, onClick: () => {} },
        { label: '转办', icon: <ArrowRight className="h-4 w-4" />, disabled: true, reason: readonlyReason, onClick: () => {} },
      ]
    : [
        { label: '认领', icon: <UserPlus className="h-4 w-4" />, disabled: true, reason: `已认领（${task?.assignee || '—'}）`, onClick: () => {} },
        task?.status === 'pending_confirmation'
          ? { label: '提交', icon: <Send className="h-4 w-4" />, disabled: true, reason: 'Expert 自动处理中：完成后自动采纳并流转，无需人工提交', onClick: () => {} }
          : {
              label: submitBusy ? '提交中…' : '提交',
              icon: submitBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />,
              tone: 'primary' as const, disabled: submitBusy,
              reason: submitBusy ? '正在提交（节点绑定了 Expert 时需等待生成完成）' : undefined,
              onClick: submitTask,
            },
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
            onClick={() => openDialog('expertApproval')}>
            <Bot className="h-4 w-4" />Expert 待审批
            <span className="rounded-full bg-violet-100 px-1.5 text-[10.5px] font-semibold text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">{expertRuns.filter((r) => r.status === 'interrupted').length}</span>
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

        {/* 中栏：任务书 + 表单 + 拆分 + 继承上下文 + Expert Run 结果 */}
        <div className="space-y-5">
          {curNodeType !== 'start' && (curCfg.purpose || curCfg.deliverable?.instruction || task?.brief || Object.keys(acceptance).length > 0) && (
            /* 任务书：人与 AI 共用同一份产出契约 */
            <SectionCard title="任务书" extra={<Badge tone="cyn"><ClipboardList className="mr-1 h-3 w-3" />产出契约</Badge>} bodyClassName="p-4">
              <div className="space-y-3 text-[12.5px] leading-relaxed text-slate-600 dark:text-slate-300">
                {task?.brief && (
                  <div className="rounded-lg border border-violet-200 bg-violet-50/60 p-3 dark:border-violet-500/30 dark:bg-violet-500/10">
                    <b className="text-[11.5px] font-semibold text-violet-700 dark:text-violet-300">拆分说明（来自父任务）</b>
                    <p className="mt-1 whitespace-pre-wrap">{task.brief}</p>
                  </div>
                )}
                {curCfg.purpose && (
                  <p><span className="font-semibold text-slate-500 dark:text-slate-400">节点目的：</span>{curCfg.purpose}</p>
                )}
                {curCfg.deliverable?.instruction && (
                  <div>
                    <div className="mb-1 flex items-center gap-1.5 font-semibold text-slate-700 dark:text-slate-200"><FileText className="h-3.5 w-3.5" />产出要求</div>
                    <p className="whitespace-pre-wrap rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">{curCfg.deliverable.instruction}</p>
                  </div>
                )}
                {Object.keys(acceptance).length > 0 && (
                  <div>
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="flex items-center gap-1.5 font-semibold text-slate-700 dark:text-slate-200"><ClipboardList className="h-3.5 w-3.5" />验收标准</span>
                      <Badge tone={Object.values(acceptance).every((c) => c.checked) ? 'suc' : 'warn'}>
                        {Object.values(acceptance).filter((c) => c.checked).length}/{Object.keys(acceptance).length} 已确认
                      </Badge>
                    </div>
                    <div className="space-y-1.5">
                      {Object.entries(acceptance).map(([key, item]) => (
                        <label key={key} className={cn('flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 transition-colors',
                          item.checked ? 'border-emerald-200 bg-emerald-50/60 dark:border-emerald-500/30 dark:bg-emerald-500/10' : 'border-slate-200 hover:border-blue-300 dark:border-slate-700')}>
                          <input type="checkbox" className="mt-0.5" checked={item.checked}
                            onChange={(e) => setAcceptance((prev) => ({ ...prev, [key]: { ...item, checked: e.target.checked } }))} />
                          <span className="min-w-0">
                            <span className={cn('block', item.checked ? 'text-emerald-700 line-through decoration-emerald-400 dark:text-emerald-300' : '')}>{item.text}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                    <p className="mt-1.5 text-[11px] text-slate-400">提交前必须逐条勾选确认；勾选记录随提交存档可审计。</p>
                  </div>
                )}
                {(curCfg.deliverable?.example || curCfg.deliverable?.aiGuidance) && (
                  <details className="rounded-lg border border-dashed border-slate-200 px-3 py-2 dark:border-slate-700">
                    <summary className="cursor-pointer text-[11.5px] font-medium text-slate-400">参考示例 / AI 指引</summary>
                    {curCfg.deliverable?.example && <p className="mt-1.5 whitespace-pre-wrap">{curCfg.deliverable.example}</p>}
                    {curCfg.deliverable?.aiGuidance && <p className="mt-1.5 whitespace-pre-wrap text-[11.5px] text-slate-400">AI 指引：{curCfg.deliverable.aiGuidance}</p>}
                  </details>
                )}
              </div>
            </SectionCard>
          )}

          {(canSplit || subtasks.length > 0 || task?.parentTaskId) && curNodeType !== 'start' && (
            /* 子任务拆分区 */
            <SectionCard
              title="子任务"
              extra={
                canSplit ? (
                  <button
                    className="inline-flex items-center gap-1 rounded-lg border border-blue-200 px-2.5 py-1 text-[11.5px] font-medium text-blue-600 transition-colors hover:bg-blue-50 dark:border-blue-500/30 dark:text-blue-300 dark:hover:bg-blue-500/10"
                    onClick={() => { setSplitRows([{ title: '', note: '', assignee: '' }]); setSplitOpen(true) }}>
                    <Layers className="h-3.5 w-3.5" />拆分子任务
                  </button>
                ) : null
              }
              bodyClassName="p-4">
              {subtasks.length > 0 ? (
                <div className="space-y-1.5">
                  {subtasks.map((s) => (
                    <div key={s.id} className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-[12px] dark:border-slate-700">
                      <button className="min-w-0 flex-1 truncate text-left font-medium text-blue-600 hover:underline" onClick={() => openTask(s.id, task?.wiId)}>
                        ↳ {s.title}
                      </button>
                      <span className="flex-none text-slate-400">{s.assignee}</span>
                      <Badge tone={s.status === 'completed' ? 'suc' : s.status === 'pending_confirmation' ? 'orgx' : 'info'}>{s.status}</Badge>
                    </div>
                  ))}
                  <p className="pt-1 text-[11px] leading-relaxed text-slate-400">
                    子任务在下一节点独立流转；各线全部完成后工作项才会关闭。
                  </p>
                </div>
              ) : canSplit ? (
                <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] leading-relaxed text-slate-400 dark:border-slate-700">
                  本节点支持拆分为多个子任务，拆分后各子任务在<b>「{flowSteps.find((s) => s.status === 'current')?.name ?? '下一节点'}」</b>独立流转。
                  {curCfg.splitMode === 'ai_assist' && ' 可使用 AI 建议快速生成拆分方案。'}
                  {curCfg.splitMode === 'manual' && ' 请人工填写拆分方案。'}
                </div>
              ) : task?.parentTaskId ? (
                <p className="text-[11.5px] text-slate-400">本任务是上级节点拆分出的子任务线；完成后沿流程独立流转。</p>
              ) : null}
            </SectionCard>
          )}

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
                    <div className="mt-1 break-all text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400"><ReadOnlyValue schema={curSchema} k={k} v={v} /></div>
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
            <SectionCard title={`${task?.node ?? '当前节点'} 表单`} extra={
              <div className="flex items-center gap-2">
                {canExpert && (latestRun?.status === 'running' || fillBusy) && (
                  <button className="inline-flex cursor-wait items-center gap-1 rounded-lg border border-violet-200 px-2.5 py-1 text-[11.5px] font-medium text-violet-500 dark:border-violet-500/30 dark:text-violet-400" disabled>
                    <Bot className="h-3.5 w-3.5 animate-pulse" />Expert 生成中…
                  </button>
                )}
                {canExpert && latestRun?.status === 'succeeded' && (
                  <button className="inline-flex items-center gap-1 rounded-lg bg-violet-600 px-2.5 py-1 text-[11.5px] font-medium text-white transition-colors hover:bg-violet-700 disabled:opacity-50"
                    disabled={adoptBusy === latestRun.id} onClick={() => void adoptRun(latestRun.id)}>
                    <Bot className="h-3.5 w-3.5" />{adoptBusy === latestRun.id ? '采纳中…' : '采纳 Expert 结果'}
                  </button>
                )}
                {canExpert && (!latestRun || latestRun.status === 'failed') && (
                  <button className="inline-flex items-center gap-1 rounded-lg border border-violet-200 px-2.5 py-1 text-[11.5px] font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:opacity-50 dark:border-violet-500/30 dark:text-violet-300 dark:hover:bg-violet-500/10"
                    disabled={fillBusy} onClick={() => void regenerateRun()}>
                    <Bot className="h-3.5 w-3.5" />{fillBusy ? 'Expert 生成中…' : 'Expert 协助填充'}
                  </button>
                )}
                <Badge tone="cyn">Schema 驱动 · 与画布节点一致</Badge>
              </div>
            }>
              {canExpert && isAutoNode && (
                <div className="mb-3 flex items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50/60 px-3 py-2 text-[11.5px] text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300">
                  <Bot className="h-3.5 w-3.5 flex-none animate-pulse" />
                  {task?.status === 'pending_confirmation'
                    ? 'Expert 自动处理中：Run 完成后会自动采纳并流转到下一节点，无需人工提交。'
                    : '本节点为 Expert 自动节点：Run 成功后自动采纳流转；当前为运行失败/校验不过后的人工兜底，可手动填写或重新生成后提交。'}
                </div>
              )}
              {aiFilledKeys.length > 0 && (
                <div className="mb-3 flex items-center gap-1.5 rounded-lg border border-violet-200 bg-violet-50/60 px-3 py-2 text-[11.5px] text-violet-700 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-300">
                  <Bot className="h-3.5 w-3.5 flex-none" />Expert 已填充 {aiFilledKeys.length} 个字段（含生成的文档），请逐项审核修改后提交。
                </div>
              )}
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
                                <span className="min-w-0 break-all text-slate-600 dark:text-slate-300"><ReadOnlyValue schema={seg.schema} k={k} v={v} /></span>
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
                                      <span className="min-w-0 break-all text-slate-600 dark:text-slate-300"><ReadOnlyValue schema={seg.schema} k={k} v={v} /></span>
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

          {/* Expert Run 结果 */}
          <SectionCard title="Expert Run 结果" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => openDialog('expertApproval')}>审批队列</button>}>
            <div className="space-y-3">
              {expertRuns.length === 0 && (
                <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                  暂无 Expert Run 产出（为节点绑定 Expert Deployment 后，到达节点将创建可追溯 Run 并可 AI 填充表单）
                </div>
              )}
              {expertRuns.map((s) => (
                <div key={s.id} className="rounded-lg border border-violet-100 bg-violet-50/50 p-3.5 dark:border-violet-500/20 dark:bg-violet-500/5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-[12.5px] font-semibold text-violet-700 dark:text-violet-300">
                      <Bot className="h-4 w-4" />{s.id}
                    </span>
                    <span className="flex flex-none items-center gap-2">
                      <Badge tone={s.status === 'succeeded' ? 'suc' : s.status === 'failed' ? 'err' : s.status === 'interrupted' ? 'orgx' : 'info'}>{s.status}</Badge>
                      {canExpert && s.status === 'succeeded' && (
                        <button className="rounded-md border border-violet-300 px-2 py-0.5 text-[11px] font-medium text-violet-600 transition-colors hover:bg-violet-100 disabled:opacity-50 dark:border-violet-500/40 dark:text-violet-300 dark:hover:bg-violet-500/10"
                          disabled={adoptBusy === s.id} onClick={() => void adoptRun(s.id)}>
                          {adoptBusy === s.id ? '采纳中…' : '采纳'}
                        </button>
                      )}
                      {canExpert && s.status === 'running' && (
                        <span className="text-[11px] text-violet-400">生成中…</span>
                      )}
                    </span>
                  </div>
                  <p className="mt-1.5 whitespace-pre-wrap break-words text-[12.5px] leading-relaxed text-slate-600 dark:text-slate-300">{s.output || s.error || '（无文本输出）'}</p>
                  <div className="mt-1.5 text-[11px] text-slate-400">{s.startedAt} · 采纳后产出解析为表单值，人工审核后提交</div>
                </div>
              ))}
            </div>
            <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
              <div className="mb-2 text-[12px] font-semibold text-slate-500 dark:text-slate-400">本节点 Expert Runtime 策略</div>
              <div className="flex flex-wrap gap-1.5">
                {expertCaps.slice(0, 8).map((c) => (
                  <span key={c.name} className={cn('cap-tag', c.mode === 'direct' ? 'cap-direct' : c.mode === 'confirm' ? 'cap-confirm' : 'cap-forbid')}>
                    {c.name} · {c.mode === 'direct' ? 'direct' : c.mode === 'confirm' ? '需确认' : '禁止'}
                  </span>
                ))}
                <span className="cap-tag cap-forbid">submit_task · 禁止</span>
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-slate-400">节点配置只能限制 Expert Deployment，不能扩大授权用户权限。</p>
            </div>
          </SectionCard>
        </div>

        {/* 右栏：文档 + 历史 + 动作 */}
        <div className="space-y-5">
          <SectionCard title="节点文档" extra={<Badge tone="info">{docs.length}</Badge>} bodyClassName="p-3">
            {docs.length ? (
              <div className="space-y-1">
                {docs.map((d) => (
                  <button key={d.id}
                    className="flex w-full items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-2 text-left text-[12px] transition-colors hover:border-blue-400 dark:border-slate-700"
                    onClick={() => setViewer({ open: true, initialId: d.id })}>
                    <FileText className="h-3.5 w-3.5 flex-none text-blue-500" />
                    <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">{d.name}</span>
                    <span className="flex-none text-[10.5px] text-slate-400">{d.uploader}</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-200 p-3.5 text-center text-[12px] text-slate-400 dark:border-slate-700">
                当前工作项暂无文档（表单上传 / Expert 生成后在此预览）
              </div>
            )}
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

      <DocumentViewerDrawer open={viewer.open} docs={docs} initialDocId={viewer.initialId} onClose={() => setViewer({ open: false })} />

      {/* 子任务拆分对话框：人工填写或 AI 建议预填，确认后创建并独立流转 */}
      {splitOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => { if (!splitBusy) setSplitOpen(false) }}>
          <div className="max-h-[86vh] w-full max-w-2xl overflow-y-auto rounded-xl border border-slate-200 bg-white p-5 shadow-l dark:border-slate-700 dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center justify-between">
              <b className="flex items-center gap-1.5 text-sm text-slate-800 dark:text-slate-100"><Layers className="h-4 w-4" />拆分「{task?.node}」为子任务</b>
              <button className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200" onClick={() => { if (!splitBusy) setSplitOpen(false) }}>✕</button>
            </div>
            <p className="mb-3 text-[11.5px] leading-relaxed text-slate-400">
              拆分后当前任务完成，各子任务在下一节点<b>独立流转</b>：各自有负责人与状态，全部完成工作项才会关闭。
            </p>
            <div className="space-y-2">
              {splitRows.map((row, i) => (
                <div key={i} className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                  <div className="flex items-center gap-2">
                    <span className="flex-none text-[11px] font-semibold text-slate-400">{i + 1}</span>
                    <input className="h-8 min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 text-[12.5px] outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
                      placeholder={`子任务 ${i + 1} 标题`} value={row.title}
                      onChange={(e) => setSplitRows((prev) => prev.map((r, j) => (j === i ? { ...r, title: e.target.value } : r)))} />
                    <input className="h-8 w-32 rounded-md border border-slate-300 bg-white px-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
                      list="split-assignee-options" placeholder="负责人（可选）" value={row.assignee}
                      onChange={(e) => setSplitRows((prev) => prev.map((r, j) => (j === i ? { ...r, assignee: e.target.value } : r)))} />
                    <datalist id="split-assignee-options">
                      {candidates.map((c) => <option key={c.name} value={c.name} />)}
                      <option value={currentUser?.name ?? ''} />
                    </datalist>
                    {splitRows.length > 1 && (
                      <button className="flex-none text-slate-300 hover:text-red-500" onClick={() => setSplitRows((prev) => prev.filter((_, j) => j !== i))}>
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                  <textarea className="mt-1.5 min-h-[40px] w-full rounded-md border border-slate-300 bg-white p-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200"
                    placeholder="该子任务的具体要求说明（将作为子任务的任务书）" value={row.note}
                    onChange={(e) => setSplitRows((prev) => prev.map((r, j) => (j === i ? { ...r, note: e.target.value } : r)))} />
                </div>
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <button className="inline-flex items-center gap-1 rounded-lg border border-violet-200 px-3 py-1.5 text-[12px] font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:opacity-50 dark:border-violet-500/30 dark:text-violet-300 dark:hover:bg-violet-500/10"
                  disabled={suggestBusy} onClick={() => void suggestSplit()}>
                  <Sparkles className="h-3.5 w-3.5" />{suggestBusy ? 'AI 分析中…' : 'AI 建议拆分'}
                </button>
                <button className="inline-flex items-center gap-1 rounded-lg border border-slate-300 px-3 py-1.5 text-[12px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 disabled:opacity-50 dark:border-slate-600 dark:text-slate-300"
                  disabled={splitRows.length >= 10} onClick={() => setSplitRows((prev) => [...prev, { title: '', note: '', assignee: '' }])}>
                  <Plus className="h-3.5 w-3.5" />添加子任务
                </button>
              </div>
              <div className="flex gap-2">
                <button className="rounded-lg border border-slate-300 px-3.5 py-1.5 text-[12px] font-medium text-slate-600 hover:border-slate-400 disabled:opacity-50 dark:border-slate-600 dark:text-slate-300"
                  disabled={splitBusy} onClick={() => setSplitOpen(false)}>取消</button>
                <button className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-[12px] font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-50"
                  disabled={splitBusy} onClick={() => void submitSplit()}>
                  {splitBusy ? '创建中…' : `创建 ${splitRows.filter((r) => r.title.trim()).length || ''} 个子任务`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

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
