/** Apply versioned generation events without mixing revisions. */
export function nextDraft(state = { attemptId: null, text: '' }, event, payload) {
  if (event === 'draft_start') return { attemptId: payload.attemptId ?? null, text: '' }
  if (event !== 'token') return state
  if (payload.attemptId != null && state.attemptId != null && payload.attemptId !== state.attemptId) return state
  return { attemptId: payload.attemptId ?? state.attemptId, text: state.text + (payload.text ?? '') }
}
