import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')

test('task progress orders template nodes by canvas edges instead of stored node-array order', () => {
  assert.match(source, /function orderCanvasNodes\(/)
  assert.match(source, /orderCanvasNodes\(canvas\.nodes, canvas\.edges\)/)
})
