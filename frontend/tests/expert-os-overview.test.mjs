import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const overview = readFileSync(new URL('../src/pages/expert-os-overview.tsx', import.meta.url), 'utf8')
const store = readFileSync(new URL('../src/store/expert-os-store.tsx', import.meta.url), 'utf8')
const components = readFileSync(new URL('../src/components/expert-os.tsx', import.meta.url), 'utf8')

test('OS overview renders actual run activity and expert ranking instead of placeholders', () => {
  assert.match(overview, /buildDailyRunSeries/)
  assert.match(overview, /buildExpertRanking/)
  assert.doesNotMatch(overview, /调用统计待后端聚合接口/)
  assert.doesNotMatch(overview, /较前 7 日 \+18\.4%/)
  assert.match(overview, /ResponsiveContainer/)
  assert.match(overview, /BarChart/)
})

test('OS overview metrics carry explicit icons', () => {
  assert.match(components, /icon\?: ReactNode/)
  for (const icon of ['Bot', 'Puzzle', 'PlugZap', 'Activity', 'ShieldAlert']) {
    assert.match(overview, new RegExp(`icon: <${icon}`))
  }
})

test('OS resource loading tolerates one failed endpoint without blanking all overview cards', () => {
  assert.match(store, /Promise\.allSettled/)
  assert.match(store, /fulfilledValue/)
  assert.match(store, /expert-runs\?page_size=100/)
  assert.doesNotMatch(store, /const \[experts, providers, deployments, runs, approvals, skills, mcp\] = await Promise\.all/)
})
