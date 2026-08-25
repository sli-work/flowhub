import { useEffect, useState } from 'react'
import { KeyRound, Copy, Download, Plus, Trash2, Check, ShieldCheck, Server } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Badge } from '../components/common'
import { cn } from '../lib/utils'
import type { AccessKeyItem } from '../types'

/** Agent 管理第三 Tab：外部接入（access key + MCP 配置 + Skill 下载） */
export function ExternalAccess() {
  const { bumpTask } = useApp()
  const [keys, setKeys] = useState<AccessKeyItem[]>([])
  const [newName, setNewName] = useState('')
  const [justCreated, setJustCreated] = useState<{ id: string; name: string; key: string } | null>(null)
  const [selKey, setSelKey] = useState('')
  const [fmt, setFmt] = useState<'opencode' | 'generic'>('opencode')
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    refresh()
  }, [])

  const refresh = async () => {
    try {
      const d = await api.get<{ items: AccessKeyItem[] }>('/api/v1/agents/access-keys')
      setKeys(d.items)
      setSelKey((prev) => prev || d.items.find((k) => k.status === 'active')?.id || '')
    } catch { /* 后端不可用 */ }
  }

  const createKey = async () => {
    setBusy(true)
    try {
      const d = await api.post<{ key: { id: string; name: string; key: string } }>('/api/v1/agents/access-keys', { name: newName.trim() || '默认' })
      setJustCreated({ id: d.key.id, name: d.key.name, key: d.key.key })
      setNewName('')
      setSelKey(d.key.id)
      bumpTask()
      toast.success('access key 已创建（明文仅此一次展示）')
      await refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建失败')
    } finally {
      setBusy(false)
    }
  }

  const revokeKey = async (id: string) => {
    if (!confirm('确认吊销该 access key？吊销后立即失效，外部 Agent 将无法再访问平台。')) return
    try {
      await api.del(`/api/v1/agents/access-keys/${id}`)
      toast.success('access key 已吊销')
      bumpTask()
      await refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '吊销失败')
    }
  }

  const copyText = async (text: string, tip: string) => {
    try {
      await navigator.clipboard.writeText(text)
      toast.success(tip)
    } catch {
      toast('复制失败，请手动复制')
    }
  }

  const selected = keys.find((k) => k.id === selKey)
  /* 完整 key：仅当选中"刚生成"的 key 时可用（平台不回显完整 key） */
  const fullKey = selected && justCreated && justCreated.id === selected.id ? justCreated.key : ''
  const origin = window.location.origin

  const mcpOpencode = selected ? JSON.stringify({
    $schema: 'https://opencode.ai/config.json',
    mcp: { servers: { flowhub: {
      type: 'remote', url: `${origin}/api/v1/mcp/sse`,
      headers: { Authorization: `Bearer ${fullKey || selected.prefix}` }, oauth: false,
    } } },
  }, null, 2) : ''

  const mcpGeneric = selected ? JSON.stringify({
    mcpServers: { flowhub: {
      type: 'sse', url: `${origin}/api/v1/mcp/sse`,
      headers: { Authorization: `Bearer ${fullKey || selected.prefix}` },
    } },
  }, null, 2) : ''

  /* 注意：.mcp.json 的 Authorization 需用完整 access key 而非前缀；此处以 selected 展示，
     用户需将配置中的 Bearer 值替换为完整 key（前端不回显完整 key）。 */
  const currentJson = fmt === 'opencode' ? mcpOpencode : mcpGeneric

  const downloadSkill = () => {
    if (!selected) { toast('请先选择一把 active 的 access key'); return }
    const md = `# FlowHub 外部接入 Skill

将本 Skill 配置到你的 Agent（opencode / Claude Code 等），即可通过 MCP 连接 FlowHub 流程协同平台，
访问**流转到你名下**的任务数据（含前序节点上下文），并基于完整上下文辅助你完成任务。

## 前置条件

1. 在 FlowHub「Agent 管理 → 外部接入」生成一把 access key（**明文仅展示一次**，服务端只存哈希）。
2. 将以下 MCP 配置写入 opencode 配置（.opencode/opencode.json 或 .mcp.json）：

\`\`\`json
${mcpOpencode}
\`\`\`

> 若配置中的 Bearer 值仅为 key 前缀（sk_xxxx…），请替换为你的完整 access key（sk_ 开头，仅生成时展示一次）。

## 可用工具（6 个）

| 工具 | 用途 |
|---|---|
| \`list_my_tasks\` | 列出我当前的任务（可选 status 过滤） |
| \`get_task\` | 查看任务详情 + 当前节点表单 |
| \`get_task_context\` | **完整上下文**：工作项 + 当前节点 + 前序节点表单明细 + 关联文档 |
| \`get_work_item\` | 工作项信息 + 全部任务概览 |
| \`list_documents\` | 工作项关联文档元数据 |
| \`get_downstream_summary\` | 后续节点任务只读摘要（不含表单明细） |

## 权限与数据边界（重要）

- 仅可访问 **你负责的任务**（assignee）或系统/组织管理员的全部任务；
- 已执行链（当前 + 前序节点）可获取**完整表单数据**；
- **后续节点**只提供只读摘要（节点/状态/处理人/截止），不含表单明细；
- 无权限的数据访问会返回明确错误，不会静默返回部分数据。

## 示例 Prompt

1. "列出我当前所有待处理的任务"
2. "给我任务 T-20260822xxxx-xxxx 的完整上下文"
3. "这个任务后续还有哪些节点？分别是谁负责？"
4. "帮我总结 REQ-2026-xxxx 这个需求当前进展"
`
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'flowhub-skill.md'
    a.click()
    URL.revokeObjectURL(a.href)
    toast.success('Skill 已下载（flowhub-skill.md）')
  }

  return (
    <div className="space-y-5">
      {/* 说明条 */}
      <div className="flex items-start gap-2.5 rounded-lg border border-blue-200 bg-blue-50 p-3 text-[12px] leading-relaxed text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-300">
        <ShieldCheck className="mt-0.5 h-4 w-4 flex-none" />
        <span>外部 Agent 通过 <b>MCP + Skill</b> 连接平台，用你的 <b>access key</b> 认证（区分权限与隔离，仅可访问你负责的任务数据）。MCP 端点：<code className="rounded bg-white/60 px-1">{origin}/api/v1/mcp/sse</code></span>
      </div>

      {/* Access Key 管理 */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-slate-800 dark:text-slate-100">
          <KeyRound className="h-4 w-4 text-blue-500" />Access Key 管理
          <span className="text-[11.5px] font-normal text-slate-400">（多 Key 可吊销 · 明文仅展示一次 · 记录最近使用）</span>
        </div>

        {justCreated && (
          <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-500/30 dark:bg-emerald-500/10">
            <div className="text-[12.5px] font-medium text-emerald-700 dark:text-emerald-400">access key「{justCreated.name}」已创建 — 明文仅此一次展示，请立即保存</div>
            <div className="mt-1.5 flex items-center gap-2">
              <code className="flex-1 overflow-x-auto rounded bg-white px-2 py-1.5 font-mono text-[12.5px] text-slate-800 dark:bg-slate-900 dark:text-slate-200">{justCreated.key}</code>
              <button className="rounded-lg border border-emerald-300 px-2.5 py-1.5 text-[12px] font-medium text-emerald-700 hover:bg-emerald-100 dark:border-emerald-500/40 dark:text-emerald-400" onClick={() => copyText(justCreated.key, 'access key 已复制')}>
                <Copy className="mr-1 inline h-3.5 w-3.5" />复制
              </button>
              <button className="rounded-lg border border-slate-300 px-2.5 py-1.5 text-[12px] text-slate-500 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400" onClick={() => setJustCreated(null)}>关闭</button>
            </div>
            <p className="mt-1.5 text-[11px] text-emerald-600/70 dark:text-emerald-400/70">服务端仅保存 bcrypt 哈希；关闭后不可再次查看，丢失需重新生成。</p>
          </div>
        )}

        <div className="mb-3 flex gap-2">
          <input className="h-9 flex-1 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            placeholder="Key 名称（如 opencode 接入 / 测试环境）" value={newName} onChange={(e) => setNewName(e.target.value)} />
          <button className="flex items-center gap-1 rounded-lg bg-blue-600 px-3.5 text-[13px] font-medium text-white hover:bg-blue-700 disabled:opacity-50" disabled={busy} onClick={createKey}>
            <Plus className="h-4 w-4" />生成 Key
          </button>
        </div>

        {keys.length === 0 ? (
          <p className="py-3 text-center text-[12.5px] text-slate-400">暂无 access key，点击「生成 Key」创建</p>
        ) : (
          <div className="space-y-2">
            {keys.map((k) => (
              <div key={k.id} className="flex items-center gap-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-[12.5px] dark:border-slate-800 dark:bg-slate-800/40">
                <span className="flex-1 min-w-0">
                  <span className="font-medium text-slate-700 dark:text-slate-200">{k.name}</span>
                  <span className="ml-2 font-mono text-[11px] text-slate-400">{k.prefix}…</span>
                  <span className="ml-2 text-[11px] text-slate-400">创建 {k.createdAt}{k.lastUsed ? ` · 最近使用 ${k.lastUsed}` : ''}</span>
                </span>
                <Badge tone={k.status === 'active' ? 'suc' : 'err'}>{k.status === 'active' ? '生效中' : '已吊销'}</Badge>
                {k.status === 'active' && (
                  <button className="rounded-lg border border-red-200 px-2 py-1 text-[11.5px] font-medium text-red-500 hover:bg-red-50 dark:border-red-500/30 dark:hover:bg-red-500/10" onClick={() => revokeKey(k.id)}>
                    <Trash2 className="mr-1 inline h-3 w-3" />吊销
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* MCP 配置复制 */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-slate-800 dark:text-slate-100">
          <Server className="h-4 w-4 text-violet-500" />MCP 配置复制
        </div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-[12.5px]">
          <span className="text-slate-500 dark:text-slate-400">选择 access key：</span>
          <select className="h-8 rounded-lg border border-slate-300 bg-white px-2 text-[12.5px] outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            value={selKey} onChange={(e) => { setSelKey(e.target.value); setCopied(false) }}>
            {keys.filter((k) => k.status === 'active').map((k) => <option key={k.id} value={k.id}>{k.name}（{k.prefix}…）</option>)}
          </select>
          <span className="text-slate-500 dark:text-slate-400">格式：</span>
          <div className="flex overflow-hidden rounded-lg border border-slate-300 dark:border-slate-700">
            <button className={cn('px-3 py-1.5 text-[12px] font-medium', fmt === 'opencode' ? 'bg-violet-600 text-white' : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400')} onClick={() => setFmt('opencode')}>opencode</button>
            <button className={cn('px-3 py-1.5 text-[12px] font-medium', fmt === 'generic' ? 'bg-violet-600 text-white' : 'text-slate-500 hover:bg-slate-100 dark:text-slate-400')} onClick={() => setFmt('generic')}>通用 MCP</button>
          </div>
        </div>
        {selected ? (
          <>
            <pre className="max-h-56 overflow-auto rounded-lg bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed text-emerald-300 dark:bg-slate-950">{currentJson}</pre>
            <div className="mt-2 flex items-center gap-3">
              <button className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-blue-700"
                onClick={() => { copyText(currentJson, 'MCP 配置已复制'); setCopied(true) }}>
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}复制配置
              </button>
              <span className="text-[11.5px] text-amber-600 dark:text-amber-400">
                {fullKey ? '✓ 已自动填入刚生成的完整 access key，复制后可直接使用' : `⚠ 需将配置中的 ${selected.prefix} 替换为完整 access key（完整 key 仅在你生成时展示一次，平台不回显）`}
              </span>
            </div>
          </>
        ) : (
          <p className="py-2 text-[12.5px] text-slate-400">请先生成一把 access key</p>
        )}
      </section>

      {/* Skill 下载 */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-2 flex items-center gap-2 text-[14px] font-semibold text-slate-800 dark:text-slate-100">
          <Download className="h-4 w-4 text-emerald-600" />Skill 下载
        </div>
        <p className="mb-3 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">
          下载外部接入 Skill（markdown）：包含平台简介、MCP 工具用法、权限与数据边界说明、示例 Prompt。
          将文件放置到你的 Agent 的 skills 目录（如 opencode 的 <code className="rounded bg-slate-100 px-1">.opencode/skills/flowhub/SKILL.md</code>）即可使用。
        </p>
        <button className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50" disabled={!selected} onClick={downloadSkill}>
          <Download className="h-4 w-4" />下载 flowhub-skill.md
        </button>
      </section>
    </div>
  )
}
