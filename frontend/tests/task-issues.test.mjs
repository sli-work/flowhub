import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const nodePage = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')
const workItemPage = readFileSync(new URL('../src/pages/workitem.tsx', import.meta.url), 'utf8')

test('task processing exposes generic issue creation and verification actions', () => {
  assert.match(nodePage, /\/api\/v1\/tasks\/\$\{activeTaskId\}\/issues/)
  assert.match(nodePage, /\/api\/v1\/tasks\/issues\/\$\{issue\.id\}\/verify/)
  assert.match(nodePage, /发起问题/)
  assert.match(nodePage, /阻断当前节点继续提交/)
})

test('work item detail renders a separate issue closure summary', () => {
  assert.match(workItemPage, /\/api\/v1\/work-items\/\$\{activeWiId\}\/issues/)
  assert.match(workItemPage, /问题闭环/)
})
