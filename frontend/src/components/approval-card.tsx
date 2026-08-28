import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, ShieldCheck, XCircle } from 'lucide-react'
import { api } from '../lib/api'
import { OsStatusBadge } from './expert-os'
import { useExpertOs } from '../store/expert-os-store'
import type { RunRecord } from '../types'

/**
 * 受治理写入的内联审批卡片：run 中断后就地批准/拒绝，决策后回填恢复结果。
 * 供 Expert 中心测试抽屉与编辑器 AiChat 测试抽屉共用。
 */
export function ApprovalPendingCard({ runId, onDecided }: { runId: string; onDecided?: (run: RunRecord) => void }) {
  const { state, decideApproval, fetchApprovals } = useExpertOs()
  const approval = state.approvals.find((item) => item.runId === runId)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [finalRun, setFinalRun] = useState<RunRecord | null>(null)
  const fetchedRef = useRef(false)

  // 挂载时才创建的审批单不在初始列表里，补拉一次
  useEffect(() => {
    if (!approval && !fetchedRef.current) {
      fetchedRef.current = true
      void fetchApprovals().catch(() => {})
    }
  }, [approval, fetchApprovals])

  const decide = async (decision: 'approved' | 'rejected') => {
    if (!approval || busy) return
    setBusy(true)
    setError('')
    try {
      await decideApproval(approval.id, decision)
      const detail = await api.get<{ run: RunRecord }>(`/api/v1/expert-runs/${runId}`)
      setFinalRun(detail.run)
      onDecided?.(detail.run)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '审批操作失败')
    } finally {
      setBusy(false)
    }
  }

  if (finalRun) {
    return (
      <div className={`mt-2 rounded-lg border px-3 py-2 text-[11.5px] leading-relaxed ${finalRun.status === 'succeeded' ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300' : 'border-red-200 bg-red-50 text-red-600 dark:border-red-500/30 dark:bg-red-500/10'}`}>
        <b><CheckCircle2 className="mr-1 inline h-3.5 w-3.5" />审批{approval?.status === 'approved' ? '已通过' : '已拒绝'} · 运行{finalRun.status === 'succeeded' ? '已恢复完成' : '已终止'}</b>
        {finalRun.output && <p className="mt-1 whitespace-pre-wrap break-words">{finalRun.output}</p>}
        {finalRun.error && <p className="mt-1">{finalRun.error}</p>}
      </div>
    )
  }

  if (!approval) {
    return <p className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11.5px] text-slate-500 dark:border-slate-700 dark:bg-slate-900">该运行已进入审批队列，可在「运行治理 / 审批队列」中处理。</p>
  }

  if (approval.status !== 'pending') {
    return (
      <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11.5px] text-slate-500 dark:border-slate-700 dark:bg-slate-900">
        审批{approval.status === 'approved' ? '已通过，运行恢复执行' : approval.status === 'rejected' ? '已拒绝，运行已终止' : '已过期，不可恢复执行'}。
      </div>
    )
  }

  return (
    <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11.5px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
      <div className="flex items-start gap-2">
        <ShieldCheck className="mt-0.5 h-4 w-4 flex-none" />
        <div className="min-w-0 flex-1">
          <b>{approval.action}</b>
          <span className="ml-1.5">({approval.tool})</span>
          <p className="mt-0.5 break-words">作用范围：{approval.scope || '—'}</p>
          <p className="mt-0.5 opacity-80">{approval.expires} 前有效；批准后 LangGraph 将恢复执行且该写入仅执行一次。</p>
        </div>
        <OsStatusBadge status="pending" />
      </div>
      <div className="mt-2.5 flex gap-2">
        <button className="flex-1 rounded-lg bg-emerald-600 py-1.5 text-[11.5px] font-medium text-white hover:bg-emerald-700 disabled:opacity-50" disabled={busy} onClick={() => void decide('approved')}>批准执行</button>
        <button className="flex-1 rounded-lg border border-red-300 py-1.5 text-[11.5px] font-medium text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-500/40 dark:hover:bg-red-500/10" disabled={busy} onClick={() => void decide('rejected')}><XCircle className="mr-1 inline h-3.5 w-3.5" />拒绝</button>
      </div>
      {error && <p className="mt-1.5 text-red-600 dark:text-red-400">{error}</p>}
    </div>
  )
}
