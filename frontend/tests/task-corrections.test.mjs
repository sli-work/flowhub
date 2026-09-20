import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')
const schemaForm = readFileSync(new URL('../src/components/schema-form.tsx', import.meta.url), 'utf8')

test('completed task detail offers an immutable correction proposal flow', () => {
  assert.match(source, /更正已提交内容/)
  assert.match(source, /\/api\/v1\/tasks\/\$\{task\.id\}\/corrections/)
  assert.match(source, /suggested_mode/)
})

test('task detail renders correction summaries returned by the API', () => {
  assert.match(source, /corrections\?: TaskCorrection\[\]/)
  assert.match(source, /更正记录/)
})

test('pending correction can be reviewed from the completed task detail', () => {
  assert.match(source, /审核更正/)
  assert.match(source, /\/api\/v1\/tasks\/corrections\/\$\{correctionReview\.id\}\/review/)
})

test('only an independently eligible reviewer sees the correction approval action', () => {
  const source = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')
  assert.match(source, /correction\.status === 'pending_review' && correction\.canReview/)
})

test('radio fields use a form-instance-specific name so correction dialogs do not collide with the task form', () => {
  assert.match(schemaForm, /useId/)
  assert.match(schemaForm, /radioName/)
})
