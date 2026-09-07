import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')

test('unauthenticated login view mounts the dialog host for local-account registration', () => {
  assert.match(
    appSource,
    /if \(!authed\) return <>\s*<LoginPage\s*\/?>\s*<DialogHost\s*\/?>\s*<\/>/s,
    'the registration dialog opened by LoginPage must be mounted before authentication',
  )
})
