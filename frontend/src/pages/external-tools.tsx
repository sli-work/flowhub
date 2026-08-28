import { useEffect, useState } from 'react'
import { Check, Clipboard, Copy, Download, ExternalLink, KeyRound, LockKeyhole, Server, ShieldCheck, Trash2 } from 'lucide-react'
import { PageHeader, SectionCard } from '../components/common'
import { toast } from '../store/app-store'
import { api, getToken } from '../lib/api'
import type { AccessKeyItem } from '../types'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'

export function ExternalToolsPage() {
  const [downloaded, setDownloaded] = useState<string[]>([])
  const [keys, setKeys] = useState<AccessKeyItem[]>([])
  const [keyName, setKeyName] = useState('')
  const [selectedKeyId, setSelectedKeyId] = useState('')
  const [plainKey, setPlainKey] = useState('')
  const [docsOpen, setDocsOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const loadKeys = async () => { try { const data = await api.get<{ items: AccessKeyItem[] }>('/api/v1/access-keys'); setKeys(data.items) } catch { setKeys([]) } }
  useEffect(() => { void loadKeys() }, [])
  const download = async (name: string, path: string, filename: string) => {
    try {
      const token = getToken()
      const response = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      if (!response.ok) throw new Error(`下载失败（HTTP ${response.status}）`)
      const url = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
      setDownloaded((current) => current.includes(name) ? current : [...current, name])
      toast.success(`${name} 已下载`)
    } catch (error) { toast.error(error instanceof Error ? error.message : `${name} 下载失败`) }
  }
  const createKey = async () => {
    setBusy(true)
    try {
      const data = await api.post<{ key: { id: string; name: string; key: string } }>('/api/v1/access-keys', { name: keyName.trim() || '默认' })
      setPlainKey(data.key.key)
      setKeyName('')
      await loadKeys()
      toast.success('Access Key 已创建，请立即保存')
    } catch (error) { toast.error(error instanceof Error ? error.message : '创建 Access Key 失败') }
    finally { setBusy(false) }
  }
  const revokeKey = async (key: AccessKeyItem) => {
    if (!window.confirm(`吊销 Access Key「${key.name}」？外部 Agent 将立即失效。`)) return
    try { await api.del(`/api/v1/access-keys/${key.id}`); await loadKeys(); toast.success('Access Key 已吊销') }
    catch (error) { toast.error(error instanceof Error ? error.message : '吊销失败') }
  }
  const copyKey = async () => { try { await navigator.clipboard.writeText(plainKey); toast.success('Access Key 已复制') } catch { toast.error('复制失败，请手动复制') } }
  const revealKey = async (key: AccessKeyItem) => { try { const data = await api.get<{ id: string; name: string; key: string }>(`/api/v1/access-keys/${key.id}/value`); setPlainKey(data.key); toast.success('Access Key 已验证') } catch (error) { toast.error(error instanceof Error ? error.message : '查看 Access Key 失败') } }
  return <div className="page-container">
    <PageHeader title="MCP / Skill 下载" sub="面向外部智能体的 FlowHub 操作能力，不进入内部 Expert Skill Registry" actions={<button className="rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-[13px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={() => setDocsOpen(true)}><ExternalLink className="mr-1 inline h-3.5 w-3.5" />查看接入文档</button>} />
    <div className="mb-5 grid gap-4 lg:grid-cols-3">
      <div className="rounded-xl border border-blue-200 bg-blue-50 p-5 dark:border-blue-500/30 dark:bg-blue-500/10"><Server className="h-5 w-5 text-blue-600" /><h2 className="mt-3 text-[15px] font-semibold text-slate-900 dark:text-slate-100">FlowHub MCP Server</h2><p className="mt-1.5 text-[12px] leading-relaxed text-slate-600 dark:text-slate-300">下载 MCP JSON，可选择将指定 Access Key 直接写入配置。</p><select aria-label="选择 MCP 配置 Access Key" value={selectedKeyId} onChange={(event) => setSelectedKeyId(event.target.value)} className="mt-3 h-9 w-full rounded-lg border border-blue-200 bg-white px-2 text-xs dark:border-slate-700 dark:bg-slate-900"><option value="">使用环境变量占位符</option>{keys.filter((key) => key.status === 'active').map((key) => <option key={key.id} value={key.id}>{key.name} · {key.prefix}...</option>)}</select><button className="mt-3 whitespace-nowrap rounded-lg bg-blue-600 px-3 py-2 text-[12px] font-medium text-white hover:bg-blue-700" onClick={() => void download('FlowHub MCP 配置', `/api/v1/external-tools/mcp-config${selectedKeyId ? `?key_id=${encodeURIComponent(selectedKeyId)}` : ''}`, 'flowhub-mcp.json')}>{downloaded.includes('FlowHub MCP 配置') ? <><Check className="mr-1 inline h-3.5 w-3.5" />已下载</> : <><Download className="mr-1 inline h-3.5 w-3.5" />下载配置</>}</button></div>
      <DownloadCard icon={<Clipboard className="h-5 w-5 text-violet-600" />} tone="violet" title="操作 Skill Markdown" description="下载外部 Agent 的 FlowHub 操作说明，包含权限边界和可用的只读 MCP Tool。" label={downloaded.includes('FlowHub 操作 Skill') ? '已下载' : '下载 Markdown'} done={downloaded.includes('FlowHub 操作 Skill')} onClick={() => void download('FlowHub 操作 Skill', '/api/v1/external-tools/skill-markdown', 'flowhub-mcp-skill.md')} />
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 dark:border-emerald-500/30 dark:bg-emerald-500/10"><KeyRound className="h-5 w-5 text-emerald-600" /><h2 className="mt-3 text-[15px] font-semibold text-slate-900 dark:text-slate-100">Access Key</h2><p className="mt-1.5 text-[12px] leading-relaxed text-slate-600 dark:text-slate-300">每个外部 Agent 使用独立 key；有效密钥可随时查看、复制或用于下载 MCP 配置。</p><div className="mt-4 flex w-full min-w-0 items-center gap-2"><Input aria-label="Access Key 名称" value={keyName} onChange={(event) => setKeyName(event.target.value)} placeholder="例如 Claude Desktop" className="min-w-0 flex-1" /><button className="shrink-0 whitespace-nowrap rounded-lg bg-emerald-600 px-4 py-2 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-60" disabled={busy} onClick={() => void createKey()}>{busy ? '创建中…' : '创建 Key'}</button></div></div>
    </div>
    {plainKey && <div className="mb-5 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"><b>Access Key</b><div className="mt-2 flex items-center gap-2 rounded-lg bg-white p-2 font-mono text-xs text-slate-800 dark:bg-slate-950 dark:text-slate-100"><code className="min-w-0 flex-1 break-all">{plainKey}</code><button aria-label="复制 Access Key" className="rounded p-1 text-amber-700 hover:bg-amber-100" onClick={() => void copyKey()}><Copy className="h-4 w-4" /></button></div></div>}
    <SectionCard title="已创建 Access Key"><div className="space-y-2">{keys.length ? keys.map((key) => <div key={key.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-xs dark:border-slate-700"><KeyRound className="h-4 w-4 text-emerald-600" /><span className="min-w-0 flex-1"><b className="block">{key.name}</b><span className="font-mono text-slate-400">{key.prefix}...</span></span><span className={key.status === 'active' ? 'text-emerald-600' : 'text-slate-400'}>{key.status === 'active' ? '有效' : '已吊销'}</span>{key.status === 'active' && <><button className="shrink-0 whitespace-nowrap rounded border border-slate-200 px-2 py-1 text-[11px] text-blue-600 hover:bg-blue-50" onClick={() => void revealKey(key)}>查看 Key</button><button aria-label={`吊销 ${key.name}`} className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600" onClick={() => void revokeKey(key)}><Trash2 className="h-4 w-4" /></button></>}</div>) : <p className="py-4 text-center text-xs text-slate-400">尚未创建 Access Key</p>}</div></SectionCard>
    <SectionCard title="使用边界"><div className="grid gap-3 md:grid-cols-2"><div className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300"><ShieldCheck className="h-4 w-4 flex-none text-emerald-600" />下载内容仅描述对外 MCP 操作能力；不会导出内部 Expert Skill、Provider 凭据或组织数据。</div><div className="flex items-start gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300"><LockKeyhole className="h-4 w-4 flex-none text-amber-600" />Access key 应作为环境变量注入外部 Agent，不会写入下载的 JSON 或 Markdown 文件。</div></div></SectionCard>
    <Dialog open={docsOpen} onOpenChange={setDocsOpen}><DialogContent className="sm:max-w-[680px]"><DialogHeader><DialogTitle>FlowHub MCP 接入文档</DialogTitle></DialogHeader><div className="space-y-4 text-sm leading-6 text-slate-600 dark:text-slate-300"><p>1. 创建一个专用于外部 Agent 的 Access Key，并立即保存明文。</p><p>2. 下载 MCP JSON，将 <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">${'{FLOWHUB_ACCESS_KEY}'}</code> 配置为该 key 的环境变量。</p><p>3. 将 JSON 导入支持 SSE MCP 的 Agent 客户端。服务端会使用调用者的 FlowHub 权限过滤任务、工作项和文档。</p><p>4. 可选下载操作 Skill Markdown，作为外部 Agent 的操作约束。</p><div className="rounded-lg bg-slate-50 p-3 font-mono text-xs dark:bg-slate-800">MCP endpoint: /api/v1/mcp/sse</div></div><DialogFooter><Button onClick={() => setDocsOpen(false)}>关闭</Button></DialogFooter></DialogContent></Dialog>
  </div>
}

function DownloadCard({ icon, tone, title, description, label, done, onClick }: { icon: React.ReactNode; tone: 'blue' | 'violet'; title: string; description: string; label: string; done: boolean; onClick: () => void }) {
  const button = tone === 'blue' ? 'bg-blue-600 hover:bg-blue-700' : 'bg-violet-600 hover:bg-violet-700'
  const border = tone === 'blue' ? 'border-blue-200 bg-blue-50 dark:border-blue-500/30 dark:bg-blue-500/10' : 'border-violet-200 bg-violet-50 dark:border-violet-500/30 dark:bg-violet-500/10'
  return <div className={`rounded-xl border p-5 ${border}`}>{icon}<h2 className="mt-3 text-[15px] font-semibold text-slate-900 dark:text-slate-100">{title}</h2><p className="mt-1.5 text-[12px] leading-relaxed text-slate-600 dark:text-slate-300">{description}</p><button className={`mt-4 rounded-lg px-3 py-2 text-[12px] font-medium text-white ${button}`} onClick={onClick}>{done ? <><Check className="mr-1 inline h-3.5 w-3.5" />{label}</> : <><Download className="mr-1 inline h-3.5 w-3.5" />{label}</>}</button></div>
}
