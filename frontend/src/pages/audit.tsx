import { useEffect, useState } from 'react'
import { Download, ShieldAlert } from 'lucide-react'
import { toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Avatar, Badge, PageHeader, type Tone } from '../components/common'
import { cn } from '../lib/utils'

const resultTone: Record<string, Tone> = { success: 'suc', failed: 'err', denied: 'warn' }
const actorTone: Record<string, Tone> = { user: 'info', agent: 'pur', system: 'gry' }

interface AuditItem {
  time: string; actor: string; actorType: string; authorized: string;
  action: string; target: string; result: string; reqId: string; ip: string;
}

export function AuditPage() {
  const [action, setAction] = useState('')
  const [result, setResult] = useState('')
  const [actor, setActor] = useState('')
  const [page, setPage] = useState(1)
  const [list, setList] = useState<AuditItem[]>([])
  const [total, setTotal] = useState(0)
  const PAGE_SIZE = 10
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  /* 后端已按 page/page_size 分页返回，list 即当前页数据，无需再次切片（修复双重分页：slice 后第 2 页恒为空） */
  const paged = list

  /* 接后端：GET /audits（后端分页 + 过滤） */
  useEffect(() => {
    const qs = new URLSearchParams({ page: String(safePage), page_size: String(PAGE_SIZE) })
    if (action) qs.set('action', action)
    if (result) qs.set('result', result)
    if (actor) qs.set('actor_type', actor)
    api.get<{ items: AuditItem[]; total: number }>(`/api/v1/audits?${qs}`)
      .then((d) => { if (d.items.length || d.total > 0) { setList(d.items); setTotal(d.total) } })
      .catch(() => { /* 后端不可用：空列表 */ })
  }, [action, result, actor, safePage])

  const exportAudits = () => {
    api.post('/api/v1/audits/export')
      .then(() => toast('导出将记录一次新的审计事件'))
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '导出失败'))
  }

  return (
    <div className="page-container">
      <PageHeader
        title="审计中心"
        sub="审计记录不可删除 · 管理员查看与导出必须再次审计（PRD §15.2）· actor / authorized_user / request_id 全链路可追溯"
        actions={
          <button className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
            onClick={exportAudits}>
            <Download className="h-4 w-4" />导出审计
          </button>
        }
      />

      <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-3 text-[12px] leading-relaxed text-slate-500 shadow-s dark:border-slate-700 dark:bg-slate-900 dark:text-slate-400">
        <ShieldAlert className="mt-0.5 h-4 w-4 flex-none text-amber-500" />
        <span>审计字段：actor_type / actor_id / authorized_user_id / action / target / organization_id / project_id / request_id / before / after / result / failure_reason / ip / user_agent / created_at（PRD §12）。</span>
      </div>

      <div className="mb-4 grid gap-2.5 md:grid-cols-3">
        <input className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" placeholder="按动作过滤，如 task:submit…" value={action} onChange={(e) => setAction(e.target.value)} />
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={result} onChange={(e) => setResult(e.target.value)}>
          <option value="">全部结果</option><option>success</option><option>failed</option><option>denied</option>
        </select>
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={actor} onChange={(e) => setActor(e.target.value)}>
          <option value="">全部主体</option><option>user</option><option>agent</option><option>system</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800/60">
                <th className="px-4 py-3 font-medium">时间</th>
                <th className="px-4 py-3 font-medium">主体</th>
                <th className="px-4 py-3 font-medium">授权用户</th>
                <th className="px-4 py-3 font-medium">动作</th>
                <th className="px-4 py-3 font-medium">目标</th>
                <th className="px-4 py-3 font-medium">结果</th>
                <th className="px-4 py-3 font-medium">请求 ID</th>
                <th className="px-4 py-3 font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((r, i) => (
                <tr key={i} className={cn('border-t border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40',
                  r.result === 'denied' && 'bg-amber-50/40 dark:bg-amber-500/5')}>
                  <td className="whitespace-nowrap px-4 py-3 text-slate-400">{r.time}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      {r.actorType === 'system' ? (
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-200 text-[10px] font-semibold text-slate-500 dark:bg-slate-700">系</span>
                      ) : (
                        <Avatar name={r.actor} grad={r.actorType === 'agent' ? 'g4' : 'g1'} size={22} />
                      )}
                      <span className="font-medium text-slate-600 dark:text-slate-300">{r.actor}</span>
                      <Badge tone={actorTone[r.actorType]} className="!px-1.5 !text-[10px]">{r.actorType}</Badge>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-400">{r.authorized}</td>
                  <td className="px-4 py-3"><span className="font-mono text-[11.5px] text-blue-600 dark:text-blue-400">{r.action}</span></td>
                  <td className="max-w-[260px] truncate px-4 py-3 text-slate-500 dark:text-slate-400">{r.target}</td>
                  <td className="px-4 py-3"><Badge tone={resultTone[r.result]}>{r.result}</Badge></td>
                  <td className="px-4 py-3 font-mono text-[11px] text-slate-400">{r.reqId}</td>
                  <td className="px-4 py-3 font-mono text-[11px] text-slate-400">{r.ip}</td>
                </tr>
              ))}
              {list.length === 0 && <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">无匹配审计记录</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-[12px] text-slate-400 dark:border-slate-800">
          <span>共 {total} 条记录 · 最近 7 天 · 第 {safePage}/{totalPages} 页</span>
          <div className="flex items-center gap-1.5">
            <button
              className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                safePage <= 1 ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
              disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
              上一页
            </button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
              <button key={p}
                className={cn('h-7 w-7 rounded-md border text-[12px] transition-colors',
                  p === safePage ? 'border-blue-600 bg-blue-600 text-white' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
                onClick={() => setPage(p)}>{p}</button>
            ))}
            <button
              className={cn('h-7 rounded-md border px-2.5 text-[12px] transition-colors',
                safePage >= totalPages ? 'cursor-not-allowed border-slate-200 text-slate-300 dark:border-slate-800 dark:text-slate-600' : 'border-slate-300 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900')}
              disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
