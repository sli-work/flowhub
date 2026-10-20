import test from 'node:test'
import assert from 'node:assert/strict'
import { availableToolVersions, automationErrors, parseToolArguments, resultDocuments } from '../src/lib/tool-contract.mjs'
const tools = [{ id: 'archive', source: 'native', slug: 'flowhub-document-archive', status: 'active', versions: [{ id: 'v1', status: 'published' }, { id: 'v2', status: 'draft' }] }]
test('grants can select only immutable published active contracts', () => {
  assert.deepEqual(availableToolVersions(tools).map(item => item.version.id), ['v1'])
  assert.deepEqual(availableToolVersions([{ ...tools[0], status: 'disabled' }]), [])
})
test('automation requires matching exact version grants and supported archive tools', () => {
  const rules = [{ id: 'a', event: 'node_entered', toolVersionId: 'v1', enabled: true }]
  assert.deepEqual(automationErrors(rules, [{ tool_version_id: 'v1' }], tools), [])
  assert.ok(automationErrors(rules, [{ tool_version_id: 'v2' }], tools).length)
  assert.ok(automationErrors([{ ...rules[0], event: 'anything' }], [{ tool_version_id: 'v1' }], tools).length)
  assert.ok(automationErrors(rules, [{ tool_version_id: 'v1' }], [{ ...tools[0], source: 'mcp' }]).length)
})
test('testing rejects non-object JSON arguments', () => {
  assert.deepEqual(parseToolArguments('{"source_document_ids":[]}'), { source_document_ids: [] })
  for (const input of ['[]', 'null', '"abc"', '{']) assert.throws(() => parseToolArguments(input))
})
test('disabled rules preserve draft settings without granting execution', () => {
  assert.deepEqual(automationErrors([{ id: 'a', event: 'bad', toolVersionId: 'missing', enabled: false }], [], []), [])
})
test('available contract selection does not mutate catalog state', () => {
  const input = structuredClone(tools)
  const before = JSON.stringify(input)
  availableToolVersions(input)
  assert.equal(JSON.stringify(input), before)
  const rules = [{ id: 'a', event: 'node_entered', toolVersionId: 'v2', enabled: true }]
  assert.ok(automationErrors(rules, [{ tool_version_id: 'v2' }], input).length)
})

test('result downloads use validated document references, never returned URLs', () => {
  assert.deepEqual(resultDocuments(null), [])
  assert.deepEqual(resultDocuments({}), [])
  assert.deepEqual(resultDocuments({ documents: [{ id: 'd1', name: '交付.md', downloadUrl: 'https://untrusted.test' }, { id: 2 }, null] }).map(({ id, name }) => ({ id, name })), [{ id: 'd1', name: '交付.md' }])
  assert.deepEqual(resultDocuments({ result: { documents: [{ id: 'd2', name: '总结.md' }] } }), [{ id: 'd2', name: '总结.md' }])
})
test('MCP grants fail closed until the published contract has task scope mapping', () => {
  const remote = { ...tools[0], source: 'mcp', versions: [{ id: 'remote1', status: 'published', taskScopeReady: false }, { id: 'remote2', status: 'published', taskScopeReady: true }, { id: 'remote3', status: 'published' }] }
  assert.deepEqual(availableToolVersions([remote]).map(item => item.version.id), ['remote2'])
})
