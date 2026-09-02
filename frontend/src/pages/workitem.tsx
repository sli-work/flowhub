import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, Paperclip, Tag, Calendar, User, Target, Inbox, StopCircle, Trash2 } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError, getToken } from '../lib/api'
import { cn } from '../lib/utils'
import {
  Badge, DocRow, SectionCard, Timeline, wiStatusBadge, priorityBadge, taskStatusBadge, EmptyState, type Tone,
} from '../components/common'
import { DocumentViewerDrawer } from '../components/document-viewer-drawer'
import type { WorkItem } from '../types'

const labelTone: Record<string, Tone> = {
  分派规则: 'pur', 值班: 'cyn', 看板: 'info', SLA: 'orgx', 去重: 'pur', AI: 'cyn',
  性能: 'err', 导出: 'info', 通知: 'cyn', 企微: 'suc', 验收: 'info',
  文件: 'cyn', 链接: 'orgx', Agent: 'pur', 审计: 'suc',
}

export function WorkItemPage() {
  const { navigate, activeWiId, openTask } = useApp()
  const [wi, setWi] = useState<WorkItem | null>(null)
  const [reload, setReload] = useState(0)
  const [instance, setInstance] = useState<{ id: string; templateId: string; version: string; currentNode: string; state: string } | null>(null)
  const [startValues, setStartValues] = useState<Record<string, unknown>>({})
  const [taskList, setTaskList] = useState<{ id: string; title?: string; node: string; status: string; assignee: string; due: string; parentTaskId?: string | null; lineageRootId?: string | null }[]>([])
  const fileRef = useRef<HTMLInputElement>(null)
  const [docs, setDocs] = useState<{ id: string; name: string; kind: string; size: string; version: string; level: string; uploader: string; time?: string }[]>([])
  const [viewer, setViewer] = useState<{ open: boolean; initialId?: string }>({ open: false })

  const refreshDocs = () => {
    if (!activeWiId) return
    api.get<{ items: { id: string; name: string; kind: string; size: string; version: string; level: string; uploader: string; time?: string }[] }>(`/api/v1/documents?wi=${activeWiId}&page_size=100`)
      .then((dd) => setDocs(dd.items))
      .catch(() => {})
  }
  /* 处理历史时间线：来自后端工作项详情任务（GET /work-items/{id} 返回 tasks） */
  const [timeline, setTimeline] = useState<{ id: string; time: string; title: string; desc: string; by: string; kind: string }[]>([])

  /* 详情 + 实例 + 起始表单值 + 任务 + 关联文档：全部按当前工作项 ID 拉取（真实数据） */
  useEffect(() => {
    if (!activeWiId) { navigate('tasks'); return }
    setWi(null); setInstance(null); setStartValues({}); setTaskList([]); setDocs([]); setTimeline([])
    api.get<{ item: WorkItem; instance: typeof instance; startValues: Record<string, unknown>; tasks: typeof taskList }>(`/api/v1/work-items/${activeWiId}`)
      .then((d) => {
        setWi(d.item); setInstance(d.instance); setStartValues(d.startValues); setTaskList(d.tasks)
        setTimeline(d.tasks.map((t) => ({
          id: t.id, time: t.due, title: `节点「${t.node}」`, desc: `任务 ${t.id} · 处理人 ${t.assignee} · ${t.status}`, by: t.assignee || '系统', kind: 'user',
        })))
      })
      .catch(() => { /* 后端不可用：保持占位 */ })
    api.get<{ items: { id: string; name: string; kind: string; size: string; version: string; level: string; uploader: string; time?: string }[] }>(`/api/v1/documents?wi=${activeWiId}&page_size=100`)
      .then((dd) => setDocs(dd.items))
      .catch(() => {})
  }, [activeWiId, navigate, reload])

  /* 手动停止：实例取消 + 未终结任务全部取消（需 workflow_instance:cancel 权限，后端校验） */
  const stopFlow = async () => {
    if (!wi) return
    if (!confirm(`确认停止工作项「${wi.title}」？流程实例将取消，未完成任务全部关闭且不可恢复。`)) return
    try {
      await api.post(`/api/v1/work-items/${wi.id}/stop`)
      toast.success('工作项已停止，流程实例已取消')
      setReload((v) => v + 1)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '停止失败')
    }
  }

  const stoppable = !!wi && !['closed', 'cancelled', 'archived'].includes(wi.status)
  /* 删除工作项：仅取消态可删（后端强校验），硬删工作项+实例+全部任务 */
  const deletable = wi?.status === 'cancelled'
  const deleteWi = async () => {
    if (!wi) return
    if (!confirm(`确认删除工作项「${wi.title}」？工作项、流程实例及全部任务将被删除且不可恢复。`)) return
    try {
      await api.del(`/api/v1/work-items/${wi.id}`)
      toast.success('工作项及其任务已删除')
      navigate('workitems')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  const pendingTask = taskList.find((t) => !['completed', 'cancelled'].includes(t.status))
  /* 去处理：打开当前待处理任务（携带真实任务 ID），无待处理则回任务列表 */
  const goHandle = () => {
    if (pendingTask) openTask(pendingTask.id, activeWiId ?? undefined)
    else { navigate('tasks'); toast('当前工作项没有待处理任务') }
  }

  /* 上传文档：multipart → POST /documents/upload（关联当前工作项） */
  const uploadDoc = async (file: File) => {
    if (!wi || !activeWiId) { toast('工作项尚未加载'); return }
    const fd = new FormData()
    fd.append('file', file)
    fd.append('project', wi.project)
    fd.append('kind', '需求文档')
    fd.append('wi', activeWiId)
    const token = getToken()
    try {
      const resp = await fetch('/api/v1/documents/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      })
      const payload = await resp.json()
      if (!resp.ok || payload.code !== 0) throw new ApiError(payload.code ?? resp.status, payload.message || '上传失败', resp.status)
      toast.success(`上传成功：${payload.data.doc.name}（校验通过并入库）`)
      refreshDocs()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '上传失败')
    }
  }

  /* 文档短时链接：POST /documents/{id}/link */
  /* 文档点击 → 统一预览抽屉（下载按钮在抽屉内） */
  const openViewer = (d: (typeof docs)[number]) => setViewer({ open: true, initialId: d.id })

  return (
    <div className="page-container">
      <button className="mb-4 flex items-center gap-1.5 text-[13px] font-medium text-slate-500 transition-colors hover:text-blue-600" onClick={() => navigate('tasks')}>
        <ArrowLeft className="h-4 w-4" />返回我的任务
      </button>

      {/* 信息头 */}
      <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <Badge tone="info">需求</Badge>
              <span className="font-mono text-[12px] text-slate-400">{wi?.id ?? '加载中…'}</span>
              {wi && priorityBadge(wi.priority)}
              {wi && wiStatusBadge(wi.status)}
              <span className="text-[12px] text-slate-400">· 流程实例 {instance ? `v${instance.version.replace(/\D/g, '') ?? instance.version}` : '—'} · {instance?.state ?? '—'}</span>
            </div>
            <h1 className="mt-2.5 text-[20px] font-semibold leading-snug text-slate-900 dark:text-slate-100">{wi?.title ?? '加载中…'}</h1>
            <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-[12.5px] text-slate-500 dark:text-slate-400">
              <span className="inline-flex items-center gap-1.5"><User className="h-3.5 w-3.5" />负责人：{wi?.assignee ?? '—'}</span>
              <span className="inline-flex items-center gap-1.5"><Inbox className="h-3.5 w-3.5" />项目：{wi?.project ?? '—'}</span>
              <span className="inline-flex items-center gap-1.5"><Calendar className="h-3.5 w-3.5" />截止：{wi?.due ?? '—'}</span>
              <span className="inline-flex items-center gap-1.5"><Tag className="h-3.5 w-3.5" />
                {(wi?.labels ?? []).map((l) => <Badge key={l} tone={labelTone[l] ?? 'gry'} className="!px-2">{l}</Badge>)}
              </span>
            </div>
          </div>
          <div className="flex flex-none items-center gap-2">
            <button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => navigate('templates')}>
              流程模板
            </button>
            {stoppable && (
              <button className="rounded-lg border border-red-200 bg-white px-3.5 py-2 text-[13px] font-medium text-red-500 transition-colors hover:border-red-400 hover:bg-red-50 dark:border-red-500/40 dark:bg-slate-900 dark:hover:bg-red-500/10" onClick={stopFlow}>
                <StopCircle className="mr-1 inline h-4 w-4" />停止流程
              </button>
            )}
            {deletable && (
              <button className="rounded-lg border border-red-200 bg-white px-3.5 py-2 text-[13px] font-medium text-red-500 transition-colors hover:border-red-400 hover:bg-red-50 dark:border-red-500/40 dark:bg-slate-900 dark:hover:bg-red-500/10" onClick={deleteWi}>
                <Trash2 className="mr-1 inline h-4 w-4" />删除工作项
              </button>
            )}
            <button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => fileRef.current?.click()}>
              <Paperclip className="mr-1 inline h-4 w-4" />上传文档
            </button>
            <input
              ref={fileRef} type="file" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadDoc(f); e.target.value = '' }}
            />
            <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={goHandle}>
              去处理
            </button>
          </div>
        </div>
        {/* 起始表单关键字段（来自发起时提交的 startValues） */}
        <div className="mt-5 grid gap-4 border-t border-slate-100 pt-5 md:grid-cols-2 dark:border-slate-800">
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-slate-500 dark:text-slate-400"><Target className="h-3.5 w-3.5" />背景 / 目标</div>
            <p className="text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{String(startValues.goal || startValues.description || '—')}</p>
          </div>
          <div>
            <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-semibold text-slate-500 dark:text-slate-400"><Tag className="h-3.5 w-3.5" />范围 / 说明</div>
            <p className="text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{String(startValues.scope || startValues.background || '—')}</p>
          </div>
        </div>
      </div>

      {/* 主体 */}
      <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_360px]">
        <div className="space-y-5">
          <SectionCard title="关联文档" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('docs')}>文档中心</button>}>
            <div className="space-y-2">
              {docs.map((d) => (
                <DocRow key={d.id} name={d.name} kind={d.kind} tone="info"
                  meta={<>{d.size} · {d.version} · {d.level} · {d.uploader}</>}
                  onClick={() => openViewer(d)} />
              ))}
            </div>
          </SectionCard>

          <SectionCard title="处理历史" extra={<button className="text-xs font-medium text-blue-600 hover:underline" onClick={() => navigate('audit')}>审计</button>}>
            {/* 点击任一历史节点条目 → 进入对应任务处理页查看（历史任务为只读回看） */}
            <Timeline events={timeline} onSelect={(id) => openTask(id, activeWiId ?? undefined)} />
          </SectionCard>
        </div>

        {/* 右侧：子工作项 / 补充信息 */}
        <div className="space-y-5">
          <SectionCard title="流程任务" extra={<Badge tone="info">{taskList.length} 个</Badge>}>
            <div className="space-y-2">
              {(() => {
                // 树形展示：主线任务（无 parentTaskId）为主干，拆分子任务递归缩进为分支；
                // 同一 lineage_root 的任务属于同一条并行子线，标「子线」徽标
                const childrenOf = (id: string) => taskList.filter((t) => t.parentTaskId === id)
                const rendered = new Set<string>()
                const render = (t: (typeof taskList)[number], depth: number): React.ReactNode => {
                  rendered.add(t.id)
                  const done = ['completed', 'cancelled'].includes(t.status)
                  const isSplitBranch = !!t.lineageRootId
                  return (
                    <div key={t.id}>
                      <div className={cn('flex items-center gap-3 rounded-lg border p-3',
                        depth > 0 ? 'ml-5 border-dashed border-slate-300 dark:border-slate-600' : 'border-slate-200 dark:border-slate-700')}>
                        <span className={done ? 'text-emerald-500' : 'text-blue-500'}>
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">
                            {isSplitBranch && <span className="mr-1 text-[10.5px] font-semibold text-violet-500">↳ 子线</span>}节点「{t.node}」
                          </div>
                          <div className="text-[11px] text-slate-400">{t.id} · 处理人 {t.assignee} · 截止 {t.due}</div>
                        </div>
                        {taskStatusBadge(t.status as Parameters<typeof taskStatusBadge>[0])}
                      </div>
                      {childrenOf(t.id).map((c) => render(c, depth + 1))}
                    </div>
                  )
                }
                // 主干按接口顺序（创建序）；拆分分支递归挂载；游离任务（父已不在列表）兜底铺在末尾
                const nodes = taskList.filter((t) => !t.parentTaskId).map((t) => render(t, 0))
                const orphans = taskList.filter((t) => !rendered.has(t.id)).map((t) => render(t, 1))
                return [...nodes, ...orphans]
              })()}
              {taskList.length === 0 && <EmptyState title="暂无流程任务" desc="工作项已创建，任务将按流程节点生成" />}
            </div>
          </SectionCard>

          <SectionCard title="补充信息">
            <div className="space-y-3">
              <EmptyState title="等待补充" desc="上游可在此节点请求更多信息" />
            </div>
          </SectionCard>
        </div>
      </div>
      <DocumentViewerDrawer open={viewer.open} docs={docs} initialDocId={viewer.initialId} onClose={() => setViewer({ open: false })} />
    </div>
  )
}
