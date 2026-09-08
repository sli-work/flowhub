import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/pages/canvas.tsx', import.meta.url), 'utf8')

test('canvas uses React Flow handles for deterministic node connections', () => {
  assert.match(source, /from '@xyflow\/react'/)
  assert.match(source, /<ReactFlow/)
  assert.match(source, /onConnect=\{onConnect\}/)
  assert.match(source, /type="source"/)
  assert.match(source, /type="target"/)
})

test('canvas rejects invalid main-flow edges before they mutate state', () => {
  assert.match(source, /const connectionError/)
  assert.match(source, /不能连接到自身节点/)
  assert.match(source, /开始节点不允许有主线入边/)
  assert.match(source, /结束节点不允许有主线出边/)
  assert.match(source, /主线不能形成环/)
})

test('canvas auto-layout reads left-to-right by topology layers', () => {
  assert.match(source, /按流转顺序从左到右分层排布/)
  assert.match(source, /x: 48 \+ level \* \(NODE_W \+ 96\)/)
})
