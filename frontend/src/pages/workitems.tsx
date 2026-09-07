import { useEffect, useState } from 'react'
import { ListChecks, Plus, Tag, Trash2 } from 'lucide-react'
import { toast, useApp } from '../store/app-store'
import { api } from '../lib/api'
import { Badge, PageHeader, priorityBadge, wiStatusBadge, type Tone } from '../components/common'
import { CreateWorkItemDialog } from '../components/dialogs'
import { Button } from '../components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { cn } from '../lib/utils'
import type { WorkItem } from '../types'

const typeLabel: Record<string, string> = { requirement: '需求线', issue: '问题线', change: '变更线' }
const typeTone: Record<string, Tone> = { requirement: 'info', issue: 'warn', change: 'pur' }
const statusLabel: Record<string, string> = {
  draft: '草稿', submitted: '已提交', in_progress: '进行中', waiting_for_information: '待补充信息',
  waiting_for_verification: '待验证', resolved: '已解决', accepted: '已验收', rejected: '已驳回',
  closed: '已关闭', cancelled: '已取消', archived: '已归档',
}
const TAG_COLORS = ['suc', 'warn', 'err', 'info', 'pur', 'cyn', 'orgx', 'gry', 'blk'] as const

interface TagItem { id: string; name: string; color: string; creator?: string; time?: string }

const PAGE_SIZE = 10

export function WorkItemsPage() {
  const { openWorkItem } = useApp()
  const [items, setItems] = useState<WorkItem[]>([])
  const [tags, setTags] = useState<TagItem[]>([])
  const [q, setQ] = useState('')
  const [label, setLabel] = useState('')
  const [project, setProject] = useState('')
  const [status, setStatus] = useState('')
  const [priority, setPriority] = useState('')
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([])

  const load = () => {
    setLoading(true)
    const params = new URLSearchParams({ page_size: '100' })
    if (q) params.set('q', q)
    if (label) params.set('label', label)
    if (project) params.set('project', project)
    if (status) params.set('status', status)
    if (priority) params.set('priority', priority)
    api.get<{ items: WorkItem[] }>(`/api/v1/work-items?${params}`)
      .then((d) => setItems(d.items))
      .catch((e) => toast.error(e instanceof Error ? e.message : '工作项加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [q, label, project, status, priority])
  useEffect(() => { setPage(1) }, [q, label, project, status, priority])
  useEffect(() => {
    api.get<{ items: { id: string; name: string }[] }>('/api/v1/projects').then((d) => setProjects(d.items)).catch(() => {})
  }, [])
  const loadTags = () => api.get<{ items: TagItem[] }>('/api/v1/tags').then((d) => setTags(d.items)).catch(() => {})
  useEffect(() => { loadTags() }, [manageOpen])

  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const paged = items.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  return (
    <div className="page-container">
      <PageHeader
        title="工作项"
        sub="全部工作项列表 · 按预定义标签（版本号等）分类筛选 · 点击标题进入详情"
        actions={
          <>
            <button className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-all hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              onClick={() => setManageOpen(true)}>
              <Tag className="h-4 w-4" />管理标签
            </button>
            <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
              onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />新建工作项
            </button>
          </>
        }
      />

      {/* 标签 pill 筛选 */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button onClick={() => setLabel('')}
          className={cn('rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors',
            label === '' ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 hover:text-blue-600 dark:bg-slate-900 dark:text-slate-400')}>
          全部 <span className="opacity-60">{items.length}</span>
        </button>
        {tags.map((t) => (
          <button key={t.id} onClick={() => setLabel(label === t.name ? '' : t.name)}
            className={cn('rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors',
              label === t.name ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 hover:text-blue-600 dark:bg-slate-900 dark:text-slate-400')}>
            {t.name}
          </button>
        ))}
        {!tags.length && <span className="text-[12px] text-slate-400">暂无预定义标签，点右上「管理标签」新增</span>}
      </div>

      {/* 搜索 + 条件筛选 */}
      <div className="mb-4 grid gap-2.5 md:grid-cols-4">
        <input className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
          placeholder="按标题搜索…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={project} onChange={(e) => setProject(e.target.value)}>
          <option value="">全部项目</option>
          {projects.map((p) => <option key={p.id} value={p.name}>{p.name}</option>)}
        </select>
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(statusLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={priority} onChange={(e) => setPriority(e.target.value)}>
          <option value="">全部优先级</option><option>P0</option><option>P1</option><option>P2</option><option>P3</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800/60">
                <th className="px-4 py-3 font-medium">标题</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">优先级</th>
                <th className="px-4 py-3 font-medium">标签</th>
                <th className="px-4 py-3 font-medium">当前处理人</th>
                <th className="px-4 py-3 font-medium">截止</th>
                <th className="px-4 py-3 font-medium">当前节点</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((w) => (
                <tr key={w.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40">
                  <td className="px-4 py-3">
                    <div className="cursor-pointer font-medium text-slate-700 hover:text-blue-600 dark:text-slate-200" onClick={() => openWorkItem(w.id)}>{w.title}</div>
                    <div className="text-[11px] text-slate-400">{w.id} · {w.project}</div>
                  </td>
                  <td className="px-4 py-3"><Badge tone={typeTone[w.type] ?? 'gry'}>{typeLabel[w.type] ?? w.type}</Badge></td>
                  <td className="px-4 py-3">{wiStatusBadge(w.status)}</td>
                  <td className="px-4 py-3">{priorityBadge(w.priority)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(w.labels ?? []).map((l) => <Badge key={l} tone={tags.find((t) => t.name === l)?.color as Tone ?? 'gry'}>{l}</Badge>)}
                      {!(w.labels ?? []).length && <span className="text-slate-300 dark:text-slate-600">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-500 dark:text-slate-400">
                    {w.assignees?.length ? (
                      <span title={w.assignees.join('、')}>
                        {w.assignees[0]}{w.assignees.length > 1 ? ` 等 ${w.assignees.length} 人` : ''}
                      </span>
                    ) : (w.assignee || '—')}
                  </td>
                  <td className="px-4 py-3 text-slate-400">{w.due || '—'}</td>
                  <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{w.progress || '—'}</td>
                </tr>
              ))}
              {!paged.length && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">{loading ? '加载中…' : '无匹配工作项，调整筛选条件后重试'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-[12px] text-slate-400 dark:border-slate-800">
          <span>共 {items.length} 个工作项 · 第 {safePage}/{totalPages} 页</span>
          <div className="flex items-center gap-1.5">
            <button className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
              safePage <= 1 ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
              disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>上一页</button>
            <button className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
              safePage >= totalPages ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
              disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>下一页</button>
          </div>
        </div>
      </div>

      {createOpen && <CreateWorkItemDialog onClose={() => { setCreateOpen(false); load() }} />}
      <TagManageDialog open={manageOpen} onClose={() => setManageOpen(false)} />
    </div>
  )
}

/* 预定义标签管理：新增（名称 + 颜色）/ 删除 */
function TagManageDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tags, setTags] = useState<TagItem[]>([])
  const [name, setName] = useState('')
  const [color, setColor] = useState<string>('gry')
  const [busy, setBusy] = useState(false)

  const loadTags = () => api.get<{ items: TagItem[] }>('/api/v1/tags').then((d) => setTags(d.items)).catch(() => {})
  useEffect(() => { if (open) loadTags() }, [open])

  const create = async () => {
    if (!name.trim()) { toast.error('标签名称不能为空'); return }
    setBusy(true)
    try {
      await api.post('/api/v1/tags', { name: name.trim(), color })
      setName('')
      toast.success(`标签「${name.trim()}」已创建`)
      loadTags()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '创建失败')
    } finally { setBusy(false) }
  }

  const remove = async (t: TagItem) => {
    try {
      await api.del(`/api/v1/tags/${t.id}`)
      toast.success(`标签「${t.name}」已删除`)
      loadTags()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    }
  }

  if (!open) return null
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader><DialogTitle className="text-[15px]">管理预定义标签</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="flex gap-2">
            <input className="h-9 flex-1 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              placeholder="标签名称，如 v1.2.0" value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') create() }} />
            <select className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={color} onChange={(e) => setColor(e.target.value)}>
              {TAG_COLORS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <Button disabled={busy} onClick={create}><Plus className="h-4 w-4" />新增</Button>
          </div>
          <div className="max-h-64 space-y-1.5 overflow-y-auto">
            {tags.map((t) => (
              <div key={t.id} className="flex items-center gap-2 rounded-lg border border-slate-100 px-3 py-2 dark:border-slate-800">
                <Badge tone={t.color as Tone}>{t.name}</Badge>
                <span className="flex-1 text-[11px] text-slate-400">{t.creator ? `${t.creator} · ${t.time}` : ''}</span>
                <button className="rounded-md p-1 text-slate-300 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-500/10" aria-label={`删除标签 ${t.name}`} onClick={() => remove(t)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            {!tags.length && (
              <div className="flex items-center gap-2 p-3 text-[12px] text-slate-400"><ListChecks className="h-4 w-4" />暂无标签，新增后可在创建工作项时绑定</div>
            )}
          </div>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>完成</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
