import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')

test('task detail initializes the form with the persisted formValues snapshot', () => {
  assert.match(source, /setFormValues\(td\.task\.formValues \?\? \{\}\)/)
})
