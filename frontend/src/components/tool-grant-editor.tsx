import { useEffect, useState } from 'react'
import { fetchTools, type ToolBinding, type ToolDefinition } from '../lib/tools'
import { availableToolVersions } from '../lib/tool-contract.mjs'

export function ToolGrantEditor({ bindings, disabled, onChange }: { bindings: ToolBinding[]; disabled: boolean; onChange: (bindings: ToolBinding[]) => void }) {
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [error, setError] = useState('')
  useEffect(() => { let active = true; fetchTools().then(items => { if (active) setTools(items) }).catch(error => { if (active) setError(String(error)) }); return () => { active = false } }, [])
  const choices = availableToolVersions(tools)
  const unscoped = tools.filter(tool => tool.source === 'mcp' && tool.status === 'active').flatMap(tool => tool.versions.filter(version => version.status === 'published' && version.taskScopeReady !== true).map(version => ({ tool, version })))
  const missing = bindings.filter(binding => !choices.some(item => item.version.id === binding.tool_version_id))
  return <section className="mb-5 space-y-2"><h3 className="text-sm font-semibold">工具授权（当前草稿）</h3><p className="text-xs text-slate-500">仅绑定已发布的固定工具版本；保存后生成草稿，生产 Deployment 保持原发布版本。作用范围固定为当前工作项，审批只能收紧。</p>{error && <p role="alert" className="text-xs text-red-600">工具目录加载失败：{error}</p>}{choices.map(({ tool, version }) => {
    const binding = bindings.find(item => item.tool_version_id === version.id)
    return <div key={version.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-xs"><label className="flex flex-1 items-center gap-2"><input type="checkbox" disabled={disabled} checked={!!binding} onChange={event => onChange(event.target.checked ? [...bindings, { tool_version_id: version.id, scope: 'current_work_item', approval: 'inherit' }] : bindings.filter(item => item.tool_version_id !== version.id))} />{tool.name} · v{version.revision} · {version.risk}</label><span>当前工作项</span>{binding && <select aria-label={`${tool.name}审批策略`} disabled={disabled} value={binding.approval} onChange={event => onChange(bindings.map(item => item === binding ? { ...item, approval: event.target.value as ToolBinding['approval'] } : item))}><option value="inherit">继承工具策略（{version.approval}）</option><option value="required">每次需要审批</option></select>}</div>
  })}{unscoped.map(({ tool, version }) => <label key={version.id} className="flex items-center gap-2 rounded-lg border p-3 text-xs text-slate-500"><input type="checkbox" disabled checked={false} />{tool.name} · v{version.revision} · 需配置任务范围映射</label>)}{!choices.length && !error && <p className="text-xs text-slate-500">暂无已发布工具，请先在工具中心测试并发布。</p>}{missing.map(binding => <div key={binding.tool_version_id} className="flex items-center justify-between border border-amber-300 p-2 text-xs"><span>授权版本不可用：{binding.tool_version_id}</span><button disabled={disabled} onClick={() => onChange(bindings.filter(item => item !== binding))}>解除绑定</button></div>)}</section>
}
