import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { fetchTools, type AutomationRule, type ToolBinding, type ToolDefinition } from '../lib/tools'
import { availableToolVersions, automationErrors } from '../lib/tool-contract.mjs'

export function NodeAutomationEditor({ deploymentId, rules, disabled, onChange }: { deploymentId?: string; rules: AutomationRule[]; disabled: boolean; onChange: (rules: AutomationRule[]) => void }) {
  const [context, setContext] = useState<{ tools: ToolDefinition[]; bindings: ToolBinding[]; deploymentId: string }>({ tools: [], bindings: [], deploymentId: '' })
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    async function load() {
      if (!deploymentId) return
      try {
        const [tools, deployments] = await Promise.all([fetchTools(), api.get<{ items: { id: string; expertId: string; versionId: string }[] }>('/api/v1/expert-deployments')])
        const deployment = deployments.items.find(item => item.id === deploymentId)
        if (!deployment) throw new Error('Deployment 不存在')
        const detail = await api.get<{ versions: { id: string; toolBindings?: ToolBinding[] }[] }>(`/api/v1/experts/${deployment.expertId}`)
        const version = detail.versions.find(item => item.id === deployment.versionId)
        if (!version) throw new Error('无法读取 Deployment 固定版本')
        if (active) { setContext({ tools, bindings: version.toolBindings ?? [], deploymentId: deploymentId! }); setError('') }
      } catch (error) { if (active) setError(error instanceof Error ? error.message : '读取自动化授权失败') }
    }
    void load(); return () => { active = false }
  }, [deploymentId])
  const ready = context.deploymentId === deploymentId && !error
  const choices = availableToolVersions(context.tools).filter(({ tool, version }) => tool.source === 'native' && tool.slug.includes('archive') && context.bindings.some(binding => binding.tool_version_id === version.id))
  const issues = ready ? automationErrors(rules, context.bindings, context.tools) : rules.some(rule => rule.enabled) ? ['请选择含归档工具授权的已发布 Deployment'] : []
  return <section className="space-y-2 border-t pt-3 text-xs"><h3 className="font-semibold">节点自动化</h3><p className="text-slate-500">首版仅支持原生归档：节点进入时保存已完成上游交付物，产出格式校验后保存 AI 总结。人工验收仍需由处理人完成。</p>{error && <p role="alert" className="text-red-600">{error}</p>}{rules.map(rule => <div key={rule.id} className="space-y-2 rounded border p-2"><label className="flex gap-2"><input type="checkbox" checked={rule.enabled} disabled={disabled} onChange={event => onChange(rules.map(item => item.id === rule.id ? { ...item, enabled: event.target.checked } : item))} />启用规则</label><select aria-label="自动化事件" disabled={disabled} value={rule.event} onChange={event => onChange(rules.map(item => item.id === rule.id ? { ...item, event: event.target.value as AutomationRule['event'] } : item))}><option value="node_entered">节点进入</option><option value="expert_output_validated">Expert 产出格式校验后</option></select><select aria-label="自动化工具固定版本" disabled={disabled || !ready} value={rule.toolVersionId} onChange={event => onChange(rules.map(item => item.id === rule.id ? { ...item, toolVersionId: event.target.value } : item))}><option value="">选择已授权归档版本</option>{!choices.some(item => item.version.id === rule.toolVersionId) && rule.toolVersionId && <option value={rule.toolVersionId}>不可用：{rule.toolVersionId}</option>}{choices.map(({ tool, version }) => <option key={version.id} value={version.id}>{tool.name} v{version.revision}</option>)}</select><button disabled={disabled} onClick={() => onChange(rules.filter(item => item.id !== rule.id))}>删除规则</button></div>)}{issues.map((issue, index) => <p role="alert" key={index} className="text-amber-700">{issue}</p>)}<button className="rounded border px-3 py-2 disabled:opacity-50" disabled={disabled || !ready || !choices.length} onClick={() => onChange([...rules, { id: crypto.randomUUID(), event: 'node_entered', toolVersionId: choices[0].version.id, enabled: true }])}>添加归档规则</button><p className="text-slate-500">规则随画布「保存草稿 / 保存并发布」写入服务器；只有发布的模板版本生效。服务端重新校验固定授权。</p></section>
}
