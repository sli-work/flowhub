import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('my tasks has a dedicated pending-correction review entry', () => {
  const source = readFileSync(new URL('../src/pages/tasks.tsx', import.meta.url), 'utf8')
  assert.match(source, /key: 'correction_pending'/)
  assert.match(source, /更正待审批/)
  assert.match(source, /t\.pendingCorrection \? '去审核'/)
  assert.match(source, /t\.pendingCorrection\?\.id/)
})

test('task detail opens the requested correction directly after loading', () => {
  const source = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')
  assert.match(source, /activeCorrectionId/)
  assert.match(source, /setCorrectionReview\(target\)/)
  assert.match(source, /该更正已处理或你已无审核权限/)
})
