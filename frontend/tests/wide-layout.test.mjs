import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const source = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')
const frontendRoot = fileURLToPath(new URL('..', import.meta.url))
const customLayoutPages = new Set(['aichat.tsx', 'login.tsx'])

test('shared page container uses the same 1600px wide-screen baseline as task processing', () => {
  const css = source('src/index.css')

  assert.match(
    css,
    /\.page-container\s*\{[^}]*max-w-\[1600px\][^}]*\}/s,
    'page-container should expand regular pages to 1600px on large displays',
  )
})

test('regular pages rely on the shared responsive page container', () => {
  const pageFiles = readdirSync(resolve(frontendRoot, 'src/pages'))
    .filter((name) => name.endsWith('.tsx') && !customLayoutPages.has(name))

  assert.ok(pageFiles.length > 1, 'expected multiple regular pages to use page-container')
  for (const name of pageFiles) {
    const path = resolve(frontendRoot, 'src/pages', name)
    const pageSource = readFileSync(path, 'utf8')
    assert.match(pageSource, /className="page-container"/, `${name} should use the shared page container`)
    assert.doesNotMatch(
      pageSource,
      /page-container[^"']*max-w-\[(?:[0-9]{1,3}|1[0-5][0-9]{2})px\]/,
      `${name} should not override the shared container with a narrower width`,
    )
  }
})

test('chat SSE consumers stop reading immediately after the terminal done event', () => {
  const aiChat = readFileSync(resolve(frontendRoot, 'src/pages/aichat.tsx'), 'utf8')
  const expertDrawer = readFileSync(resolve(frontendRoot, 'src/components/expert-chat-drawer.tsx'), 'utf8')

  assert.match(aiChat, /let finished = false/)
  assert.match(aiChat, /if \(event === "done" && data\.message\) \{[\s\S]*?finished = true/)
  assert.match(aiChat, /if \(finished \|\| chunk\.done\) break/)
  assert.match(expertDrawer, /let finished = false/)
  assert.match(expertDrawer, /if \(event === 'done' && data\.message\) \{[\s\S]*?finished = true/)
  assert.match(expertDrawer, /if \(finished \|\| chunk\.done\) break/)
})
