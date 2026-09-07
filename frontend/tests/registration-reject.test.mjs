import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/org.tsx', import.meta.url), 'utf8')

test('registration rejection collects a reason and calls the rejection endpoint', () => {
  assert.match(source, /function RejectRegistrationDialog\(/)
  assert.match(source, /\/api\/v1\/auth\/approvals\/\$\{user\.id\}\/reject/)
  assert.match(source, /拒绝原因/)
})
