import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/components/dialogs.tsx', import.meta.url), 'utf8')

test('forced password change dialog is registered for accounts whose password was reset', () => {
  assert.match(source, /function ChangePasswordDialog\(/)
  assert.match(source, /changePwd:\s*ChangePasswordDialog/)
  assert.match(source, /\/api\/v1\/auth\/change-password/)
})
