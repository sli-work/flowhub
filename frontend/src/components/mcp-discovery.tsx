import { useState } from 'react'
import { api } from '../lib/api'
import type { ToolDefinition } from '../lib/tools'
import { Button } from './ui/button'
import { useApp } from '../store/app-store'
export function McpDiscovery({ serverId }: { serverId: string }) {
  const { navigate } = useApp()
  const [busy, setBusy] = useState(false)
  const [items, setItems] = useState<ToolDefinition[] | null>(null)
  const [error, setError] = useState('')
  const discover = async () => {
    setBusy(true); setError('')
    try { const data = await api.post<{ items: ToolDefinition[] }>(`/api/v1/mcp-servers/${serverId}/discover-tools`); setItems(data.items) }
    catch (error) { setError(error instanceof Error ? error.message : '连接检测失败') }
    finally { setBusy(false) }
  }
  return <section className="space-y-2 border-t pt-3"><h3 className="font-semibold">连接检测与工具发现</h3><p className="text-xs text-slate-500">使用已保存凭据连接远端 Streamable HTTP MCP，并将发现的契约导入工具中心草稿；测试发布后才可授权给 Expert。</p><Button disabled={busy} onClick={() => void discover()}>{busy ? '连接并发现中…' : '检测连接并发现 / 导入工具'}</Button>{error && <p role="alert" className="text-xs text-red-600">{error}</p>}{items && <><p className="text-xs">已发现 / 导入 {items.length} 个工具：{items.map(item => item.name).join('、')}</p><Button variant="outline" onClick={() => navigate('tool-center')}>前往工具中心测试发布</Button></>}</section>
}
