import { useEffect, useState } from 'react'
import { FolderKanban, Plus, Users, GitCommitHorizontal, Lock, Pencil, Workflow, Archive, Trash2 } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { Avatar, Badge, PageHeader, SearchInput, type Tone } from '../components/common'
import { ProjectDialog } from '../components/dialogs'
import { api, ApiError } from '../lib/api'
import type { Project } from '../types'

const statusMap: Record<string, [Tone, string]> = {
  active: ['suc', '进行中'], draft: ['gry', '草稿'], paused: ['warn', '已暂停'],
  completed: ['info', '已完成'], cancelled: ['gry', '已取消'], archived: ['blk', '已归档'],
}

const bindTone: Record<string, Tone> = { requirement: 'info', issue: 'warn', change: 'pur' }

export function ProjectsPage() {
  const { openWorkItem } = useApp()
  const [projects, setProjects] = useState<Project[]>([])
  const [editor, setEditor] = useState<{ open: boolean; mode: 'create' | 'edit'; project?: Project }>({ open: false, mode: 'create' })
  const [keyword, setKeyword] = useState('')

  /* 点击项目卡片 → 打开该项目最新工作项（真实数据；无则提示） */
  const openProjectLatest = async (p: Project) => {
    try {
      const d = await api.get<{ items: { id: string }[] }>(`/api/v1/work-items?project=${encodeURIComponent(p.name)}&page_size=1`)
      if (d.items.length) openWorkItem(d.items[0].id)
      else toast('该项目暂无工作项，可先新建工作项')
    } catch {
      toast('工作项加载失败')
    }
  }

  /* 归档项目（归档后可删除） */
  const archiveProject = async (p: Project) => {
    if (!confirm(`确认归档项目「${p.name}」？归档后为只读，且只有归档后的项目才能删除。`)) return
    try {
      await api.post(`/api/v1/projects/${p.id}/archive`)
      toast.success(`已归档项目「${p.name}」（只读，可删除）`)
      load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '归档失败')
    }
  }

  /* 删除归档项目（仅归档状态可删，后端校验；有关联工作项会被拒绝） */
  const deleteArchived = async (p: Project) => {
    if (!confirm(`确认删除归档项目「${p.name}」？将清理其模板绑定与处理人配置，此操作不可恢复。`)) return
    try {
      await api.del(`/api/v1/projects/${p.id}`)
      toast.success(`已删除项目「${p.name}」`)
      load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  /* 挂载时从后端加载项目（对齐 GET /projects 返回 camelCase 字段） */
  const load = () => {
    api.get<{ items: Project[]; total: number }>('/api/v1/projects')
      .then((d) => { if (d.items.length) setProjects(d.items) })
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '项目加载失败'))
  }
  useEffect(() => { load() }, [])

  /* 搜索过滤（名称/编码）+ 最近更新排序：本地操作 */
  const visible = projects
    .filter((p) => p.status !== 'archived' && (!keyword || p.name.includes(keyword) || p.code.includes(keyword.toUpperCase())))
  const archived = projects.filter((p) => p.status === 'archived')

  const save = async (p: Project) => {
    // 前端 camelCase → 后端 snake_case（对齐 ProjectUpsertReq）
    const body = {
      name: p.name, code: p.code, status: p.status, desc: p.desc, manager: p.manager,
      template_bindings: (p.templateBindings ?? []).map((b) => ({
        template_id: b.templateId, version: b.version, status: b.status,
        assignments: (b.assignments ?? []).map((a) => ({
          node_id: a.nodeId, node_label: a.nodeLabel, users: a.users, roles: a.roles,
        })),
      })),
    }
    try {
      if (p.id && projects.some((x) => x.id === p.id)) {
        const d = await api.patch<{ item: Project }>(`/api/v1/projects/${p.id}`, body)
        setProjects((prev) => prev.map((x) => (x.id === p.id ? d.item : x)))
        toast.success('已保存项目配置：模板绑定变更已写入审计（含 before/after）')
      } else {
        const d = await api.post<{ item: Project }>('/api/v1/projects', body)
        setProjects((prev) => [d.item, ...prev])
        toast.success('已创建项目：模板绑定已写入审计')
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存项目失败')
    }
  }

  const ProjectCard = ({ p, isArchived }: { p: Project; isArchived?: boolean }) => {
    const [tone, label] = statusMap[p.status]
    const actives = p.templateBindings.filter((b) => b.status === 'active')
    const disabledBinds = p.templateBindings.filter((b) => b.status === 'disabled')
    return (
      <div className="group flex cursor-pointer flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-s transition-all hover:-translate-y-0.5 hover:border-blue-300 hover:shadow-m dark:border-slate-700 dark:bg-slate-900 dark:hover:border-blue-500/40"
        onClick={() => openProjectLatest(p)}>
        <div className="flex items-start justify-between">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-indigo-700 text-white">
            <FolderKanban className="h-5 w-5" />
          </span>
          <div className="flex items-center gap-1.5">
            <Badge tone={tone}>{label}</Badge>
            {!isArchived && (
              <>
                <button
                  className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:bg-slate-100 hover:text-amber-600 group-hover:opacity-100 dark:hover:bg-slate-800"
                  title="归档项目（归档后可删除）"
                  onClick={(e) => { e.stopPropagation(); archiveProject(p) }}>
                  <Archive className="h-3.5 w-3.5" />
                </button>
                <button
                  className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:bg-slate-100 hover:text-blue-600 group-hover:opacity-100 dark:hover:bg-slate-800"
                  title="编辑项目 / 配置模板绑定"
                  onClick={(e) => { e.stopPropagation(); setEditor({ open: true, mode: 'edit', project: p }) }}>
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              </>
            )}
          </div>
        </div>
        <div className="mt-3">
          <div className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">{p.name}</div>
          <div className="mt-0.5 font-mono text-[11px] text-slate-400">{p.code} · 更新于 {p.updated}</div>
        </div>
        <p className="mt-2 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{p.desc}</p>
        <div className="mt-4">
          <div className="mb-1.5 flex items-center justify-between text-[11.5px] text-slate-400">
            <span>进度</span><span className="font-medium text-slate-600 dark:text-slate-300">{p.progress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-600" style={{ width: `${p.progress}%` }} />
          </div>
        </div>
        {/* 流程模板绑定 */}
        <div className="mt-3.5 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-3 dark:border-slate-800">
          <Workflow className="h-3.5 w-3.5 flex-none text-slate-300 dark:text-slate-600" />
          {actives.length === 0 && disabledBinds.length === 0 ? (
            <span className="text-[11.5px] text-slate-400">未配置流程模板</span>
          ) : (
            <>
              {actives.map((b) => (
                <Badge key={b.templateId} tone={bindTone[b.type]} className="!px-2 !text-[10.5px]">
                  {b.name} {b.version}
                </Badge>
              ))}
              {disabledBinds.map((b) => (
                <Badge key={b.templateId} tone="gry" className="!px-2 !text-[10.5px] line-through">
                  {b.name} {b.version}
                </Badge>
              ))}
            </>
          )}
        </div>
        <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-[12px] text-slate-400 dark:border-slate-800">
          <div className="flex items-center gap-1">
            <Users className="h-3.5 w-3.5" />{p.members} 人
            <span className="ml-2 inline-flex items-center gap-1"><GitCommitHorizontal className="h-3.5 w-3.5" />{p.workItems} 项</span>
          </div>
          <span className="inline-flex items-center gap-1.5"><Avatar name={p.manager} grad="g6" size={20} />{p.manager}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="page-container">
      <PageHeader
        title="项目列表"
        sub="项目可绑定多条流程线模板（需求开发 / 售后问题处理 / 变更）· 归档项目只读"
        actions={
          <>
            <button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => { setProjects((prev) => [...prev].sort((a, b) => (b.updated ?? '').localeCompare(a.updated ?? ''))); toast('已按最近更新排序') }}>
              最近更新
            </button>
            <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700" onClick={() => setEditor({ open: true, mode: 'create' })}>
              <Plus className="mr-1 inline h-4 w-4" />新建项目
            </button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <SearchInput placeholder="搜索项目名称 / 编码…" width={260} onSearch={(v) => { setKeyword(v.trim()); toast(v.trim() ? `搜索项目：${v.trim()}` : '显示全部项目') }} />
        <span className="whitespace-nowrap text-[12px] text-slate-400">共 {visible.length} 个进行中项目 · {projects.length} 个可见</span>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {visible.map((p) => <ProjectCard key={p.id} p={p} />)}
      </div>

      {archived.length > 0 && (
        <div className="mt-8">
          <div className="mb-3 flex items-center gap-2 text-[13px] font-semibold text-slate-500 dark:text-slate-400">
            <Lock className="h-4 w-4" /> 归档项目（只读）
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {archived.map((p) => (
              <div key={p.id} className="group flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 opacity-80 dark:border-slate-700 dark:bg-slate-900/50"
                onClick={() => { toast('归档项目只读：允许查看与下载，不允许新建或流转任务（PRD §4.2）'); }}>
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-200 text-slate-500 dark:bg-slate-800"><Lock className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium text-slate-600 dark:text-slate-300">{p.name}</div>
                  <div className="text-[11px] text-slate-400">只读 · 2025 年归档 · {p.workItems} 项</div>
                </div>
                <Badge tone={statusMap[p.status][0]}>{statusMap[p.status][1]}</Badge>
                <button
                  className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:bg-red-50 hover:text-red-500 group-hover:opacity-100 dark:hover:bg-red-500/10"
                  title="删除归档项目"
                  onClick={(e) => { e.stopPropagation(); deleteArchived(p) }}>
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {editor.open && (
        <ProjectDialog
          mode={editor.mode}
          project={editor.project}
          onSave={save}
          onClose={() => setEditor({ open: false, mode: 'create' })}
        />
      )}
    </div>
  )
}
