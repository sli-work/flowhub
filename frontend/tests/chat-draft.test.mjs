import assert from 'node:assert/strict'
import test from 'node:test'
import { nextDraft } from '../src/lib/chat-draft.mjs'

test('revision replaces earlier draft and ignores late tokens', () => {
  let state = nextDraft(undefined, 'draft_start', { attemptId: 1 })
  state = nextDraft(state, 'token', { attemptId: 1, text: 'old' })
  state = nextDraft(state, 'draft_start', { attemptId: 2 })
  state = nextDraft(state, 'token', { attemptId: 1, text: 'late' })
  assert.equal(state.text, '')
  assert.equal(nextDraft(state, 'token', { attemptId: 2, text: 'new' }).text, 'new')
})
test('legacy token streams remain supported', () => {
  assert.equal(nextDraft(undefined, 'token', { text: 'hello' }).text, 'hello')
})
