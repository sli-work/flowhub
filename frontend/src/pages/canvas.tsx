import { useRef, useState, useEffect } from 'react'
import {
  Save, ShieldCheck, Undo2, GitBranch, Zap, X, Plus, Trash2,
  Move, Link2, Eye, Check, Play, Clock, LayoutGrid,
} from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { PageHeader, Badge } from '../components/common'
import { fieldTypeLabel } from '../components/schema-form'
import { cn } from '../lib/utils'
import type { CanvasNode, DeliverableAcceptanceItem, FormField, FormFieldType, NodeDeliverable, SplitMode } from '../types'

/* 节点类型 → 视觉 */
const nodeStyle: Record<CanvasNode['type'], { bg: string; text: string; fill: string; label: string }> = {
  start: { bg: 'bg-emerald-500', text: 'text-emerald-700 dark:text-emerald-300', fill: '#10B981', label: '开始' },
  end: { bg: 'bg-slate-600', text: 'text-slate-600 dark:text-slate-300', fill: '#475569', label: '结束' },
  task: { bg: 'bg-blue-500', text: 'text-blue-700 dark:text-blue-300', fill: '#3B82F6', label: '任务' },
  decision: { bg: 'bg-amber-500', text: 'text-amber-700 dark:text-amber-300', fill: '#F59E0B', label: '决策' },
  parallel_split: { bg: 'bg-violet-500', text: 'text-violet-700 dark:text-violet-300', fill: '#8B5CF6', label: '并行分叉' },
  parallel_join: { bg: 'bg-violet-500', text: 'text-violet-700 dark:text-violet-300', fill: '#8B5CF6', label: '并行汇合' },
  acceptance: { bg: 'bg-cyan-500', text: 'text-cyan-700 dark:text-cyan-300', fill: '#06B6D4', label: '验收' },
  closure: { bg: 'bg-orange-500', text: 'text-orange-700 dark:text-orange-300', fill: '#F97316', label: '闭环确认' },
  timer: { bg: 'bg-pink-500', text: 'text-pink-700 dark:text-pink-300', fill: '#EC4899', label: '定时' },
}

const typeLine: Record<CanvasNode['type'], string> = {
  start: 'START · 开始', end: 'END · 结束', task: 'TASK · 任务',
  decision: 'DECISION · 条件选择', parallel_split: 'SPLIT · 并行分叉', parallel_join: 'JOIN · 并行汇合',
  acceptance: 'ACCEPTANCE · 验收', closure: 'CLOSURE · 闭环确认', timer: 'TIMER · 定时等待',
}

/* 节点类型说明：用途 + 操作步骤（属性面板选中节点时展示，帮助理解各类型怎么用） */
const typeGuide: Record<CanvasNode['type'], { what: string; steps: string[] }> = {
  start: {
    what: '流程起点。新建工作项时填写的「硬性要求表单」就是开始节点的表单字段。',
    steps: ['在「表单字段」区新增字段（key / label / 类型 / 必填）', '发布后，新建工作项会按最新版本开始节点的表单收集信息'],
  },
  task: {
    what: '普通任务节点：处理人提交表单后，自动沿主边流转到下一节点。',
    steps: ['配置本节点要收集的表单字段（处理页展示）', '配置处理主体（人工 / Agent 协助）与 SLA 时限', '处理人「去处理 → 提交」后自动流转'],
  },
  decision: {
    what: '决策 / 条件节点：按处理人提交的表单值，自动选择走哪一条分支。',
    steps: ['先从本节点右侧端口拖出 2 条以上出边（到不同分支）', '选中本节点，在「分支条件」区为每条出边配置：字段 + 等于/不等于/包含 + 值', '选择「默认分支」：表单值都不匹配时走这里', '发布后流转：提交的表单值匹配哪个条件，就走对应分支'],
  },
  parallel_split: {
    what: '并行分叉：一次生成多个并行子任务，各分支可同时被处理。',
    steps: ['配置「分支数」（2-8）', '拖出对应数量的出边，连接到各并行分支的第一个节点', '发布后：进入该节点时同时生成 N 个任务'],
  },
  parallel_join: {
    what: '并行汇合：等待所有并行分支都完成后，才继续流转到下一节点。',
    steps: ['把各并行分支的最后一个节点都连到本节点', '所有分支任务完成后自动流转'],
  },
  acceptance: {
    what: '验收节点：验收人确认交付物是否满足验收标准。',
    steps: ['配置验收表单（如验收标准、验收结论）', '验收人提交结论后流转'],
  },
  closure: {
    what: '闭环确认：流程结束前的最终确认（如问题关闭确认）。',
    steps: ['配置确认表单（处理结果、确认人）', '确认后流转到结束节点，工作项关闭'],
  },
  timer: {
    what: '定时节点：到达指定时长后自动流转，无需人工处理。',
    steps: ['配置「等待时长」（小时）', '发布后：进入该节点自动计时，到时自动推进'],
  },
  end: {
    what: '最终确认节点：到达后生成待办，由绑定处理人确认提交后关闭工作项。',
    steps: ['可配置最终确认表单与处理人', '处理人提交后工作项自动关闭'],
  },
}

const W = 1400
const H = 640
const NODE_W = 132
const NODE_H = 60

interface Snap {
  nodes: CanvasNode[]
  edges: [string, string][]
  fallbacks: [string, string][]
}

function defaultCfg(type: CanvasNode['type']): CanvasNode['cfg'] {
  return {
    typeLine: typeLine[type],
    purpose: '新节点：双击属性面板编辑目的与产出物。',
    handler: '人工',
    fallback: '—',
    sla: '48 小时',
    schema: [],
    output: '待配置',
  }
}

export function CanvasPage() {
  const { openDialog, locateNode, setCheckProblems, canvasTarget } = useApp()
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  const [nodes, setNodes] = useState<CanvasNode[]>([])
  const [edges, setEdges] = useState<[string, string][]>([])
  const [fallbacks, setFallbacks] = useState<[string, string][]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const [checkResult, setCheckResult] = useState<{ ok: boolean; msgs: string[] } | null>(null)
  const [locateFlash, setLocateFlash] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  /* 可绑定 Expert Deployment 列表（后端 active） */
  const [deployments, setDeployments] = useState<{ id: string; name: string; environment: string; alias: string }[]>([])
  useEffect(() => {
    api.get<{ items: { id: string; name: string; environment: string; alias: string; status: string }[] }>('/api/v1/expert-deployments')
      .then((d) => setDeployments(d.items.filter((item) => item.status === 'active')))
      .catch(() => {})
  }, [])
  /* 当前模板：默认需求流程 v3（侧栏进入），模板页点开对应模板时用 canvasTarget */
  const tplId = canvasTarget?.templateId ?? 'tpl-req'
  const tplName = canvasTarget?.templateName ?? '需求流程'
  const viewVersion = canvasTarget?.version ?? 'v3'

  /* 挂载时从后端拉取画布（GET /templates/{id}/versions/{v}/canvas，后端空时返回默认画布） */
  useEffect(() => {
    api.get<{ nodes: CanvasNode[]; edges: [string, string][]; fallbacks: [string, string][] }>(`/api/v1/templates/${tplId}/versions/${viewVersion}/canvas`)
      .then((d) => {
        // 统一节点尺寸为当前画布规格（历史版本节点可能存有旧尺寸）
        const normalized = d.nodes.map((n) => ({ ...n, width: NODE_W, height: NODE_H }))
        // 画布扩大后，历史布局可能挤在左上角：整体居中（保持相对位置不变）
        if (normalized.length) {
          const minX = Math.min(...normalized.map((n) => n.x))
          const maxX = Math.max(...normalized.map((n) => n.x + n.width))
          const minY = Math.min(...normalized.map((n) => n.y))
          const maxY = Math.max(...normalized.map((n) => n.y + n.height))
          const offX = Math.max(0, (W - (maxX - minX)) / 2 - minX)
          const offY = Math.max(0, (H - (maxY - minY)) / 2 - minY)
          for (const n of normalized) { n.x += offX; n.y += offY }
        }
        setNodes(normalized)
        setEdges(d.edges)
        setFallbacks(d.fallbacks)
        setSelectedId(d.nodes[0]?.id ?? '')
      })
      .catch(() => { /* 后端不可用：画布为空 */ })
  }, [tplId, viewVersion])

  /* ---------- 视图：滚轮缩放 + 拖拽平移 + 适配 ---------- */
  const [vb, setVb] = useState({ x: 0, y: 0, w: W, h: H })
  const [zoom, setZoom] = useState(1)
  const panRef = useRef<{ sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null)
  const panMoved = useRef(false)

  const applyZoom = (factor: number, cx?: number, cy?: number) => {
    const nw = Math.min(W * 2.5, Math.max(W * 0.35, vb.w * factor))
    const k = nw / vb.w
    const px = cx ?? vb.x + vb.w / 2
    const py = cy ?? vb.y + vb.h / 2
    setVb({ x: px - (px - vb.x) * k, y: py - (py - vb.y) * k, w: nw, h: vb.h * k })
    setZoom(W / nw)
  }
  const fitView = () => { setVb({ x: 0, y: 0, w: W, h: H }); setZoom(1) }

  /* 滚轮缩放（以鼠标位置为中心；非 passive 监听以阻止页面滚动） */
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = svg.getBoundingClientRect()
      const cx = vb.x + ((e.clientX - rect.left) / rect.width) * vb.w
      const cy = vb.y + ((e.clientY - rect.top) / rect.height) * vb.h
      applyZoom(e.deltaY > 0 ? 1.12 : 1 / 1.12, cx, cy)
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  })

  const snapshot = useRef<Snap | null>(null)
  const drag = useRef<{
    kind: 'move' | 'link'
    id: string
    ox: number; oy: number   // move: 节点原坐标; link: 端口坐标
    start: { x: number; y: number }  // 指针按下时的 SVG 坐标
    cur: { x: number; y: number } | null
  } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const selected = nodes.find((n) => n.id === selectedId) ?? nodes[0] ?? null
  const st = selected ? nodeStyle[selected.type] : nodeStyle.task

  /* ---------- 坐标换算（按当前 viewBox，支持缩放/平移） ---------- */
  const toSvg = (e: { clientX: number; clientY: number }) => {
    const rect = svgRef.current!.getBoundingClientRect()
    return { x: vb.x + (e.clientX - rect.left) * vb.w / rect.width, y: vb.y + (e.clientY - rect.top) * vb.h / rect.height }
  }

  /* ---------- 发布校验问题定位：选中目标节点 + 视图居中 + 脉冲高亮 ---------- */
  useEffect(() => {
    if (!locateNode) return
    const target = nodes.find((n) => n.id === locateNode)
    setSelectedId(locateNode)
    setLocateFlash(locateNode)
    // 视图居中到目标节点（缩放态下也能定位）
    if (target) {
      setVb((v) => ({
        x: Math.max(0, Math.min(W - v.w, target.x + target.width / 2 - v.w / 2)),
        y: Math.max(0, Math.min(H - v.h, target.y + target.height / 2 - v.h / 2)),
        w: v.w, h: v.h,
      }))
    } else {
      toast(`未找到节点 ${locateNode}（当前画布为已发布版本，问题节点属于草稿）`)
    }
    // 脉冲高亮 3 秒后清除
    const timer = window.setTimeout(() => setLocateFlash(null), 3000)
    return () => window.clearTimeout(timer)
  }, [locateNode])

  /* ---------- 编辑模式进入 / 退出 ---------- */
  const enterEdit = () => {
    snapshot.current = { nodes: nodes.map((n) => ({ ...n, cfg: { ...n.cfg } })), edges: [...edges], fallbacks: [...fallbacks] }
    setMode('edit')
    setSelectedEdge(null)
    toast('已进入编辑模式：可拖拽节点、拖端口连线、增删节点，按 Delete 删除选中项')
  }
  /* ---------- 保存草稿（不发布）：保存到最新草稿版本（没有则自动创建新版本草稿） ---------- */
  const saveDraft = async () => {
    setSnapshotDraft()
    setMode('view')
    setBusy(true)
    try {
      const r = await api.post<{ version: string; status: string }>(
        `/api/v1/templates/${tplId}/versions/save-draft`,
        { nodes, edges, fallbacks },
      )
      toast.success(`画布已保存为 ${r.version} 草稿（未发布）`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存草稿失败')
      setMode('edit')
    } finally {
      setBusy(false)
    }
  }

  /* ---------- 发布：自动创建/复用最新版本 → 静态校验 → 自动发布（POST /versions/save-and-publish） ---------- */
  const publishCanvas = async () => {
    setSnapshotDraft()
    setMode('view')
    setBusy(true)
    try {
      const r = await api.post<{ version: string; status: string }>(
        `/api/v1/templates/${tplId}/versions/save-and-publish`,
        { nodes, edges, fallbacks },
      )
      toast.success(`已保存并发布新版本 ${r.version}：静态校验通过，进入 published 状态`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        // 校验未通过：画布已存为新版本草稿（可复用重试），打开发布校验弹框查看完整问题清单并定位
        toast.error(e.message)
        openDialog('validate')
      } else {
        toast.error(e instanceof ApiError ? e.message : '发布失败')
      }
    } finally {
      setBusy(false)
    }
  }

  const cancelEdit = () => {
    if (snapshot.current) {
      setNodes(snapshot.current.nodes)
      setEdges(snapshot.current.edges)
      setFallbacks(snapshot.current.fallbacks)
    }
    setMode('view')
    setSelectedEdge(null)
    setCheckResult(null)
    toast('已取消编辑：恢复进入前快照')
  }
  const setSnapshotDraft = () => {
    snapshot.current = { nodes: nodes.map((n) => ({ ...n, cfg: { ...n.cfg } })), edges: [...edges], fallbacks: [...fallbacks] }
  }

  /* ---------- 节点操作 ---------- */
  const updateNode = (id: string, patch: Partial<CanvasNode>) =>
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, ...patch } : n)))

  const updateNodeCfg = (id: string, cfgPatch: Partial<CanvasNode['cfg']>) =>
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, cfg: { ...n.cfg, ...cfgPatch } } : n)))

  /* ---------- Schema 字段编辑 ---------- */
  const updateSchemaField = (idx: number, patch: Partial<FormField>) =>
    updateNodeCfg(selected.id, { schema: selected.cfg.schema.map((f, i) => (i === idx ? { ...f, ...patch } : f)) })
  const removeSchemaField = (idx: number) =>
    updateNodeCfg(selected.id, { schema: selected.cfg.schema.filter((_, i) => i !== idx) })
  const moveSchemaField = (idx: number, dir: -1 | 1) => {
    const next = [...selected.cfg.schema]
    const target = idx + dir
    if (target < 0 || target >= next.length) return
    ;[next[idx], next[target]] = [next[target], next[idx]]
    updateNodeCfg(selected.id, { schema: next })
  }
  const addSchemaField = (label: string, type: FormFieldType) => {
    if (!label.trim()) { toast('请填写字段名称'); return }
    const dup = selected.cfg.schema.some((f) => f.label.trim() === label.trim())
    if (dup) { toast.error('已存在同名字段'); return }
    updateNodeCfg(selected.id, {
      schema: [...selected.cfg.schema, { key: `f${Date.now().toString(36)}`, label: label.trim(), type, required: false }],
    })
    toast.success(`已添加字段「${label.trim()}」`)
  }
  const setOptionsText = (idx: number, text: string) => {
    // 编辑态用文本承载（每行 显示文本=值），失焦/变更时解析为 options
    const options = text.split('\n').filter((line) => line.trim()).map((line) => {
      const [labelPart, valuePart] = line.split('=')
      return { label: (labelPart ?? '').trim(), value: (valuePart ?? labelPart ?? '').trim() }
    })
    updateSchemaField(idx, { options })
  }

  /* ---------- 产出契约 / 拆分模式 ---------- */
  const updateDeliverable = (patch: Partial<NodeDeliverable>) => {
    const d = selected.cfg.deliverable
    const base: NodeDeliverable = {
      instruction: d?.instruction ?? '',
      acceptance: d?.acceptance ?? [],
      aiGuidance: d?.aiGuidance ?? '',
      example: d?.example ?? '',
    }
    updateNodeCfg(selected.id, { deliverable: { ...base, ...patch } })
  }
  const addAcceptanceItem = () =>
    updateDeliverable({ acceptance: [...(selected.cfg.deliverable?.acceptance ?? []), { key: `a${Date.now().toString(36)}`, text: '' }] })
  const updateAcceptanceItem = (idx: number, patch: Partial<DeliverableAcceptanceItem>) =>
    updateDeliverable({ acceptance: (selected.cfg.deliverable?.acceptance ?? []).map((item, i) => (i === idx ? { ...item, ...patch } : item)) })
  const removeAcceptanceItem = (idx: number) =>
    updateDeliverable({ acceptance: (selected.cfg.deliverable?.acceptance ?? []).filter((_, i) => i !== idx) })
  const setSplitMode = (mode: SplitMode) => updateNodeCfg(selected.id, { split: { mode } })

  const addNode = (type?: CanvasNode['type']) => {
    const t = type ?? 'task'
    const idx = nodes.length
    const row = Math.floor(idx / 5)
    const col = idx % 5
    const id = `n${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`
    const label = nodeStyle[t].label
    const node: CanvasNode = {
      id, label, type: t, sub: t.toUpperCase(), x: 24 + col * 168, y: 24 + row * 110, width: NODE_W, height: NODE_H,
      cfg: defaultCfg(t),
    }
    setNodes((prev) => [...prev, node])
    setSelectedId(id)
    setSelectedEdge(null)
    setAddOpen(false)
    toast(`已添加节点「${label}」（${id}）：拖动右侧端口连线`)
  }

  const deleteNode = (id: string) => {
    const n = nodes.find((x) => x.id === id)!
    setNodes((prev) => prev.filter((x) => x.id !== id))
    setEdges((prev) => prev.filter(([a, b]) => a !== id && b !== id))
    setFallbacks((prev) => prev.filter(([a, b]) => a !== id && b !== id))
    if (selectedId === id) setSelectedId(nodes.find((x) => x.id !== id)?.id ?? '')
    toast(`已删除节点「${n.label}」及其关联边`)
  }

  /* ---------- 连线操作 ---------- */
  const addEdge = (from: string, to: string) => {
    if (from === to) return
    if (edges.some(([a, b]) => a === from && b === to)) { toast('该连线已存在'); return }
    setEdges((prev) => [...prev, [from, to]])
    toast('已创建主线连线')
  }
  const addFallback = (from: string, to: string) => {
    if (from === to) return
    if (fallbacks.some(([a, b]) => a === from && b === to)) { toast('该回退路径已存在'); return }
    setFallbacks((prev) => [...prev, [from, to]])
    toast.success(`已设置回退目标：${nodes.find((n) => n.id === to)?.label}`)
  }
  const deleteEdge = (key: string) => {
    setEdges((prev) => prev.filter(([a, b]) => `${a}-${b}` !== key))
    setSelectedEdge(null)
    toast('已删除主线连线')
  }
  const deleteFallback = (key: string) => {
    setFallbacks((prev) => prev.filter(([a, b]) => `fb-${a}-${b}` !== key))
    toast('已移除回退路径')
  }

  /* ---------- 指针事件（window 级监听，兼容所有浏览器） ---------- */
  const nodesRef = useRef(nodes)
  const edgesRef = useRef(edges)
  useEffect(() => { nodesRef.current = nodes; edgesRef.current = edges })

  /* 稳定的监听器引用（避免 render 闭包版本漂移） */
  const moveListener = useRef<(e: PointerEvent) => void>(() => {})
  const upListener = useRef<(e: PointerEvent) => void>(() => {})

  useEffect(() => {
    return () => {
      window.removeEventListener('pointermove', moveListener.current)
      window.removeEventListener('pointerup', upListener.current)
      window.removeEventListener('pointercancel', upListener.current)
    }
  }, [])

  /* ---------- 背景平移（按住空白拖拽；未移动视为点击空白 → 取消选中） ---------- */
  const onBgPointerDown = (e: React.PointerEvent) => {
    panRef.current = { sx: e.clientX, sy: e.clientY, vx: vb.x, vy: vb.y, moved: false }
    panMoved.current = false
    const onMove = (ev: PointerEvent) => {
      const p = panRef.current
      if (!p) return
      const dx = ev.clientX - p.sx
      const dy = ev.clientY - p.sy
      if (Math.abs(dx) + Math.abs(dy) > 3) p.moved = true
      if (!p.moved) return
      panMoved.current = true
      const rect = svgRef.current!.getBoundingClientRect()
      setVb((v) => ({ x: p.vx - (dx * v.w) / rect.width, y: p.vy - (dy * v.h) / rect.height, w: v.w, h: v.h }))
    }
    const onUp = () => {
      if (!panMoved.current && mode === 'edit') { setSelectedEdge(null); setSelectedId('') }
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  /* ---------- 自动整理：拓扑分层 + 蛇形排布（主边最长路径定层级） ---------- */
  const autoLayout = () => {
    setNodes((prev) => {
      const ids = prev.map((n) => n.id)
      const succ = new Map<string, string[]>()
      const indeg = new Map<string, number>()
      for (const id of ids) { succ.set(id, []); indeg.set(id, 0) }
      for (const [a, b] of edges) {
        if (succ.has(a) && indeg.has(b) && a !== b) {
          succ.get(a)!.push(b)
          indeg.set(b, (indeg.get(b) ?? 0) + 1)
        }
      }
      // Kahn 最长路径分层（回退边不参与，避免环干扰）
      const depth = new Map<string, number>()
      const work = new Map(indeg)
      const q = ids.filter((id) => (work.get(id) ?? 0) === 0)
      for (const id of q) depth.set(id, 0)
      while (q.length) {
        const id = q.shift()!
        for (const b of succ.get(id) ?? []) {
          depth.set(b, Math.max(depth.get(b) ?? 0, (depth.get(id) ?? 0) + 1))
          work.set(b, (work.get(b) ?? 1) - 1)
          if ((work.get(b) ?? 0) === 0) q.push(b)
        }
      }
      for (const id of ids) if (!depth.has(id)) depth.set(id, 0) // 环上孤点放首层兜底
      // 分层 → 行（行内按当前 x 保持相对顺序）→ 蛇形排布
      const layers = new Map<number, string[]>()
      for (const id of [...ids].sort((x, y) => (prev.find((n) => n.id === x)?.x ?? 0) - (prev.find((n) => n.id === y)?.x ?? 0))) {
        const d = depth.get(id) ?? 0
        if (!layers.has(d)) layers.set(d, [])
        layers.get(d)!.push(id)
      }
      const depths = [...layers.keys()].sort((a, b) => a - b)
      const GAP_X = NODE_W + 72
      const GAP_Y = NODE_H + 84
      const posById = new Map<string, { x: number; y: number }>()
      depths.forEach((d, row) => {
        const rowIds = layers.get(d)!
        const ltr = row % 2 === 0
        const ordered = ltr ? rowIds : [...rowIds].reverse()
        const rowWidth = ordered.length * GAP_X - (GAP_X - NODE_W)
        const startX = Math.max(32, (W - rowWidth) / 2)
        ordered.forEach((id, i) => {
          posById.set(id, {
            x: Math.round(ltr ? startX + i * GAP_X : startX + (ordered.length - 1 - i) * GAP_X),
            y: Math.round(36 + row * GAP_Y),
          })
        })
      })
      return prev.map((n) => ({ ...n, ...(posById.get(n.id) ?? {}) }))
    })
    setCheckResult(null)
    toast.success('已自动整理：按流转顺序分层蛇形排布')
  }

  /* ---------- 拖连线落点高亮 ---------- */
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  /* 悬停节点：显示删除按钮 */
  const [hoverNodeId, setHoverNodeId] = useState<string | null>(null)

  const onWinMove = (e: PointerEvent) => {
    const d = drag.current
    if (!d) return
    const p = toSvg({ clientX: e.clientX, clientY: e.clientY })
    if (d.kind === 'move') {
      const nx = Math.round((d.ox + (p.x - d.start.x)) / 8) * 8
      const ny = Math.round((d.oy + (p.y - d.start.y)) / 8) * 8
      updateNode(d.id, { x: Math.max(8, Math.min(W - NODE_W - 8, nx)), y: Math.max(8, Math.min(H - NODE_H - 8, ny)) })
    } else {
      // 连线磁吸：指针靠近目标节点（含 16px 外扩热区）时吸附到其左侧端口，松手即连
      const hit = hitNodeRef(p, 16)
      d.cur = hit ? { x: hit.x, y: hit.y + hit.height / 2 } : p
      setDropTarget(hit && hit.id !== d.id ? hit.id : null)
    }
  }
  const onWinUp = (e: PointerEvent) => {
    const d = drag.current
    if (!d) return
    const p = toSvg({ clientX: e.clientX, clientY: e.clientY })
    if (d.kind === 'link') {
      const target = hitNodeRef(p, 16)
      if (target && target.id !== d.id) {
        addEdge(d.id, target.id)
      } else if (target) {
        toast('不能连接到自身节点')
      } else {
        toast('未命中节点：从节点右侧蓝点拖出，靠近目标节点会自动吸附，松手完成连线')
      }
      setDropTarget(null)
    }
    drag.current = null
    window.removeEventListener('pointermove', moveListener.current)
    window.removeEventListener('pointerup', upListener.current)
    window.removeEventListener('pointercancel', upListener.current)
  }
  moveListener.current = onWinMove
  upListener.current = onWinUp
  const beginDrag = (kind: 'move' | 'link', id: string, ox: number, oy: number, p: { x: number; y: number }) => {
    drag.current = { kind, id, ox, oy, start: p, cur: kind === 'link' ? p : null }
    window.addEventListener('pointermove', moveListener.current)
    window.addEventListener('pointerup', upListener.current)
    window.addEventListener('pointercancel', upListener.current)
  }
  const onNodePointerDown = (e: React.PointerEvent, n: CanvasNode) => {
    if (mode !== 'edit') return
    e.preventDefault()
    e.stopPropagation()
    beginDrag('move', n.id, n.x, n.y, toSvg(e))
    setSelectedId(n.id)
    setSelectedEdge(null)
  }
  const onPortPointerDown = (e: React.PointerEvent, n: CanvasNode) => {
    if (mode !== 'edit') return
    e.preventDefault()
    e.stopPropagation()
    beginDrag('link', n.id, n.x + NODE_W, n.y + NODE_H / 2, toSvg(e))
    setSelectedId(n.id)
    setSelectedEdge(null)
  }
  const hitNodeRef = (p: { x: number; y: number }, pad = 0) =>
    nodesRef.current.find((n) => p.x >= n.x - pad && p.x <= n.x + n.width + pad && p.y >= n.y - pad && p.y <= n.y + n.height + pad)

  /* ---------- 键盘删除 ---------- */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (mode !== 'edit') return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        const tag = (e.target as HTMLElement)?.tagName
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
        if (selectedEdge) { deleteEdge(selectedEdge); return }
        if (selectedId) { const n = nodes.find((x) => x.id === selectedId); if (n && n.type !== 'start' && n.type !== 'end') deleteNode(selectedId) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  /* ---------- 静态校验（真实规则） ---------- */
  const runCheck = () => {
    const msgs: string[] = []
    const problems: { title: string; nodeId: string; desc: string }[] = []
    const starts = nodes.filter((n) => n.type === 'start')
    const ends = nodes.filter((n) => n.type === 'end')
    if (starts.length !== 1) {
      msgs.push(`开始节点数量应为 1（当前 ${starts.length}）`)
      problems.push({ title: `开始节点数量非法（${starts.length}）`, nodeId: starts[0]?.id ?? nodes[0]?.id ?? '', desc: '开始节点必须且仅能存在 1 个' })
    }
    if (ends.length < 1) {
      msgs.push('缺少结束节点')
      problems.push({ title: '缺少结束节点', nodeId: '', desc: '流程必须包含至少 1 个结束节点' })
    }
    if (starts.length === 1 && edges.some(([, b]) => b === starts[0].id)) {
      msgs.push('开始节点不允许有主线入边')
      problems.push({ title: '开始节点存在主线入边', nodeId: starts[0].id, desc: '开始节点不允许有主线入边；回退到开始节点（驳回重提）是允许的' })
    }
    if (ends.length > 0 && (edges.some(([a]) => ends.some((en) => en.id === a)) || fallbacks.some(([a]) => ends.some((en) => en.id === a)))) {
      msgs.push('结束节点不允许有出边')
      problems.push({ title: '结束节点存在出边', nodeId: ends[0].id, desc: '结束节点不允许有任何出边（主线/回退均不允许）' })
    }
    for (const [a, b] of edges) {
      if (!nodes.some((n) => n.id === a) || !nodes.some((n) => n.id === b)) {
        msgs.push(`连线引用不存在的节点（${a} → ${b}）`)
        problems.push({ title: `连线引用不存在的节点（${a} → ${b}）`, nodeId: a, desc: '主边两端必须引用画布中存在的节点' })
        break
      }
    }
    for (const n of nodes.filter((x) => x.type !== 'start' && x.type !== 'end')) {
      if (!edges.some(([a]) => a === n.id) && !fallbacks.some(([a]) => a === n.id)) {
        msgs.push(`节点「${n.label}」没有出边（悬空）`)
        problems.push({ title: `节点「${n.label}」悬空（无出边）`, nodeId: n.id, desc: `节点 ID ${n.id} 没有指向任何后续节点，流程将在此中断` })
      }
    }
    for (const [a, b] of fallbacks) {
      if (a === b) {
        msgs.push(`回退路径不能指向自身（${a}）`)
        const n = nodes.find((x) => x.id === a)
        problems.push({ title: `节点「${n?.label ?? a}」回退指向自身`, nodeId: a, desc: `节点 ID ${a} 的回退路径不能指向自己` })
      }
    }
    setCheckProblems(problems)
    const result = { ok: msgs.length === 0, msgs }
    setCheckResult(result)
    if (result.ok) toast.success('静态校验通过：拓扑、连通性、回退目标均合法（PRD §6.4）')
    else toast.error(`校验未通过：${msgs.length} 个问题（详见提示条）`)
  }

  /* ---------- 渲染辅助 ---------- */
  const pos = (id: string) => nodes.find((n) => n.id === id)!
  const edgeD = (a: string, b: string, offset = 0) => {
    const A = pos(a), B = pos(b)
    if (!A || !B) return ''
    const acx = A.x + A.width / 2, acy = A.y + A.height / 2
    const bcx = B.x + B.width / 2, bcy = B.y + B.height / 2
    const dx = bcx - acx, dy = bcy - acy
    let p1: { x: number; y: number }, p2: { x: number; y: number }
    if (Math.abs(dy) > Math.abs(dx) * 1.4) {
      // 近似垂直（蛇形换行）：下行从源底部出、目标顶部入；上行反之
      if (dy > 0) { p1 = { x: acx, y: A.y + A.height }; p2 = { x: bcx, y: B.y } }
      else { p1 = { x: acx, y: A.y }; p2 = { x: bcx, y: B.y + B.height } }
    } else if (dx >= 0) {
      // 右向流：源右侧出、目标左侧入
      p1 = { x: A.x + A.width, y: acy }; p2 = { x: B.x, y: bcy }
    } else {
      // 回向（目标在左）：源底部绕行出、目标左侧入，避免横穿中间节点
      p1 = { x: acx, y: A.y + A.height }; p2 = { x: B.x, y: bcy }
    }
    // 贝塞尔朝向按跨度主轴选择；offset 平移控制点（拖弯）
    if (Math.abs(p1.x - p2.x) >= Math.abs(p1.y - p2.y)) {
      const mx = (p1.x + p2.x) / 2
      return `M ${p1.x} ${p1.y} C ${mx} ${p1.y + offset}, ${mx} ${p2.y + offset}, ${p2.x} ${p2.y}`
    }
    const my = (p1.y + p2.y) / 2 + offset
    return `M ${p1.x} ${p1.y} C ${p1.x} ${my}, ${p2.x} ${my}, ${p2.x} ${p2.y}`
  }
  const fbD = (a: string, b: string) => {
    const A = pos(a), B = pos(b)
    if (!A || !B) return ''
    const x1 = A.x + A.width / 2, y1 = A.y + A.height
    const x2 = B.x + B.width / 2, y2 = B.y
    const my = (y1 + y2) / 2 + 28
    return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`
  }
  const edgeKey = (a: string, b: string) => `${a}-${b}`
  /** 连线视觉偏移：存源节点 cfg.edgeOffsets，随画布保存/加载 */
  const edgeOffsetOf = (a: string, b: string) => {
    const from = nodes.find((n) => n.id === a)
    return from?.cfg.edgeOffsets?.[`${a}-${b}`] ?? 0
  }
  const setEdgeOffset = (a: string, b: string, offset: number) => {
    updateNodeCfg(a, { edgeOffsets: { ...(nodes.find((n) => n.id === a)?.cfg.edgeOffsets ?? {}), [`${a}-${b}`]: Math.round(offset) } })
  }

  /* ---------- 拖动选中连线调整弯曲度 ---------- */
  const edgeDragRef = useRef<{ key: string; a: string; b: string; startY: number; startOffset: number } | null>(null)
  const edgeMoveListener = useRef<(e: PointerEvent) => void>(() => {})
  const edgeUpListener = useRef<() => void>(() => {})
  useEffect(() => () => {
    window.removeEventListener('pointermove', edgeMoveListener.current)
    window.removeEventListener('pointerup', edgeUpListener.current)
  }, [])
  const beginEdgeDrag = (key: string, e: React.PointerEvent) => {
    const idx = key.startsWith('fb-') ? null : key.split('-')
    if (!idx) return // 回退边固定绕行形态，不支持拖弯
    const [a, b] = idx
    if (e.stopPropagation) e.stopPropagation()
    edgeDragRef.current = { key, a, b, startY: e.clientY, startOffset: edgeOffsetOf(a, b) }
    const onMove = (ev: PointerEvent) => {
      const d = edgeDragRef.current
      if (!d) return
      const rect = svgRef.current!.getBoundingClientRect()
      const scale = vbRef.current.w / rect.width
      setEdgeOffset(d.a, d.b, d.startOffset + ((ev.clientY - d.startY) * scale) / 2)
    }
    const onUp = () => {
      edgeDragRef.current = null
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }
  const vbRef = useRef(vb)
  useEffect(() => { vbRef.current = vb })

  /* 添加节点类型选择器 */
  /* 全部节点类型均可添加；开始/结束全局唯一（画布只能各 1 个，发布校验强制） */
  const addableTypes = Object.keys(nodeStyle) as CanvasNode['type'][]
  const typeExists = (t: CanvasNode['type']) => (t === 'start' || t === 'end') && nodes.some((n) => n.type === t)

  return (
    <div className="page-container">
      <PageHeader
        title={mode === 'edit' ? `流程画布 · ${tplName}（编辑中·草稿）` : `流程画布 · ${tplName} ${viewVersion}`}
        sub={mode === 'edit'
          ? '拖拽节点移动 · 从节点右侧端口拖到目标节点连线 · 点击边/节点后按 Delete 删除 · 取消编辑恢复快照'
          : `已发布版本不可修改；「保存草稿」暂存到最新草稿、「发布」自动创建新版本并发布`}
        actions={
          mode === 'edit' ? (
            <>
              <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={cancelEdit}>
                <X className="h-4 w-4" />取消编辑
              </button>
              <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={runCheck}>
                <ShieldCheck className="h-4 w-4" />静态校验
              </button>
              <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={saveDraft} disabled={busy}>
                <Save className="h-4 w-4" />保存草稿
              </button>
              <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700 disabled:opacity-60" onClick={publishCanvas} disabled={busy}>
                <Play className="h-4 w-4" />{busy ? '处理中…' : '发布'}
              </button>
            </>
          ) : (
            <>
              <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => toast(`当前为已发布只读版本；进入编辑模式修改后「保存草稿」暂存、「发布」自动创建新版本并发布`)}>
                <Save className="h-4 w-4" />保存草稿
              </button>
              <button className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => openDialog('validate')}>
                <Play className="h-4 w-4" />发布校验
              </button>
              <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={enterEdit}>
                <Zap className="h-4 w-4" />进入编辑模式
              </button>
            </>
          )
        }
      />

      <div className="grid gap-5 xl:grid-cols-[1fr_350px]">
        {/* ============ 画布 ============ */}
        <div className={cn('rounded-xl border bg-white p-5 shadow-s dark:bg-slate-900',
          mode === 'edit' ? 'border-blue-300 ring-2 ring-blue-500/10 dark:border-blue-500/50' : 'border-slate-200 dark:border-slate-700')}>
          <div className="mb-3 flex flex-wrap items-center gap-3 text-[11.5px] text-slate-400">
            <span className="inline-flex items-center gap-1.5"><span className="h-0.5 w-6 bg-blue-400" />主线流转</span>
            <span className="inline-flex items-center gap-1.5"><span className="h-0 w-6 border-t-2 border-dashed border-red-400" />回退路径</span>
            {mode === 'edit' && <span className="inline-flex items-center gap-1 text-blue-500"><Move className="h-3.5 w-3.5" />编辑中</span>}
            <span className="ml-auto inline-flex items-center gap-1"><GitBranch className="h-3.5 w-3.5" />{nodes.length} 节点 · {edges.length} 条主线 · {fallbacks.length} 条回退</span>
          </div>

          {/* 校验结果提示条 */}
          {checkResult && (
            <div className={cn('mb-3 rounded-lg border px-3.5 py-2.5 text-[12px] leading-relaxed',
              checkResult.ok
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300'
                : 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300')}>
              {checkResult.ok ? '✓ 静态校验通过：拓扑、连通性、回退目标均合法' : (
                <>
                  ✗ 校验未通过：{checkResult.msgs.length} 个问题
                  <ul className="mt-1 list-inside list-disc">
                    {checkResult.msgs.slice(0, 4).map((m) => <li key={m}>{m}</li>)}
                  </ul>
                </>
              )}
            </div>
          )}

          <div className="relative overflow-hidden rounded-lg border border-slate-100 bg-slate-50/40 dark:border-slate-800 dark:bg-slate-950/40">
            <svg
              ref={svgRef}
              viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
              className={cn('block w-full touch-none select-none', mode === 'edit' ? 'cursor-grab active:cursor-grabbing' : 'cursor-grab')}
              style={{ width: '100%', height: 460 }}
              onPointerDown={onBgPointerDown}
            >
              <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#94A3B8" />
                </marker>
                <marker id="arrow-blue" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#2563EB" />
                </marker>
                <marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#F87171" />
                </marker>
                <marker id="arrow-green" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 10 5 L 0 10 z" fill="#10B981" />
                </marker>
              </defs>

              {/* 网格背景（编辑模式） */}
              {mode === 'edit' && (
                <pattern id="grid" width="24" height="24" patternUnits="userSpaceOnUse">
                  <path d="M 24 0 L 0 0 0 24" fill="none" stroke="#F1F5F9" strokeWidth="0.6" className="dark:stroke-slate-800" />
                </pattern>
              )}
              {mode === 'edit' && <rect x={-2000} y={-2000} width={W + 4000} height={H + 4000} fill="url(#grid)" />}

              {/* 主线边 */}
              {edges.map(([a, b]) => {
                const key = edgeKey(a, b)
                const sel = selectedEdge === key
                const dPath = edgeD(a, b, edgeOffsetOf(a, b))
                return (
                  <g key={key} onClick={(e) => { if (mode === 'edit') { e.stopPropagation(); setSelectedEdge(key) } }} className={mode === 'edit' ? 'cursor-pointer' : ''}>
                    <path d={dPath} fill="none" stroke={sel ? '#2563EB' : '#94A3B8'} strokeWidth={sel ? 2.6 : 1.6} markerEnd={sel ? 'url(#arrow-blue)' : 'url(#arrow)'} className="transition-colors" />
                    {/* 加宽点击热区 */}
                    <path d={dPath} fill="none" stroke="transparent" strokeWidth="14" style={{ pointerEvents: mode === 'edit' ? 'stroke' : 'none' }} />
                  </g>
                )
              })}
              {/* 回退边（下行绕行曲线） */}
              {fallbacks.map(([a, b]) => {
                const key = `fb-${a}-${b}`
                const sel = selectedEdge === key
                return (
                  <g key={key} onClick={(e) => { if (mode === 'edit') { e.stopPropagation(); setSelectedEdge(key) } }} className={mode === 'edit' ? 'cursor-pointer' : ''}>
                    <path d={fbD(a, b)} fill="none" stroke={sel ? '#2563EB' : '#F87171'} strokeWidth={sel ? 2.6 : 1.4} strokeDasharray="5 4" markerEnd="url(#arrow-red)" className="transition-colors" />
                    <path d={fbD(a, b)} fill="none" stroke="transparent" strokeWidth="14" style={{ pointerEvents: mode === 'edit' ? 'stroke' : 'none' }} />
                  </g>
                )
              })}

              {/* 临时连线（拖拽中）：磁吸时吸附线 + 跟随提示 */}
              {drag.current?.kind === 'link' && drag.current.cur && (() => {
                const d = drag.current!
                const cur = d.cur!
                const snapped = !!dropTarget
                return (
                  <g pointerEvents="none">
                    <path d={`M ${d.ox} ${d.oy} L ${cur.x} ${cur.y}`} fill="none"
                      stroke={snapped ? '#10B981' : '#2563EB'} strokeWidth={snapped ? 2.4 : 1.8} strokeDasharray={snapped ? undefined : '4 3'} markerEnd={snapped ? 'url(#arrow-green)' : 'url(#arrow-blue)'} />
                    <g transform={`translate(${(d.ox + cur.x) / 2}, ${(d.oy + cur.y) / 2 - 16})`}>
                      <rect x={-54} y={-11} width={108} height={20} rx={10} fill={snapped ? '#059669' : '#1D4ED8'} opacity={0.92} />
                      <text x={0} y={3} fontSize="10.5" textAnchor="middle" fill="white" fontWeight="600">
                        {snapped ? '松手完成连线' : '拖到目标节点附近'}
                      </text>
                    </g>
                  </g>
                )
              })()}

              {/* 节点 */}
              {nodes.map((n) => {
                const s = nodeStyle[n.type]
                const sel = selectedId === n.id
                const located = locateFlash === n.id
                const dropping = dropTarget === n.id
                const label = n.label.length > 10 ? `${n.label.slice(0, 10)}…` : n.label
                const sub = `${n.sub} · ${typeLine[n.type]}`
                return (
                  <g key={n.id} className="canvas-node select-none"
                    onPointerDown={(e) => onNodePointerDown(e, n)}
                    onPointerEnter={() => setHoverNodeId(n.id)}
                    onPointerLeave={() => setHoverNodeId((cur) => (cur === n.id ? null : cur))}
                    onClick={(e) => { e.stopPropagation(); setSelectedId(n.id); setSelectedEdge(null) }}>
                    <rect x={n.x} y={n.y} width={n.width} height={n.height} rx={10}
                      className={cn('node-body transition-all',
                        dropping ? 'fill-emerald-50 dark:fill-emerald-500/10'
                          : sel ? 'fill-blue-50 dark:fill-blue-500/10'
                            : 'fill-white dark:fill-slate-800')}
                      stroke={located ? '#EF4444' : dropping ? '#10B981' : sel ? '#2563EB' : '#CBD5E1'}
                      strokeWidth={located ? 2.5 : dropping || sel ? 2 : 1.2}
                      strokeDasharray={n.type === 'decision' ? '5 3' : located ? '6 3' : undefined}
                      style={{ cursor: mode === 'edit' ? 'move' : 'pointer' }} />
                    {located && (
                      <circle cx={n.x + n.width / 2} cy={n.y + n.height / 2} r={34} fill="none" stroke="#EF4444" strokeWidth="1.6" opacity="0.7">
                        <animate attributeName="r" from="22" to="40" dur="0.9s" repeatCount="indefinite" />
                        <animate attributeName="opacity" from="0.8" to="0" dur="0.9s" repeatCount="indefinite" />
                      </circle>
                    )}
                    <circle cx={n.x + 16} cy={n.y + 17} r={5} className={s.bg} />
                    <text x={n.x + 28} y={n.y + 21} fontSize="11.5" fontWeight="600" className="fill-slate-700 dark:fill-slate-200" pointerEvents="none">{label}</text>
                    <text x={n.x + 16} y={n.y + 42} fontSize="9" className="fill-slate-400" pointerEvents="none">{sub.length > 20 ? `${sub.slice(0, 20)}…` : sub}</text>
                    {located && (
                      <text x={n.x + n.width - 8} y={n.y - 6} fontSize="9" fontWeight="600" textAnchor="end" fill="#EF4444" pointerEvents="none">问题节点</text>
                    )}
                    {/* 端口（编辑模式）：右侧输出；拖线接近的节点端口高亮放大提示可落点 */}
                    {mode === 'edit' && (
                      <>
                        <circle cx={n.x + n.width} cy={n.y + n.height / 2} r={16} fill="transparent" className="node-port-hit" style={{ cursor: 'crosshair' }}
                          onPointerDown={(e) => onPortPointerDown(e, n)}>
                          <title>从这里拖出连线到目标节点（靠近会自动吸附）</title>
                        </circle>
                        <circle cx={n.x + n.width} cy={n.y + n.height / 2}
                          r={dropping ? 9 : n.id === drag.current?.id ? 7.5 : 6}
                          fill={dropping ? '#10B981' : '#2563EB'} stroke="white" strokeWidth={1.6} pointerEvents="none"
                          className={dropping ? 'node-port animate-pulse' : 'node-port'} />
                        <circle cx={n.x} cy={n.y + n.height / 2} r={5} fill="#CBD5E1" opacity="0.6" pointerEvents="none" />
                      </>
                    )}
                    {/* 节点删除按钮（编辑模式）：选中常显，悬停节点也显示，热区放大 */}
                    {mode === 'edit' && n.type !== 'start' && n.type !== 'end' && (sel || hoverNodeId === n.id) && (
                      <g className="cursor-pointer" onPointerDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); deleteNode(n.id) }}>
                        <circle cx={n.x + n.width - 8} cy={n.y - 8} r={11} fill="transparent" />
                        <circle cx={n.x + n.width - 8} cy={n.y - 8} r={9} fill={sel ? '#DC2626' : '#EF4444'} opacity={sel ? 1 : 0.75} />
                        <path d={`M ${n.x + n.width - 12} ${n.y - 12} l 8 8 M ${n.x + n.width - 4} ${n.y - 12} l -8 8`} stroke="white" strokeWidth="1.8" strokeLinecap="round" pointerEvents="none" />
                        <title>删除节点「{n.label}」及其关联连线（Delete）</title>
                      </g>
                    )}
                  </g>
                )
              })}

              {/* 选中边：删除按钮 + 拖弯手柄 */}
              {mode === 'edit' && selectedEdge && (() => {
                const key = selectedEdge
                const [a, b] = key.startsWith('fb-')
                  ? key.slice(3).split('-')
                  : key.split('-')
                const A = pos(a), B = pos(b)
                if (!A || !B) return null
                const cx = (A.x + A.width / 2 + B.x + B.width / 2) / 2, cy = (A.y + A.height / 2 + B.y + B.height / 2) / 2
                const isFb = key.startsWith('fb-')
                return (
                  <g>
                    <g className="cursor-pointer" onClick={(e) => { e.stopPropagation(); if (isFb) deleteFallback(key); else deleteEdge(key) }}>
                      <rect x={cx - 16} y={cy - 16} width={32} height={32} rx={8} fill="#DC2626" />
                      <path d={`M ${cx - 6} ${cy - 6} l 12 12 M ${cx + 6} ${cy - 6} l -12 12`} stroke="white" strokeWidth="2" strokeLinecap="round" />
                      <title>删除选中连线（Delete）</title>
                    </g>
                    {!isFb && (
                      <g className="cursor-ns-resize" onPointerDown={(e) => beginEdgeDrag(key, e)}>
                        <circle cx={cx} cy={cy + 34} r={12} fill="transparent" />
                        <circle cx={cx} cy={cy + 34} r={8} fill="#2563EB" stroke="white" strokeWidth="1.6" />
                        <path d={`M ${cx - 3.5} ${cy + 32} h 7 M ${cx - 3.5} ${cy + 36} h 7`} stroke="white" strokeWidth="1.4" strokeLinecap="round" pointerEvents="none" />
                        <title>上下拖动调整连线弯曲度（自动保存到草稿）</title>
                      </g>
                    )}
                  </g>
                )
              })()}
            </svg>

            {/* 缩放控件 */}
            <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/95 px-1 py-1 shadow-s dark:border-slate-700 dark:bg-slate-900/95">
              <button className="h-7 w-7 rounded-md text-[14px] font-medium text-slate-500 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-800" title="缩小" onClick={() => applyZoom(1.2)}>−</button>
              <span className="w-10 text-center text-[11px] font-medium text-slate-500">{Math.round(zoom * 100)}%</span>
              <button className="h-7 w-7 rounded-md text-[14px] font-medium text-slate-500 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-800" title="放大" onClick={() => applyZoom(1 / 1.2)}>＋</button>
              <button className="h-7 rounded-md px-2 text-[11px] font-medium text-slate-500 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-800" title="适配视图" onClick={fitView}>适配</button>
            </div>
            {mode === 'edit' && (
              <div className="absolute bottom-3 left-3 rounded-md bg-white/90 px-2 py-1 text-[10.5px] text-slate-400 shadow-s dark:bg-slate-900/90">
                滚轮缩放 · 拖拽空白平移
              </div>
            )}
          </div>

          {/* 底部工具条 */}
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
            {mode === 'edit' ? (
              <>
                <div className="relative">
                  <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-blue-700" onClick={() => setAddOpen(!addOpen)}>
                    <Plus className="h-4 w-4" />添加节点
                  </button>
                  {addOpen && (
                    <div className="absolute bottom-full left-0 z-20 mb-2 w-[220px] rounded-xl border border-slate-200 bg-white p-2 shadow-l dark:border-slate-700 dark:bg-slate-900">
                      {addableTypes.map((t) => {
                        const exists = typeExists(t)
                        return (
                          <button key={t} onClick={() => addNode(t)} disabled={exists}
                            title={exists ? `画布已存在${nodeStyle[t].label}节点（全局唯一）` : typeGuide[t].what}
                            className={cn('flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[12.5px] font-medium',
                              exists ? 'cursor-not-allowed text-slate-300 dark:text-slate-600' : 'text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800')}>
                            <span className={cn('h-2.5 w-2.5 rounded-full', nodeStyle[t].bg)} />
                            {nodeStyle[t].label}
                            {exists && <span className="text-[10.5px] text-amber-500">已存在</span>}
                            <span className="ml-auto text-[10.5px] font-normal text-slate-400">{typeLine[t]}</span>
                          </button>
                        )
                      })}
                    </div>
                  )}
                </div>
                <span className="text-[11.5px] text-slate-400">拖拽节点移动 · 右侧蓝点拖到目标节点创建主线 · 属性面板设置回退</span>
                <button className="flex items-center gap-1 text-[11.5px] font-medium text-slate-500 hover:text-blue-600" title="按流转顺序分层蛇形重排" onClick={autoLayout}>
                  <LayoutGrid className="h-3.5 w-3.5" />自动整理
                </button>
                <button className="ml-auto flex items-center gap-1 text-[11.5px] font-medium text-blue-600 hover:underline" onClick={runCheck}>
                  <ShieldCheck className="h-3.5 w-3.5" />静态校验
                </button>
              </>
            ) : (
              <>
                <span className="text-[11.5px] text-slate-400">滚轮缩放 · 拖拽空白平移 · 点击节点查看属性</span>
                <button className="flex items-center gap-1 text-[11.5px] font-medium text-slate-500 hover:text-blue-600" title="按流转顺序分层蛇形重排" onClick={autoLayout}>
                  <LayoutGrid className="h-3.5 w-3.5" />自动整理
                </button>
                <button className="ml-auto inline-flex items-center gap-1 font-medium text-blue-600 hover:underline" onClick={enterEdit}>
                  <Zap className="h-3.5 w-3.5" />进入编辑模式
                </button>
              </>
            )}
          </div>
        </div>

        {/* ============ 属性面板 ============ */}
        <div className="space-y-4 self-start">
          <div className={cn('rounded-xl border bg-white p-5 shadow-s dark:bg-slate-900',
            mode === 'edit' ? 'border-blue-200 dark:border-blue-500/30' : 'border-slate-200 dark:border-slate-700')}>
            {!selected ? (
              <div className="py-6 text-center text-[12px] text-slate-400">画布加载中…（节点数据来自后端模板画布）</div>
            ) : (
            <>
            <div className="mb-1 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={cn('h-2.5 w-2.5 rounded-full', st.bg)} />
                <span className={cn('text-[11.5px] font-semibold tracking-wide', st.text)}>{selected.cfg.typeLine}</span>
              </div>
              {mode === 'edit' && <Badge tone="cyn"><Eye className="mr-1 h-3 w-3" />可编辑</Badge>}
            </div>

            {/* 类型说明：用途 + 操作步骤（帮助理解该类型节点是什么、怎么操作） */}
            {(() => {
              const guide = typeGuide[selected.type]
              return (
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                  <div className={cn('text-[12px] font-semibold', st.text)}>{st.label}节点 · 是什么</div>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500 dark:text-slate-400">{guide.what}</p>
                  <div className="mt-2 text-[11px] font-semibold text-slate-500 dark:text-slate-400">怎么操作：</div>
                  <ol className="mt-1 space-y-1">
                    {guide.steps.map((s, i) => (
                      <li key={i} className="flex gap-1.5 text-[11.5px] leading-snug text-slate-500 dark:text-slate-400">
                        <span className="flex-none font-bold text-slate-400">{i + 1}.</span>
                        <span>{s}</span>
                      </li>
                    ))}
                  </ol>
                </div>
              )
            })()}

            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">节点名称</label>
                <input className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                  value={selected.label}
                  disabled={mode !== 'edit'}
                  onChange={(e) => updateNode(selected.id, { label: e.target.value })} />
              </div>
              <div>
                <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">节点目的</label>
                <textarea className="min-h-[56px] w-full rounded-lg border border-slate-300 bg-white p-2.5 text-[12.5px] leading-relaxed outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:disabled:bg-slate-800/50"
                  value={selected.cfg.purpose}
                  disabled={mode !== 'edit'}
                  onChange={(e) => updateNodeCfg(selected.id, { purpose: e.target.value })} />
              </div>
              <div>
                <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">处理主体</label>
                <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:disabled:bg-slate-800/50"
                  disabled={mode !== 'edit'}
                  value={selected.cfg.handler}
                  onChange={(e) => updateNodeCfg(selected.id, { handler: e.target.value })}>
                  <option>人工 + Expert 可协助</option><option>人工</option><option>Expert 自动</option>
                </select>
              </div>

              {/* SLA / Schema / Output 编辑 */}
              <div>
                <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">SLA</label>
                <input className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:disabled:bg-slate-800/50"
                  value={selected.cfg.sla} disabled={mode !== 'edit'}
                  onChange={(e) => updateNodeCfg(selected.id, { sla: e.target.value })} />
              </div>
              {/* 表单 Schema（结构化字段） */}
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="text-[12px] font-medium text-slate-500 dark:text-slate-400">表单 Schema</label>
                  {mode === 'edit' && <Badge tone="info">{selected.cfg.schema.length} 字段</Badge>}
                </div>
                <div className="space-y-1">
                  {selected.cfg.schema.map((f, i) => (
                    <details key={f.key} className="rounded-md bg-slate-50 dark:bg-slate-800/60">
                      <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5 text-[12px] marker:content-none">
                        <span className={cn('min-w-0 flex-1 truncate font-medium', f.required ? 'text-slate-700 dark:text-slate-200' : 'text-slate-500 dark:text-slate-400')}>
                          {f.label}{f.required && <span className="text-red-500"> *</span>}
                        </span>
                        <span className="flex-none text-[10.5px] text-slate-400">{fieldTypeLabel[f.type]}</span>
                        {mode === 'edit' && (
                          <>
                            <button title="上移" onClick={(e) => { e.preventDefault(); moveSchemaField(i, -1) }} className="flex-none text-slate-300 hover:text-blue-500">↑</button>
                            <button title="下移" onClick={(e) => { e.preventDefault(); moveSchemaField(i, 1) }} className="flex-none text-slate-300 hover:text-blue-500">↓</button>
                            <button
                              title="切换必填/选填"
                              onClick={(e) => { e.preventDefault(); updateSchemaField(i, { required: !f.required }) }}
                              className={cn('flex-none rounded px-1.5 py-0.5 text-[10px] font-semibold transition-colors',
                                f.required ? 'bg-red-50 text-red-500 dark:bg-red-500/15' : 'bg-slate-200 text-slate-400 hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-500')}>
                              {f.required ? '必填' : '选填'}
                            </button>
                            <button title="删除字段" onClick={(e) => { e.preventDefault(); removeSchemaField(i) }} className="flex-none text-slate-300 transition-colors hover:text-red-500">
                              <X className="h-3 w-3" />
                            </button>
                          </>
                        )}
                      </summary>
                      <div className="space-y-1.5 border-t border-slate-200 px-2.5 py-2 dark:border-slate-700">
                        <input className="h-7 w-full rounded border border-slate-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-blue-400 disabled:bg-slate-100 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                          value={f.placeholder ?? ''} disabled={mode !== 'edit'} placeholder="占位提示（placeholder）"
                          onChange={(e) => updateSchemaField(i, { placeholder: e.target.value })} />
                        <input className="h-7 w-full rounded border border-slate-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-blue-400 disabled:bg-slate-100 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                          value={f.hint ?? ''} disabled={mode !== 'edit'} placeholder="字段说明（hint）"
                          onChange={(e) => updateSchemaField(i, { hint: e.target.value })} />
                        {(f.type === 'select' || f.type === 'multiselect' || f.type === 'radio') && (
                          <textarea className="min-h-[44px] w-full rounded border border-slate-300 bg-white p-1.5 text-[11.5px] outline-none focus:border-blue-400 disabled:bg-slate-100 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                            value={(f.options ?? []).map((o) => o.value === o.label ? o.label : `${o.label}=${o.value}`).join('\n')}
                            disabled={mode !== 'edit'} placeholder={'选项，每行一项：显示文本=值\n如：通过=pass'}
                            onChange={(e) => setOptionsText(i, e.target.value)} />
                        )}
                      </div>
                    </details>
                  ))}
                  {selected.cfg.schema.length === 0 && (
                    <div className="rounded-md border border-dashed border-slate-300 px-2.5 py-2 text-center text-[11.5px] text-slate-400 dark:border-slate-700">
                      未配置表单字段
                    </div>
                  )}
                </div>
                {mode === 'edit' && (
                  <AddFieldRow onAdd={(label, type) => addSchemaField(label, type)} />
                )}
              </div>
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <label className="text-[12px] font-medium text-slate-500 dark:text-slate-400">产出契约（任务书 / AI 简报共用）</label>
                </div>
                {(() => {
                  const d = selected.cfg.deliverable
                  const dv: NodeDeliverable = {
                    instruction: d?.instruction ?? (selected.cfg.output && selected.cfg.output !== '待配置' ? selected.cfg.output : ''),
                    acceptance: d?.acceptance ?? [],
                    aiGuidance: d?.aiGuidance ?? '',
                    example: d?.example ?? '',
                  }
                  return (
                    <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/40">
                      <div>
                        <label className="mb-1 block text-[11px] font-medium text-slate-400">产出要求说明（到达节点时展示给处理人与 AI）</label>
                        <textarea className="min-h-[52px] w-full rounded-md border border-slate-300 bg-white p-2 text-[12px] outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                          value={dv.instruction} disabled={mode !== 'edit'}
                          placeholder="本节点要求产出的内容、口径与边界…"
                          onChange={(e) => updateDeliverable({ instruction: e.target.value })} />
                      </div>
                      <div>
                        <div className="mb-1 flex items-center justify-between">
                          <label className="text-[11px] font-medium text-slate-400">验收标准（提交前必须逐条勾选确认）</label>
                          {mode === 'edit' && <button className="inline-flex items-center gap-0.5 text-[11px] font-medium text-blue-600 hover:underline" onClick={addAcceptanceItem}><Plus className="h-3 w-3" />添加</button>}
                        </div>
                        <div className="space-y-1">
                          {dv.acceptance.map((item, i) => (
                            <div key={item.key} className="space-y-1 rounded-md bg-white px-2 py-1.5 dark:bg-slate-900">
                              <div className="flex items-center gap-1.5">
                                <span className="flex-none text-[10px] font-semibold text-slate-400">{i + 1}.</span>
                                <input className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-[12px] outline-none focus:border-blue-400 disabled:text-slate-500 dark:text-slate-200"
                                  value={item.text} disabled={mode !== 'edit'} placeholder="验收条目…"
                                  onChange={(e) => updateAcceptanceItem(i, { text: e.target.value })} />
                                {mode === 'edit' && <button onClick={() => removeAcceptanceItem(i)} className="flex-none text-slate-300 hover:text-red-500"><X className="h-3 w-3" /></button>}
                              </div>
                              <input className="w-full rounded border border-transparent bg-transparent px-1 pb-0.5 text-[10.5px] text-slate-400 outline-none focus:border-blue-400 disabled:text-slate-500"
                                value={item.hint ?? ''} disabled={mode !== 'edit'} placeholder="验证方式 / 证据提示（可选）"
                                onChange={(e) => updateAcceptanceItem(i, { hint: e.target.value })} />
                            </div>
                          ))}
                          {!dv.acceptance.length && (
                            <div className="rounded-md border border-dashed border-slate-300 px-2 py-1.5 text-center text-[11px] text-slate-400 dark:border-slate-700">
                              未配置验收标准（不强制勾选）
                            </div>
                          )}
                        </div>
                      </div>
                      <div>
                        <label className="mb-1 block text-[11px] font-medium text-slate-400">AI 执行指引（仅写给 Expert 的补充指令与边界）</label>
                        <textarea className="min-h-[44px] w-full rounded-md border border-slate-300 bg-white p-2 text-[12px] outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                          value={dv.aiGuidance ?? ''} disabled={mode !== 'edit'} placeholder="例如：引用上游表单结论；不得编造数据…"
                          onChange={(e) => updateDeliverable({ aiGuidance: e.target.value })} />
                      </div>
                      {mode === 'edit' ? (
                        <div>
                          <label className="mb-1 block text-[11px] font-medium text-slate-400">参考示例（可选，展开填写）</label>
                          <textarea className="min-h-[40px] w-full rounded-md border border-slate-300 bg-white p-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                            value={dv.example ?? ''} placeholder="符合要求的产出示例…"
                            onChange={(e) => updateDeliverable({ example: e.target.value })} />
                        </div>
                      ) : (
                        dv.example && <p className="truncate text-[11px] text-slate-400">示例：{dv.example}</p>
                      )}
                    </div>
                  )
                })()}
              </div>

              {/* 子任务拆分模式（仅任务节点）：拆分后子任务在下一节点独立流转 */}
              {selected.type === 'task' && (
                <div>
                  <label className="mb-1 block text-[12px] font-medium text-slate-500 dark:text-slate-400">子任务拆分</label>
                  <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 disabled:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:disabled:bg-slate-800/50"
                    disabled={mode !== 'edit'}
                    value={selected.cfg.split?.mode ?? 'off'}
                    onChange={(e) => setSplitMode(e.target.value as SplitMode)}>
                    <option value="off">关闭（不可拆分）</option>
                    <option value="manual">人工拆分（处理人手动拆）</option>
                    <option value="ai_assist">AI 建议 + 人工确认（推荐）</option>
                    <option value="ai_auto">Expert 自动拆分（需绑定 Expert Deployment）</option>
                  </select>
                  <p className="mt-1 text-[10.5px] leading-snug text-slate-400">
                    拆分后子任务在下一节点独立流转；要求本节点只有一条后继分支{(selected.cfg.split?.mode ?? 'off') === 'ai_auto' && '，且需在下方绑定 Expert Deployment'}
                    {['ai_assist', 'ai_auto'].includes(selected.cfg.split?.mode ?? 'off') && (selected.cfg.deliverable?.instruction ?? selected.cfg.output) ? '' : '；建议先填写产出契约说明拆分口径'}
                  </p>
                </div>
              )}

              {/* 回退目标（编辑模式） */}
              {mode === 'edit' && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
                  <div className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold text-amber-700 dark:text-amber-300">
                    <Undo2 className="h-3.5 w-3.5" />回退目标
                  </div>
                  <div className="flex gap-2">
                    <select
                      id="fbTarget"
                      className="h-8 min-w-0 flex-1 rounded-md border border-amber-300 bg-white px-2 text-[12px] outline-none focus:border-amber-500 dark:border-amber-500/40 dark:bg-slate-900 dark:text-slate-200"
                      defaultValue="">
                      <option value="" disabled>选择目标节点…</option>
                      {nodes.filter((n) => n.id !== selected.id).map((n) => (
                        <option key={n.id} value={n.id}>{n.label}（{n.sub}）</option>
                      ))}
                    </select>
                    <button className="rounded-md bg-amber-600 px-2.5 text-[12px] font-medium text-white hover:bg-amber-700"
                      onClick={() => {
                        const sel = document.getElementById('fbTarget') as HTMLSelectElement
                        if (sel?.value) { addFallback(selected.id, sel.value); sel.value = '' }
                        else toast('请先选择回退目标节点')
                      }}>
                      添加
                    </button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {fallbacks.filter(([a]) => a === selected.id).map(([a, b]) => (
                      <span key={`${a}-${b}`} className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-500/20 dark:text-amber-300">
                        → {nodes.find((n) => n.id === b)?.label}
                        <button onClick={() => deleteFallback(`fb-${a}-${b}`)} className="text-amber-500 hover:text-red-500"><X className="h-3 w-3" /></button>
                      </span>
                    ))}
                    {fallbacks.filter(([a]) => a === selected.id).length === 0 && (
                      <span className="text-[11px] text-amber-600/70 dark:text-amber-400/60">暂无回退目标（退回须从模板允许目标中选择）</span>
                    )}
                  </div>
                </div>
              )}

              {/* 删除节点（属性面板入口，编辑模式；start/end 不可删） */}
              {mode === 'edit' && selected.type !== 'start' && selected.type !== 'end' && (
                <button className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] font-medium text-red-600 transition-colors hover:bg-red-100 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400 dark:hover:bg-red-500/20"
                  onClick={() => { if (window.confirm(`删除节点「${selected.label}」及其关联连线？`)) deleteNode(selected.id) }}>
                  <Trash2 className="h-4 w-4" />删除此节点（含关联连线）
                </button>
              )}

              {/* 节点类型差异配置：不同类型节点提供不同操作（决策分支 / 定时时长 / 并行分支数） */}
              {mode === 'edit' && selected.type === 'decision' && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-amber-700 dark:text-amber-300">
                    <GitBranch className="h-3.5 w-3.5" />分支条件（决策节点按表单值选择下一分支）
                  </div>
                  {(() => {
                    const outEdges = edges.filter(([a]) => a === selected.id).map(([, b]) => b)
                    const branches = selected.cfg.branches ?? {}
                    return outEdges.length > 1 ? (
                      <>
                        <div className="space-y-1.5">
                          {outEdges.map((to) => {
                            const node = nodes.find((n) => n.id === to)
                            const cond = branches[to] ?? { field: '', op: 'eq', value: '' }
                            return (
                              <div key={to} className="flex items-center gap-1.5 text-[11.5px]">
                                <span className="min-w-0 flex-1 truncate font-medium text-slate-700 dark:text-slate-200">→ {node?.label ?? to}</span>
                                <input
                                  className="h-7 w-20 rounded-md border border-amber-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-amber-500 dark:border-amber-500/40 dark:bg-slate-900 dark:text-slate-200"
                                  placeholder="字段" value={cond.field}
                                  onChange={(e) => updateNodeCfg(selected.id, { branches: { ...branches, [to]: { ...cond, field: e.target.value } } })} />
                                <select
                                  className="h-7 rounded-md border border-amber-300 bg-white px-1 text-[11.5px] outline-none dark:border-amber-500/40 dark:bg-slate-900 dark:text-slate-200"
                                  value={cond.op}
                                  onChange={(e) => updateNodeCfg(selected.id, { branches: { ...branches, [to]: { ...cond, op: e.target.value } } })}>
                                  <option value="eq">等于</option>
                                  <option value="ne">不等于</option>
                                  <option value="contains">包含</option>
                                </select>
                                <input
                                  className="h-7 w-24 rounded-md border border-amber-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-amber-500 dark:border-amber-500/40 dark:bg-slate-900 dark:text-slate-200"
                                  placeholder="值" value={cond.value}
                                  onChange={(e) => updateNodeCfg(selected.id, { branches: { ...branches, [to]: { ...cond, value: e.target.value } } })} />
                              </div>
                            )
                          })}
                        </div>
                        <div className="mt-2 flex items-center gap-1.5 text-[11.5px] text-amber-700 dark:text-amber-300">
                          <span>默认分支（无条件匹配时）</span>
                          <select
                            className="h-7 rounded-md border border-amber-300 bg-white px-1 text-[11.5px] outline-none dark:border-amber-500/40 dark:bg-slate-900 dark:text-slate-200"
                            value={selected.cfg.defaultBranch ?? ''}
                            onChange={(e) => updateNodeCfg(selected.id, { defaultBranch: e.target.value })}>
                            <option value="">选择默认分支</option>
                            {outEdges.map((to) => <option key={to} value={to}>{nodes.find((n) => n.id === to)?.label ?? to}</option>)}
                          </select>
                        </div>
                      </>
                    ) : (
                      <p className="text-[11.5px] leading-relaxed text-amber-600/80 dark:text-amber-400/70">决策节点需要 <b>2 条以上出边</b> 才能配置分支条件（从节点右侧端口拖出多根连线）。</p>
                    )
                  })()}
                </div>
              )}

              {mode === 'edit' && selected.type === 'timer' && (
                <div className="rounded-lg border border-pink-200 bg-pink-50 p-3 dark:border-pink-500/30 dark:bg-pink-500/10">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-pink-700 dark:text-pink-300">
                    <Clock className="h-3.5 w-3.5" />定时等待（到达后等待指定时长再流转）
                  </div>
                  <div className="flex items-center gap-1.5 text-[11.5px] text-pink-700 dark:text-pink-300">
                    <input type="number" min={0} className="h-7 w-24 rounded-md border border-pink-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-pink-500 dark:border-pink-500/40 dark:bg-slate-900 dark:text-slate-200"
                      value={selected.cfg.waitHours ?? 24}
                      onChange={(e) => updateNodeCfg(selected.id, { waitHours: Math.max(0, Number(e.target.value) || 0) })} />
                    <span>小时</span>
                  </div>
                </div>
              )}

              {mode === 'edit' && selected.type === 'parallel_split' && (
                <div className="rounded-lg border border-violet-200 bg-violet-50 p-3 dark:border-violet-500/30 dark:bg-violet-500/10">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-violet-700 dark:text-violet-300">
                    <GitBranch className="h-3.5 w-3.5" />并行分叉（同时生成多个分支任务）
                  </div>
                  <div className="flex items-center gap-1.5 text-[11.5px] text-violet-700 dark:text-violet-300">
                    <input type="number" min={2} max={8} className="h-7 w-20 rounded-md border border-violet-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-violet-500 dark:border-violet-500/40 dark:bg-slate-900 dark:text-slate-200"
                      value={selected.cfg.branchCount ?? 2}
                      onChange={(e) => updateNodeCfg(selected.id, { branchCount: Math.min(8, Math.max(2, Number(e.target.value) || 2)) })} />
                    <span>个分支</span>
                  </div>
                </div>
              )}

              <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-[12px] font-semibold text-slate-500 dark:text-slate-400">Expert Deployment（节点级）</span>
                  {mode === 'edit' && <Badge tone={selected.cfg.expert?.expertDeploymentId ? 'suc' : 'gry'} className="!px-1.5 !text-[10px]">{selected.cfg.expert?.expertDeploymentId ? '已绑定' : '未绑定'}</Badge>}
                </div>
                {mode === 'edit' ? (
                  <div className="space-y-2">
                    <select
                      className="h-8 w-full rounded-md border border-slate-300 bg-white px-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-200"
                       value={selected.cfg.expert?.expertDeploymentId ?? ''}
                       onChange={(e) => {
                         const expertDeploymentId = e.target.value
                         updateNodeCfg(selected.id, { expert: expertDeploymentId ? { expertDeploymentId } : undefined })
                       }}>
                       <option value="">不绑定 Expert（仅人工处理）</option>
                       {deployments.map((deployment) => (
                         <option key={deployment.id} value={deployment.id}>{deployment.name}（{deployment.environment} · {deployment.alias || '无别名'}）</option>
                       ))}
                     </select>
                     {selected.cfg.expert?.expertDeploymentId ? (
                       <p className="text-[11px] leading-relaxed text-slate-400">
                         已绑定 Expert Deployment；运行固定使用其发布版本，由 LangGraph 执行。
                         {selected.cfg.handler === 'Expert 自动' && <span className="text-amber-600 dark:text-amber-400">「Expert 自动」节点流转时自动创建 Run，写操作将在审批处中断。</span>}
                       </p>
                     ) : (
                       <p className="text-[11px] leading-relaxed text-slate-400">绑定已发布的 Expert Deployment 后，流转到该节点时将创建可追溯 Run。</p>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                     {selected.cfg.expert?.expertDeploymentId ? (
                       <span className="cap-tag cap-confirm">{deployments.find((item) => item.id === selected.cfg.expert?.expertDeploymentId)?.name ?? selected.cfg.expert.expertDeploymentId} · 已绑定</span>
                     ) : (
                       <span className="cap-tag cap-forbid">未绑定 Expert Deployment</span>
                    )}
                  </div>
                )}
                 <p className="mt-2 text-[11px] leading-relaxed text-slate-400">节点配置只能限制 Expert 运行范围，不能扩大授权用户权限。</p>
              </div>

              {mode === 'edit' ? (
                <div className="flex gap-2">
                  <button className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-blue-600 py-2 text-[12.5px] font-medium text-white hover:bg-blue-700"
                    onClick={() => { setSnapshotDraft(); toast.success(`节点「${selected.label}」配置已保存`) }}>
                    <Check className="h-4 w-4" />保存配置
                  </button>
                  {selected.type !== 'start' && selected.type !== 'end' && (
                    <button className="flex items-center justify-center gap-1.5 rounded-lg border border-red-200 px-3 py-2 text-[12.5px] font-medium text-red-500 hover:bg-red-50 dark:border-red-500/30 dark:hover:bg-red-500/10"
                      onClick={() => deleteNode(selected.id)}>
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              ) : (
                <div className="flex items-center gap-2 rounded-lg bg-slate-50 p-2.5 text-[11.5px] text-slate-400 dark:bg-slate-800/60">
                  <LockIcon /> 只读视图：进入编辑模式后可修改节点与回退配置
                </div>
              )}
            </div>
            </>
            )}
          </div>

          {mode === 'view' ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-[12px] leading-relaxed text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
              <b className="mb-1 flex items-center gap-1.5"><Undo2 className="h-4 w-4" />回退路径</b>
              测试与验收节点回退目标已在模板中限定；退回必须选择模板允许目标并填写原因（PRD §6.2）。进入编辑模式可增删回退路径。
            </div>
          ) : (
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-[12px] leading-relaxed text-slate-500 shadow-s dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
              <b className="mb-1 flex items-center gap-1.5"><Link2 className="h-4 w-4" />编辑提示</b>
              <ul className="list-inside list-disc space-y-1 text-[11.5px]">
                <li>拖拽节点移动（自动吸附 8px 网格）</li>
                <li>节点右侧<b className="text-blue-600 dark:text-blue-400">蓝点</b>拖到目标节点创建主线</li>
                <li>点击边高亮后按 Delete 或点击红色 ✕ 删除</li>
                <li>「添加节点」支持 7 种类型（任务/决策/并行/验收/闭环/定时）</li>
                <li>属性面板可编辑名称/目的/处理主体/SLA/Schema/产出物</li>
                <li>取消编辑恢复进入前快照，「保存草稿」暂存，「发布」自动创建新版本并发布</li>
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function LockIcon() {
  return (
    <svg className="flex-none" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  )
}

/* Schema 添加字段行 */
function AddFieldRow({ onAdd }: { onAdd: (label: string, type: FormFieldType) => void }) {
  const [label, setLabel] = useState('')
  const [type, setType] = useState<FormFieldType>('input')
  return (
    <div className="mt-1.5 flex items-center gap-1.5">
      <input
        className="h-8 min-w-0 flex-1 rounded-md border border-slate-300 bg-white px-2 text-[12px] outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        placeholder="新字段名称…"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && label.trim()) { onAdd(label, type); setLabel('') } }}
      />
      <select
        className="h-8 flex-none rounded-md border border-slate-300 bg-white px-1.5 text-[11.5px] outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
        value={type}
        onChange={(e) => setType(e.target.value as FormFieldType)}
      >
        {(Object.keys(fieldTypeLabel) as FormFieldType[]).map((t) => (
          <option key={t} value={t}>{fieldTypeLabel[t]}</option>
        ))}
      </select>
      <button
        className="flex-none rounded-md bg-blue-600 px-2 py-1.5 text-[11.5px] font-medium text-white hover:bg-blue-700"
        onClick={() => { if (label.trim()) { onAdd(label, type); setLabel('') } else toast('请填写字段名称') }}
      >
        <Plus className="h-3 w-3" />
      </button>
    </div>
  )
}
