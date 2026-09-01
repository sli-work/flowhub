/* 代码仓库中心：托管平台连接管理（GitHub / GitLab / 自建）+ 项目 ↔ 仓库多对多绑定 */
import { useEffect, useMemo, useState } from 'react'
import { GitBranch, Lock, Globe, Plus, RefreshCw, Trash2, Link2, Unlink, Pencil, ShieldCheck, ShieldAlert } from 'lucide-react'
import { Badge, EmptyState, PageHeader, SearchInput, type Tone } from '../components/common'
import { cn } from '../lib/utils'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import type { Project, ProjectRepo, RemoteRepoInfo, RepoConnection, RepoItem } from '../types'

const providerLabel: Record<string, string> = { github: 'GitHub', gitlab: 'GitLab' }
const roleLabel: Record<string, string> = { main: '主仓库', docs: '文档', service: '微服务', lib: '组件库' }
const roleTone: Record<string, Tone> = { main: 'info', docs: 'pur', service: 'warn', lib: 'suc' }

const statusBadge = (s: string): [Tone, string] => (s === 'ok' ? ['suc', '已连接'] : ['warn', '已失效'])

/* ============ 连接新建 / 编辑对话框 ============ */
function ConnectionDialog({ conn, onClose, onSaved }: {
  conn?: RepoConnection
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState(conn?.name ?? '')
  const [provider, setProvider] = useState<'github' | 'gitlab'>(conn?.provider ?? 'gitlab')
  const [baseUrl, setBaseUrl] = useState(conn?.baseUrl ?? '')
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const isGitlab = provider === 'gitlab'

  const save = async () => {
    if (!name.trim()) { toast.error('请填写连接名称'); return }
    if (isGitlab && !baseUrl.trim()) { toast.error('自建 GitLab 需要填写地址（GitLab.com 可填 https://gitlab.com）'); return }
    if (!conn && token.trim().length < 8) { toast.error('请填写 Access Token（至少 8 位）'); return }
    setSaving(true)
    try {
      if (conn) {
        await api.patch(`/api/v1/repo-connections/${conn.id}`, {
          name: name.trim(), base_url: baseUrl.trim(),
          ...(token.trim() ? { token: token.trim() } : {}),
        })
        toast.success('连接已更新')
      } else {
        await api.post('/api/v1/repo-connections', {
          name: name.trim(), provider, base_url: baseUrl.trim(), token: token.trim(),
        })
        toast.success('连接已创建并验证通过')
      }
      onSaved()
      onClose()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">{conn ? '编辑连接' : '新建代码仓库连接'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">连接名称 <span className="text-red-500">*</span></label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：公司 GitLab" />
            </div>
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">平台类型</label>
              <select
                className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
                value={provider}
                disabled={!!conn}
                onChange={(e) => setProvider(e.target.value as 'github' | 'gitlab')}>
                <option value="gitlab">GitLab / 自建 GitLab</option>
                <option value="github">GitHub</option>
              </select>
            </div>
          </div>
          {isGitlab && (
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">
                GitLab 地址 <span className="text-red-500">*</span>
              </label>
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://gitlab.example.com（官方云可填 https://gitlab.com）" />
            </div>
          )}
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">
              Access Token {!conn && <span className="text-red-500">*</span>}
            </label>
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={conn ? `已配置（${conn.tokenHint}），留空则不修改` : provider === 'github' ? 'ghp_…（需 repo 读权限）' : 'glpat-…（需 read_api）'}
            />
            <p className="text-[11.5px] leading-relaxed text-slate-400">
              Token 加密存储，仅用于服务端读取仓库元信息；创建 / 更换时自动验证。
            </p>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
            <Button size="sm" disabled={saving} onClick={save}>{saving ? '验证中…' : conn ? '保存' : '创建并验证'}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 绑定仓库对话框：选连接 → 搜远端仓库 → 选项目 ============ */
function BindRepoDialog({ presetProjectId, onClose, onSaved }: { presetProjectId?: string; onClose: () => void; onSaved: () => void }) {
  const [conns, setConns] = useState<RepoConnection[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [connId, setConnId] = useState('')
  const [keyword, setKeyword] = useState('')
  const [remote, setRemote] = useState<RemoteRepoInfo[]>([])
  /* 多选：一次绑定多个仓库到同一项目（共享连接与角色，逐个提交） */
  const [picked, setPicked] = useState<RemoteRepoInfo[]>([])
  const [projectId, setProjectId] = useState(presetProjectId ?? '')
  const [role, setRole] = useState<ProjectRepo['role']>('main')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.get<{ items: RepoConnection[] }>('/api/v1/repo-connections').then((d) => {
      setConns(d.items)
      if (d.items.length) setConnId(d.items[0].id)
    }).catch(() => toast.error('连接列表加载失败'))
    api.get<{ items: Project[] }>('/api/v1/projects').then((d) => {
      const usable = d.items.filter((p) => p.status !== 'archived')
      setProjects(usable)
      /* 预选项目仍可用则保留，否则回落到第一个 */
      setProjectId((prev) => (prev && usable.some((p) => p.id === prev) ? prev : usable[0]?.id ?? ''))
    }).catch(() => {})
  }, [])

  const search = async (q = keyword) => {
    if (!connId) return
    setLoading(true)
    try {
      const d = await api.get<{ items: RemoteRepoInfo[] }>(
        `/api/v1/repo-connections/${connId}/remote-repos?q=${encodeURIComponent(q)}`,
      )
      setRemote(d.items)
      setPicked([])
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '仓库搜索失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { if (connId) search('') /* 切换连接自动拉取 */ }, [connId]) // eslint-disable-line react-hooks/exhaustive-deps

  const togglePick = (r: RemoteRepoInfo) =>
    setPicked((prev) => (prev.some((x) => x.providerRepoId === r.providerRepoId) ? prev.filter((x) => x.providerRepoId !== r.providerRepoId) : [...prev, r]))

  const save = async () => {
    if (!picked.length) { toast.error('请先勾选要绑定的仓库（支持多选）'); return }
    if (!projectId) { toast.error('请选择要绑定到的项目'); return }
    setSaving(true)
    try {
      let okCount = 0
      for (const repo of picked) {
        try {
          await api.post(`/api/v1/projects/${projectId}/repos`, {
            connection_id: connId, provider_repo_id: repo.providerRepoId, role,
          })
          okCount += 1
        } catch (e) {
          // 单个失败（多为重复绑定）不阻断其余仓库
          toast.warning(`「${repo.fullName}」绑定失败：${e instanceof Error ? e.message : '未知错误'}`)
        }
      }
      if (okCount) toast.success(`已绑定 ${okCount} 个仓库到项目`)
      if (okCount) { onSaved(); onClose() }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle className="text-[15px]">绑定代码仓库到项目</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">使用连接</label>
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={connId} onChange={(e) => setConnId(e.target.value)}>
                {conns.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}（{providerLabel[c.provider]} · {c.status === 'ok' ? '有效' : '已失效'}）</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">绑定到项目</label>
              <select className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder="搜索仓库名称…" onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
            <Button variant="outline" size="sm" disabled={loading} onClick={() => search()}>{loading ? '搜索中…' : '搜索'}</Button>
          </div>
          <div className="max-h-64 space-y-1.5 overflow-y-auto rounded-lg border border-slate-200 p-2 dark:border-slate-700">
            {remote.length === 0 && <p className="p-3 text-center text-[12px] text-slate-400">该连接下暂无可见仓库</p>}
            {remote.map((r) => {
              const on = picked.some((x) => x.providerRepoId === r.providerRepoId)
              return (
                <button
                  key={r.providerRepoId}
                  className={`flex w-full items-center gap-2 rounded-lg border p-2.5 text-left transition-colors ${
                    on
                      ? 'border-blue-300 bg-blue-50 dark:border-blue-500/40 dark:bg-blue-500/10'
                      : 'border-transparent hover:bg-slate-50 dark:hover:bg-slate-800/60'}`}
                  onClick={() => togglePick(r)}>
                  <span className={`flex h-4 w-4 flex-none items-center justify-center rounded border ${on ? 'border-blue-500 bg-blue-500' : 'border-slate-300 dark:border-slate-600'}`}>
                    {on && (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
                    )}
                  </span>
                  <GitBranch className="h-4 w-4 flex-none text-slate-400" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{r.fullName}</div>
                    <div className="truncate text-[11px] text-slate-400">{r.description || '无描述'} · 默认分支 {r.defaultBranch}</div>
                  </div>
                  <Badge tone={r.visibility === 'public' ? 'gry' : 'blk'} className="!px-1.5 !text-[10px]">{r.visibility === 'public' ? '公开' : '私有'}</Badge>
                </button>
              )
            })}
          </div>
          {picked.length > 0 && (
            <p className="text-[11.5px] text-slate-400">已选 {picked.length} 个仓库，将绑定到同一项目并使用同一角色。</p>
          )}
          <div className="space-y-1.5">
            <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">仓库角色</label>
            <div className="flex gap-1.5">
              {Object.entries(roleLabel).map(([k, label]) => (
                <button
                  key={k}
                  className={`rounded-lg border px-3 py-1.5 text-[12px] transition-colors ${
                    role === k ? 'border-blue-400 bg-blue-50 text-blue-600 dark:border-blue-500/50 dark:bg-blue-500/10 dark:text-blue-400' : 'border-slate-200 text-slate-500 hover:border-slate-300 dark:border-slate-700 dark:text-slate-400'}`}
                  onClick={() => setRole(k as typeof role)}>
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
            <Button size="sm" disabled={saving || !picked.length} onClick={save}>{saving ? `绑定中（${picked.length} 个）…` : `绑定${picked.length > 1 ? ` ${picked.length} 个仓库` : ''}`}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/* ============ 页面 ============ */
export function ReposPage() {
  const [conns, setConns] = useState<RepoConnection[]>([])
  const [repos, setRepos] = useState<RepoItem[]>([])
  const [keyword, setKeyword] = useState('')
  const [connEditor, setConnEditor] = useState<{ open: boolean; conn?: RepoConnection }>({ open: false })
  const [bindOpen, setBindOpen] = useState<{ projectId?: string } | null>(null)

  const load = () => {
    api.get<{ items: RepoConnection[] }>('/api/v1/repo-connections').then((d) => setConns(d.items)).catch(() => toast.error('连接加载失败'))
    api.get<{ items: RepoItem[] }>('/api/v1/repos').then((d) => setRepos(d.items)).catch(() => toast.error('仓库加载失败'))
  }
  useEffect(() => { load() }, [])

  const verifyConn = async (c: RepoConnection) => {
    try {
      const d = await api.post<{ item: RepoConnection }>(`/api/v1/repo-connections/${c.id}/verify`)
      setConns((prev) => prev.map((x) => (x.id === c.id ? { ...x, ...d.item } : x)))
      toast.success(d.item.status === 'ok' ? '连接有效' : '连接已失效：请更新 Token')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '检测失败')
    }
  }

  const deleteConn = async (c: RepoConnection) => {
    if (!confirm(`确认删除连接「${c.name}」？将同时删除其 ${c.repoCount} 个仓库记录与相关项目绑定。`)) return
    try {
      await api.del(`/api/v1/repo-connections/${c.id}`)
      toast.success(`已删除连接「${c.name}」`)
      load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const unbind = async (repo: RepoItem, b: RepoItem['bindings'][number]) => {
    if (!confirm(`确认将 ${repo.fullName} 从项目「${b.projectName}」解绑？（仓库记录保留，可再次绑定）`)) return
    try {
      await api.del(`/api/v1/projects/${b.projectId}/repos/${b.bindingId}`)
      toast.success(`已解绑 ${repo.fullName}`)
      load()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '解绑失败')
    }
  }

  const visible = repos.filter((r) => !keyword || r.fullName.toLowerCase().includes(keyword.toLowerCase()))

  /* 项目主视角：按项目聚合绑定关系；无绑定的仓库归入「未绑定」组 */
  const UNBOUND_KEY = '__unbound__'
  const { groups, unbound } = useMemo(() => {
    const map = new Map<string, { id: string; name: string; items: { repo: RepoItem; binding: RepoItem['bindings'][number] }[] }>()
    for (const r of visible) {
      for (const b of r.bindings) {
        let g = map.get(b.projectId)
        if (!g) { g = { id: b.projectId, name: b.projectName, items: [] }; map.set(b.projectId, g) }
        g.items.push({ repo: r, binding: b })
      }
    }
    return {
      groups: [...map.values()],
      unbound: visible.filter((r) => r.bindings.length === 0),
    }
  }, [visible])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  useEffect(() => {
    if (selectedKey === UNBOUND_KEY) { if (unbound.length === 0) setSelectedKey(null); return }
    if (groups.length === 0) { setSelectedKey(unbound.length ? UNBOUND_KEY : null); return }
    if (!selectedKey || !groups.some((g) => g.id === selectedKey)) setSelectedKey(groups[0].id)
  }, [groups, unbound, selectedKey])
  const selectedGroup = selectedKey === UNBOUND_KEY
    ? { id: UNBOUND_KEY, name: '未绑定仓库', items: unbound.map((r) => ({ repo: r, binding: null as RepoItem['bindings'][number] | null })) }
    : groups.find((g) => g.id === selectedKey) ?? null

  return (
    <div className="page-container">
      <PageHeader
        title="代码仓库"
        sub="项目可绑定多个代码仓库（GitHub / GitLab / 自建 GitLab）；连接凭证加密存储、跨项目共享"
        actions={
          <>
            <button
              className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              onClick={() => setConnEditor({ open: true })}>
              <Plus className="mr-1 inline h-4 w-4" />新建连接
            </button>
            <button
              className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
              onClick={() => {
                if (conns.length === 0) { toast.error('请先创建一个代码仓库连接'); return }
                setBindOpen({})
              }}>
              <Link2 className="mr-1 inline h-4 w-4" />绑定仓库到项目
            </button>
          </>
        }
      />

      {/* 连接列表 */}
      <div className="mb-6 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {conns.map((c) => {
          const [tone, label] = statusBadge(c.status)
          return (
            <div key={c.id} className="group flex flex-col rounded-xl border border-slate-200 bg-white p-4 shadow-s dark:border-slate-700 dark:bg-slate-900">
              <div className="flex items-start justify-between">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-slate-700 to-slate-900 text-white">
                  <GitBranch className="h-4.5 w-4.5" />
                </span>
                <div className="flex items-center gap-1.5">
                  <Badge tone={tone}>{label}</Badge>
                  <button className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:text-blue-600 group-hover:opacity-100" title="重新验证" onClick={() => verifyConn(c)}>
                    <RefreshCw className="h-3.5 w-3.5" />
                  </button>
                  <button className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:text-blue-600 group-hover:opacity-100" title="编辑 / 更换 Token" onClick={() => setConnEditor({ open: true, conn: c })}>
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button className="rounded-md p-1.5 text-slate-300 opacity-0 transition-all hover:text-red-500 group-hover:opacity-100" title="删除连接" onClick={() => deleteConn(c)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              <div className="mt-2.5 text-[14px] font-semibold text-slate-800 dark:text-slate-100">{c.name}</div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-slate-400">
                <Badge tone="info" className="!px-1.5 !text-[10px]">{providerLabel[c.provider]}</Badge>
                {c.provider === 'gitlab' && <span className="max-w-full truncate font-mono">{c.baseUrl || 'https://gitlab.com'}</span>}
                <span>· {c.account || '未验证'}</span>
                <span>· Token {c.tokenHint}</span>
                <span>· {c.repoCount} 仓库</span>
              </div>
              <div className="mt-2 flex items-center gap-1 text-[11px] text-slate-400">
                {c.status === 'ok' ? <ShieldCheck className="h-3.5 w-3.5 text-emerald-500" /> : <ShieldAlert className="h-3.5 w-3.5 text-amber-500" />}
                最近检测 {c.checkedAt || '—'}
              </div>
            </div>
          )
        })}
        {conns.length === 0 && (
          <div className="md:col-span-2 xl:col-span-3">
            <EmptyState title="还没有代码仓库连接" desc="先创建一个 GitHub / GitLab 连接（Token 加密存储），再把仓库绑定到项目" />
          </div>
        )}
      </div>

      {/* 绑定关系：项目主视角 主从布局 */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-[14px] font-semibold text-slate-700 dark:text-slate-200">项目绑定（{visible.length} 个仓库）</h3>
        <SearchInput placeholder="搜索仓库…" width={220} onSearch={(v) => setKeyword(v.trim().toLowerCase())} />
      </div>
      <div className="grid items-start gap-3 lg:grid-cols-[240px_1fr]">
        {/* 左侧：项目列表 */}
        <nav className="rounded-xl border border-slate-200 bg-white p-2 shadow-s dark:border-slate-700 dark:bg-slate-900">
          <p className="px-2 pb-1.5 pt-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">项目</p>
          {groups.map((g) => (
            <button key={g.id}
              className={cn('mb-0.5 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors',
                selectedKey === g.id ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-500/10 dark:text-blue-400' : 'text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/60')}
              onClick={() => setSelectedKey(g.id)}>
              <span className="min-w-0 flex-1 truncate">{g.name}</span>
              <Badge tone={selectedKey === g.id ? 'info' : 'gry'} className="!px-1.5 !text-[10px]">{g.items.length}</Badge>
            </button>
          ))}
          {unbound.length > 0 && (
            <button
              className={cn('flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors',
                selectedKey === UNBOUND_KEY ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-500/10 dark:text-blue-400' : 'text-slate-500 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800/60')}
              onClick={() => setSelectedKey(UNBOUND_KEY)}>
              <span className="min-w-0 flex-1 truncate italic">未绑定仓库</span>
              <Badge tone={selectedKey === UNBOUND_KEY ? 'info' : 'gry'} className="!px-1.5 !text-[10px]">{unbound.length}</Badge>
            </button>
          )}
          {groups.length === 0 && unbound.length === 0 && (
            <p className="p-2 text-[12px] leading-relaxed text-slate-400">尚无绑定，点击右上角「绑定仓库到项目」创建第一条绑定</p>
          )}
        </nav>

        {/* 右侧：选中项目的仓库卡片 */}
        <div className="min-h-[200px] space-y-2.5">
          {selectedGroup && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-s dark:border-slate-700 dark:bg-slate-900">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-semibold text-slate-800 dark:text-slate-100">{selectedGroup.name}</div>
                <div className="text-[11.5px] text-slate-400">
                  {selectedKey === UNBOUND_KEY ? '以下仓库未绑定到任何项目' : `共 ${selectedGroup.items.length} 个绑定仓库`}
                </div>
              </div>
              {selectedKey !== UNBOUND_KEY && (
                <button
                  className="rounded-lg bg-blue-600 px-3 py-1.5 text-[12px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
                  onClick={() => {
                    if (conns.length === 0) { toast.error('请先创建一个代码仓库连接'); return }
                    setBindOpen({ projectId: selectedGroup.id })
                  }}>
                  <Plus className="mr-1 inline h-3.5 w-3.5" />绑定仓库到此项目
                </button>
              )}
            </div>
          )}
          {selectedGroup?.items.map(({ repo: r, binding: b }) => (
            <div key={`${r.id}-${b?.bindingId ?? 'unbound'}`} className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
              <span className="flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400">
                {r.visibility === 'public' ? <Globe className="h-4.5 w-4.5" /> : <Lock className="h-4.5 w-4.5" />}
              </span>
              <div className="min-w-[200px] flex-1">
                <div className="flex items-center gap-2">
                  <a href={r.webUrl} target="_blank" rel="noreferrer" className="truncate font-mono text-[13.5px] font-medium text-slate-800 hover:text-blue-600 dark:text-slate-100">
                    {r.fullName}
                  </a>
                  {b && <Badge tone={roleTone[b.role]} className="!px-1.5 !text-[10px]">{roleLabel[b.role]}</Badge>}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11.5px] text-slate-400">
                  <span>{providerLabel[r.provider]} · {r.connectionName}</span>
                  <span>· 默认分支 <span className="font-mono">{r.defaultBranch}</span></span>
                  <span>· 同步于 {r.syncedAt || '—'}</span>
                  {r.bindings.length > 1 && b && (
                    <span>· 另绑定 {r.bindings.filter((x) => x.bindingId !== b.bindingId).map((x) => x.projectName).join('、')}</span>
                  )}
                </div>
              </div>
              {b ? (
                <button
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11.5px] text-slate-500 transition-colors hover:border-red-300 hover:text-red-500 dark:border-slate-700 dark:text-slate-400"
                  onClick={() => unbind(r, b)}>
                  <Unlink className="mr-1 inline h-3 w-3" />解绑
                </button>
              ) : (
                <span className="text-[11.5px] text-slate-400">未绑定项目</span>
              )}
            </div>
          ))}
          {selectedGroup === null && (
            <EmptyState title="暂无绑定" desc="点击右上角「绑定仓库到项目」，从连接可见的远端仓库中选择" />
          )}
        </div>
      </div>

      {connEditor.open && (
        <ConnectionDialog
          conn={connEditor.conn}
          onClose={() => setConnEditor({ open: false })}
          onSaved={load}
        />
      )}
      {bindOpen && <BindRepoDialog presetProjectId={bindOpen.projectId} onClose={() => setBindOpen(null)} onSaved={load} />}
    </div>
  )
}
