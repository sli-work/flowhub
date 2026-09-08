import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const types = readFileSync(new URL('../src/types/index.ts', import.meta.url), 'utf8')
const form = readFileSync(new URL('../src/components/schema-form.tsx', import.meta.url), 'utf8')
const node = readFileSync(new URL('../src/pages/node.tsx', import.meta.url), 'utf8')

test('template schema offers a multi-image field with image-only upload and thumbnail preview', () => {
  assert.match(types, /\| 'date' \| 'number' \| 'upload' \| 'file' \| 'image'/)
  assert.match(form, /image: '图片上传'/)
  assert.match(form, /case 'image'/)
  assert.match(form, /accept=\{imageOnly \? 'image\/png,image\/jpeg,image\/webp' : undefined\}/)
  assert.match(form, /\/api\/v1\/documents\/images\/upload/)
  assert.match(form, /图片上传（可多选）/)
  assert.match(form, /<img/)
  assert.match(node, /fieldType === 'image'/)
})
