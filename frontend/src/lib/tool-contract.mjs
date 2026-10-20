export function availableToolVersions(tools) {
  return tools.filter(tool => tool.status === 'active').flatMap(tool => tool.versions.filter(version => version.status === 'published' && (tool.source === 'native' || version.taskScopeReady === true)).map(version => ({ tool, version })))
}
export function automationErrors(rules, bindings, tools) {
  return rules.filter(rule => rule.enabled).flatMap(rule => {
    const entry = availableToolVersions(tools).find(item => item.version.id === rule.toolVersionId)
    if (!['node_entered', 'expert_output_validated'].includes(rule.event)) return ['不支持的自动化事件']
    if (!entry || entry.tool.source !== 'native' || !entry.tool.slug.includes('archive')) return ['自动化仅支持已发布的原生归档工具']
    if (!bindings.some(binding => binding.tool_version_id === rule.toolVersionId)) return ['自动化工具版本必须与 Deployment 固定版本的授权一致']
    return []
  })
}
export function parseToolArguments(text) {
  const value = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('测试参数必须是 JSON 对象')
  return value
}
export function resultDocuments(result) {
  if (!result || typeof result !== 'object') return []
  const value = Array.isArray(result.documents) ? result.documents : []
  const nested = result.result && result.result !== result ? resultDocuments(result.result) : []
  return [...value.filter(doc => doc && typeof doc === 'object' && typeof doc.id === 'string' && typeof doc.name === 'string'), ...nested]
}
