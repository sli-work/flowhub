export type DraftState = { attemptId: string | number | null; text: string }
export function nextDraft(state: DraftState | undefined, event: string, payload: { attemptId?: string | number; text?: string }): DraftState
