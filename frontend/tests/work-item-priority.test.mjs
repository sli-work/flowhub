import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/components/dialogs.tsx', import.meta.url), 'utf8')

test('new work item dialog offers a priority selector and sends its value', () => {
  assert.match(source, /const \[priority, setPriority\] = useState<'P0' \| 'P1' \| 'P2' \| 'P3'>\('P2'\)/)
  assert.match(source, /value=\{priority\}[\s\S]{0,300}setPriority/)
  assert.match(source, /project_id: projId, template_id: templateId, start_values: values, priority, labels: selLabels/)
  for (const value of ['P0', 'P1', 'P2', 'P3']) assert.match(source, new RegExp(`value="${value}"`))
})

test('work item detail provides an editable priority control for existing work items', () => {
  const detailSource = readFileSync(new URL('../src/pages/workitem.tsx', import.meta.url), 'utf8')
  assert.match(detailSource, /api\.patch\(`\/api\/v1\/work-items\/\$\{wi\.id\}\/priority`, \{ priority \}\)/)
  assert.match(detailSource, /aria-label="修改优先级"/)
})

test('work item list distinguishes the current primary assignee from all node assignees', () => {
  const listSource = readFileSync(new URL('../src/pages/workitems.tsx', import.meta.url), 'utf8')
  assert.match(listSource, /当前处理人/)
  assert.match(listSource, /w\.assignees\.length > 1/)
  assert.match(listSource, /title=\{w\.assignees\.join\('、'\)\}/)
})
