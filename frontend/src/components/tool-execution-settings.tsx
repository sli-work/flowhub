import { useState } from 'react'
import { api } from '../lib/api'
import { type ToolDefinition, type ToolVersion, type ToolContext } from '../lib/tools'
import { toast } from '../store/app-store'
import { Button } from './ui/button'

const contexts: { value: ToolContext; label: string }[] = [
  { value: 'work_item_id', label: '当前工作项' },
  { value: 'task_id', label: '当前任务' },
  { value: 'project', label: '当前项目' },
]
export function ToolExecutionSettings({ tool, version, onCreated }: { tool: ToolDefinition; version: ToolVersion; onCreated: (id: string) => Promise<void> }) {
  const [bindings, setBindings] = useState<Record<string, ToolContext>>(version.contextBindings ?? {})
  const [busy, setBusy] = useState(false)
  const properties = version.inputSchema.properties
  const params = properties && typeof properties === 'object' && !Array.isArray(properties) ? Object.keys(properties) : []
  const create = async () => {
    setBusy(true)
    try {
      const next = await api.post<ToolVersion>(`/api/v1/tools/${tool.id}/versions`, {
        input_schema: version.inputSchema,
        output_schema: version.outputSchema,
        risk: version.risk,
        approval: version.approval,
        config_json: { context_bindings: bindings },
      })
      await onCreated(next.id)
      toast.success('已创建映射草稿版本，需重新测试并发布后授权')
    } catch (error) { toast.error(error instanceof Error ? error.message : '创建版本失败') }
    finally { setBusy(false) }
  }
  if (tool.source === 'native') return <p className="text-xs text-slate-500">原生工具由平台校验当前任务与工作项范围。</p>
  return <section className="space-y-3 rounded border p-3 text-sm">
    <h3 className="font-semibold">执行设置 · 任务范围映射</h3>
    <p className="text-xs text-slate-500">选择远端参数对应的任务上下文。执行时由服务器绑定并校验，Expert 不能改写。映射变更将创建新草稿，现有发布版本保持不变。至少一个参数须绑定当前工作项或任务；仅绑定项目不足。首版支持顶层范围参数及正文、标题等负载字段，其他目标参数需要专用连接器。</p>
    <p className="text-xs">当前版本：{version.taskScopeReady ? '任务范围映射已就绪' : '需配置任务范围映射'}</p>
    {params.map(param => <label key={param} className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="font-mono">{param}</span><select aria-label={`${param}上下文映射`} className="rounded border bg-transparent p-2" value={bindings[param] ?? ''} disabled={busy} onChange={event => {
      const remaining = Object.fromEntries(Object.entries(bindings).filter(([key]) => key !== param))
      setBindings(event.target.value ? { ...remaining, [param]: event.target.value as ToolContext } : remaining)
    }}><option value="">不绑定上下文</option>{contexts.map(context => <option key={context.value} value={context.value}>{context.label}</option>)}</select></label>)}
    {!params.length && <p className="text-xs text-amber-700">远端 Schema 未声明顶层参数，无法配置任务范围映射；请先更新远端契约并重新发现。</p>}
    <Button disabled={busy || !Object.values(bindings).some(value => value === 'work_item_id' || value === 'task_id') || !params.length} onClick={() => void create()}>{busy ? '创建中…' : '创建映射草稿版本'}</Button>
  </section>
}
