import { useEffect, useState } from 'react'
import { FileText, RotateCw } from 'lucide-react'
import { api } from '../lib/api'
import { cn } from '../lib/utils'
import type { AttachmentEvidence } from '../types'
import { toast } from '../store/app-store'

const STATUS_TONE: Record<string, string> = {
  indexed: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300',
  needs_ocr: 'bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-300',
  failed: 'bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-300',
  skipped: 'bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400',
}

function statusLabel(status: string, cacheHit: boolean, selected: boolean): string {
  if (!selected) return '未使用'
  if (status === 'indexed') return cacheHit ? '缓存命中' : '已索引'
  if (status === 'needs_ocr') return '需 OCR'
  if (status === 'failed') return '失败'
  return status
}

export function AttachmentEvidencePanel({ taskId, evidence }: { taskId: string; evidence?: AttachmentEvidence }) {
  const [state, setState] = useState<AttachmentEvidence | undefined>(evidence)
  const [busy, setBusy] = useState(false)
  useEffect(() => setState(evidence), [evidence])

  const failed = (state?.parsed ?? []).some((p) => p.status === 'failed')
  const reparse = async () => {
    if (!taskId || busy) return
    setBusy(true)
    try {
      const d = await api.post<{ attachments: AttachmentEvidence['parsed']; injected: AttachmentEvidence['injected']; durationMs: number }>(
        `/api/v1/tasks/${taskId}/reparse-attachments`,
      )
      setState((prev) => ({ ...(prev ?? { candidates: [], totalChars: 0, durationMs: 0 }), parsed: d.attachments, injected: d.injected, durationMs: d.durationMs }))
      toast.success('附件解析完成')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '重试解析失败')
    } finally {
      setBusy(false)
    }
  }

  if (!state || (!state.parsed?.length && !state.injected?.length)) return null

  return (
    <div className="mt-2.5 rounded-lg border border-slate-200 bg-white/60 px-2.5 py-2 dark:border-slate-700 dark:bg-slate-900/40">
      <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
        <span className="font-semibold text-slate-600 dark:text-slate-300">附件证据</span>
        <span className="text-slate-400">本轮送入 {state.injected?.length ?? 0} 片段 · {state.totalChars ?? 0} 字 · {(state.durationMs ?? 0) / 1000}s</span>
        {failed && (
          <button className="ml-auto inline-flex items-center gap-1 rounded-md border border-violet-300 px-2 py-0.5 font-medium text-violet-600 transition-colors hover:bg-violet-50 disabled:opacity-50 dark:border-violet-500/40 dark:text-violet-300"
            disabled={busy} onClick={() => void reparse()}>
            <RotateCw className={cn('h-3 w-3', busy && 'animate-spin')} />{busy ? '解析中…' : '重试解析'}
          </button>
        )}
      </div>
      <details className="mt-1.5">
        <summary className="cursor-pointer text-[11px] font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-200">展开证据片段与来源定位</summary>
        <div className="mt-2 space-y-1.5">
          {(state.candidates ?? []).map((c) => {
            const parsedEntry = state.parsed?.find((p) => p.id === c.id)
            const used = !!parsedEntry || c.selected === true
            return (
              <div key={c.id} className="flex items-center gap-2 text-[11px]">
                <FileText className="h-3 w-3 flex-none text-blue-500" />
                <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300" title={c.reason}>{c.name}</span>
                <span className={cn('flex-none rounded px-1.5 py-px font-medium', STATUS_TONE[parsedEntry?.status ?? 'skipped'] ?? STATUS_TONE.skipped)}>
                  {statusLabel(parsedEntry?.status ?? 'skipped', parsedEntry?.cacheHit ?? false, used)}
                </span>
              </div>
            )
          })}
          {(state.injected ?? []).map((chunk) => (
            <div key={`${chunk.docId}:${chunk.seq}`} className="rounded-md bg-slate-50 px-2 py-1.5 text-[11px] leading-relaxed text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              <span className="mr-1.5 rounded bg-violet-100 px-1 py-px font-mono text-[10px] text-violet-700 dark:bg-violet-500/20 dark:text-violet-300">
                {chunk.docName}:{chunk.location}:{chunk.seq}
              </span>
              {chunk.text}
            </div>
          ))}
        </div>
      </details>
    </div>
  )
}
