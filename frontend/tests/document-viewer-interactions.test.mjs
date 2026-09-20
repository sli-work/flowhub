import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/components/document-viewer-drawer.tsx', import.meta.url), 'utf8')

test('image previews support zoomed pointer dragging and reset their pan state when the document changes', () => {
  assert.match(source, /function ImagePreview/)
  assert.match(source, /onPointerDown/)
  assert.match(source, /onPointerMove/)
  assert.match(source, /setPointerCapture/)
  assert.match(source, /setOffset\(\{ x: 0, y: 0 \}\)/)
  assert.match(source, /onDoubleClick/)
})

test('document preview keeps text selection enabled for native copy operations', () => {
  assert.match(source, /userSelect: 'text'/)
  assert.match(source, /styleIsolation: 'shadow'/)
})
