import { useEffect, useState } from 'react'
import { Plus, Eye } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Badge, PageHeader, type Tone } from '../components/common'
import type { TemplateVersion } from '../types'

const statusMap: Record<string, [Tone, string]> = {
  published: ['suc', '已发布'], draft: ['warn', '草稿'], reviewing: ['pur', '评审中'],
  deprecated: ['gry', '已废弃'], archived: ['blk', '已归档'],
}

function TemplateRow({ name, desc, icon, iconGrad, versions, onOpen, boundCount, tplId, onRename, onDelete }: {
  name: string; desc: string; icon: string; iconGrad: string; versions: TemplateVersion[]; onOpen: () => void; boundCount: number; tplId: string
  onRename: (id: string, name: string) => void; onDelete: (id: string, name: string) => void
}) {
  const { openCanvas } = useApp()
  const latest = versions[0]
  const [tone, label] = latest ? statusMap[latest.status] : ['gry' as Tone, '暂无版本']
  return (
    <div className="flex items-center gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-s transition-all hover:border-blue-300 dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/40">
      <span className={cn('flex h-10 w-10 flex-none items-center justify-center rounded-lg text-white', iconGrad)}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <button className="text-[14.5px] font-semibold text-slate-800 hover:text-blue-600 dark:text-slate-100" onClick={onOpen}>{name}</button>
          <Badge tone={tone}>{label}</Badge>
          <Badge tone="cyn" className="!px-2 !text-[10.5px]">被 {boundCount} 个项目绑定</Badge>
          <span className="text-[11.5px] text-slate-400">{latest ? `${latest.version} · ${latest.nodes} 节点` : '版本加载中…'}</span>
        </div>
        <div className="mt-0.5 text-[12px] text-slate-400">{desc}</div>
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-slate-400">
          {versions.map((v) => (
            <span key={v.version} className="inline-flex items-center gap-1.5">
              <button
                title={v.status === 'published' ? `预览 ${v.version}（已发布 · 只读）` : `打开 ${v.version}（${statusMap[v.status][1]}）`}
                onClick={() => openCanvas(tplId, name, v.version)}
                className={cn('group inline-flex items-center gap-1 rounded-md px-1 py-0.5 transition-colors',
                  v.status === 'published' ? 'hover:bg-emerald-50 dark:hover:bg-emerald-500/10' : 'hover:bg-amber-50 dark:hover:bg-amber-500/10')}>
                <Badge tone={statusMap[v.status][0]} className="!px-1.5 !text-[10px]">{v.version} · {v.instances} 实例</Badge>
                <Eye className="h-3 w-3 text-slate-300 transition-colors group-hover:text-blue-500" />
              </button>
              {v.updated} · {v.updatedBy}
            </span>
          ))}
        </div>
      </div>
      <div className="flex flex-none flex-col items-end gap-1.5">
        <button className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={onOpen}>打开画布</button>
        <span className="flex items-center gap-2 text-[11px] text-slate-400">
          <button className="text-slate-400 hover:text-blue-600 dark:hover:text-blue-400" onClick={() => onRename(tplId, name)}>改名</button>
          <span className="text-slate-300 dark:text-slate-600">·</span>
          <button className="text-slate-400 hover:text-red-500" onClick={() => onDelete(tplId, name)}>删除</button>
        </span>
        <span className="text-[11px] text-slate-400">点击版本号可预览</span>
      </div>
    </div>
  )
}

function cn(...args: (string | false | undefined)[]) { return args.filter(Boolean).join(' ') }

/* 模板类型文案 / 内置模板专属描述与图标 / 通用渐变（动态模板轮换） */
const BUILTIN_DESC: Record<string, string> = {
  'tpl-req': '需求提交 → 分析 → 评审 → 拆分 → 开发 → 测试 → 验收 → 发布 → 完成（可跳过专业节点）',
  'tpl-issue': '问题提报 → 分诊 → 二线分析 → 开发排查 → 修复 → 验证 → 售后确认 → 关闭',
}
const BUILTIN_ICON: Record<string, string> = { 'tpl-req': '需', 'tpl-issue': '问' }
const GRADS = [
  'bg-gradient-to-br from-blue-500 to-indigo-700',
  'bg-gradient-to-br from-amber-500 to-orange-600',
  'bg-gradient-to-br from-emerald-500 to-teal-700',
  'bg-gradient-to-br from-purple-500 to-pink-600',
  'bg-gradient-to-br from-cyan-500 to-blue-700',
  'bg-gradient-to-br from-rose-500 to-red-700',
]

export function TemplatesPage() {
  const { openCanvas } = useApp()
  /* 模板池：来自后端 GET /templates/pool（动态渲染列表） */
  const [tplPool, setTplPool] = useState<{ id: string; name: string; type: string }[]>([])
  /* 版本数据：来自后端 GET /templates/{id}/versions */
  const [versionsMap, setVersionsMap] = useState<Record<string, TemplateVersion[]>>({})
  /* 项目绑定计数：来自后端项目列表 */
  const [bindCounts, setBindCounts] = useState<Record<string, number>>({})
  const [createOpen, setCreateOpen] = useState(false)
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    api.get<{ items: { templateBindings: { templateId: string; status: string }[] }[] }>('/api/v1/projects')
      .then((d) => {
        const m: Record<string, number> = {}
        d.items.forEach((p) => p.templateBindings.forEach((b) => { if (b.status === 'active') m[b.templateId] = (m[b.templateId] ?? 0) + 1 }))
        setBindCounts(m)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    const load = async () => {
      try {
        const pool = await api.get<{ items: { id: string; name: string; type: string }[] }>('/api/v1/templates/pool')
        setTplPool(pool.items)
        const next: Record<string, TemplateVersion[]> = {}
        for (const t of pool.items) {
          try {
            const d = await api.get<{ items: TemplateVersion[] }>(`/api/v1/templates/${t.id}/versions`)
            if (d.items.length) next[t.id] = d.items
          } catch { /* 单模板失败跳过 */ }
        }
        if (Object.keys(next).length) setVersionsMap((prev) => ({ ...prev, ...next }))
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : '模板加载失败')
      }
    }
    load()
  }, [refreshTick])

  const renameTpl = async (id: string, oldName: string) => {
    const n = window.prompt('输入新的模板名称', oldName)
    if (!n || !n.trim() || n.trim() === oldName) return
    try {
      const d = await api.patch<{ name: string }>(`/api/v1/templates/${id}`, { name: n.trim() })
      toast.success(`已重命名为「${d.name}」`)
      setRefreshTick((t) => t + 1)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '改名失败')
    }
  }

  const deleteTpl = async (id: string, name: string) => {
    if (!confirm(`确认删除模板「${name}」？将级联删除其全部版本与画布。已被项目绑定或有流程实例的模板会被系统拒绝。`)) return
    try {
      await api.del(`/api/v1/templates/${id}`)
      toast.success(`已删除模板「${name}」`)
      setRefreshTick((t) => t + 1)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  return (
    <div className="page-container">
      <PageHeader
        title="流程模板"
        sub="点击版本号可预览已发布版本（只读）；草稿可在画布继续编辑；「发布」自动创建新版本并发布（PRD §4.3）"
        actions={
          <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
            onClick={() => setCreateOpen(true)}>
            <Plus className="mr-1 inline h-4 w-4" />新建模板
          </button>
        }
      />
      <div className="space-y-4">
        {tplPool.length === 0 && (
          <p className="text-sm text-slate-400">模板加载中…</p>
        )}
        {/* 平铺渲染模板池（含新建的模板，不分组） */}
        {tplPool.map((t, i) => (
                <TemplateRow
                  key={t.id}
                  name={t.name}
                  icon={BUILTIN_ICON[t.id] ?? t.name[0]?.toUpperCase() ?? '流'}
                  iconGrad={GRADS[i % GRADS.length]}
                  tplId={t.id}
                  desc={BUILTIN_DESC[t.id] ?? '自定义流程模板'}
                  versions={versionsMap[t.id] ?? []}
                  boundCount={bindCounts[t.id] ?? 0}
                  onRename={renameTpl}
                  onDelete={deleteTpl}
                  onOpen={() => openCanvas(t.id, t.name, versionsMap[t.id]?.[0]?.version ?? 'v1')}
                />
        ))}
      </div>

      {createOpen && (
        <CreateTemplateDialog
          onClose={() => setCreateOpen(false)}
          onCreated={(id, name) => {
            setCreateOpen(false)
            setRefreshTick((t) => t + 1)  // 刷新模板列表
            openCanvas(id, name, 'v1')    // 打开新模板 v1 草稿画布
          }}
        />
      )}
    </div>
  )
}

/* 新建模板对话框：名称 → 创建 → 打开新模板画布（v1 草稿） */
function CreateTemplateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string, name: string) => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!name.trim()) { toast('请输入模板名称'); return }
    setBusy(true)
    try {
      const d = await api.post<{ template: { id: string; name: string } }>('/api/v1/templates', { name: name.trim(), type: 'requirement' })
      toast.success(`已创建模板「${d.template.name}」，正在打开画布`)
      onCreated(d.template.id, d.template.name)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl dark:bg-slate-900" onClick={(e) => e.stopPropagation()}>
        <div className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">新建流程模板</div>
        <div className="mt-4 space-y-3">
          <div>
            <label className="text-[12.5px] font-medium text-slate-500 dark:text-slate-400">模板名称</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)}
              placeholder="例如：变更流程"
              onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
              className="mt-1 h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" />
          </div>
          <p className="text-[11.5px] leading-relaxed text-slate-400">创建后自动生成 v1 草稿版本，可在画布中添加节点、配置各节点表单与处理人，再「发布」上线。</p>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-slate-300 px-3.5 py-2 text-[12.5px] font-medium text-slate-500 hover:border-slate-400 dark:border-slate-700 dark:text-slate-400">取消</button>
          <button onClick={submit} disabled={busy}
            className="rounded-lg bg-blue-600 px-3.5 py-2 text-[12.5px] font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-60">
            {busy ? '创建中…' : '创建并打开画布'}
          </button>
        </div>
      </div>
    </div>
  )
}
