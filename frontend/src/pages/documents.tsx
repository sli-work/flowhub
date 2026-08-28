import { useEffect, useRef, useState } from 'react'
import { Upload, ShieldAlert } from 'lucide-react'
import { toast } from '../store/app-store'
import { api, ApiError, getToken } from '../lib/api'
import { Badge, PageHeader, kindIcon, type Tone } from '../components/common'
import { cn } from '../lib/utils'
import { DocumentViewerDrawer } from '../components/document-viewer-drawer'
import type { DocItem } from '../types'

const levelTone: Record<string, Tone> = { L1: 'suc', L2: 'info', L3: 'err' }
const scanTone: Record<string, Tone> = { 已扫描: 'suc', 含毒: 'err', 扫描中: 'warn' }

export function DocumentsPage() {
  const [filter, setFilter] = useState<'all' | 'L1' | 'L2' | 'L3' | 'infected'>('all')
  const [docs, setDocs] = useState<DocItem[]>([])
  const [viewer, setViewer] = useState<{ open: boolean; initialId?: string }>({ open: false })
  const fileRef = useRef<HTMLInputElement>(null)

  /* 接后端：GET /documents 拉取文档列表 */
  useEffect(() => {
    api.get<{ items: DocItem[] }>('/api/v1/documents?page_size=100')
      .then((d) => { if (d.items.length) setDocs(d.items) })
      .catch(() => { /* 保持演示数据 */ })
  }, [])

  const list = docs.filter((d) => {
    if (filter === 'all') return true
    if (filter === 'infected') return d.scan === '含毒'
    return d.level === filter
  })

  const [name, setName] = useState('')
  const [proj, setProj] = useState('')
  const [level, setLevel] = useState('')
  const [scan, setScan] = useState('')
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 5
  const searched = list.filter((d) =>
    (!name || d.name.includes(name)) && (!proj || d.project.includes(proj)) &&
    (!level || d.level === level) && (!scan || d.scan === scan))
  const totalPages = Math.max(1, Math.ceil(searched.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const paged = searched.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)
  useEffect(() => { setPage(1) }, [filter, name, proj, level, scan])

  /* 上传：multipart → POST /documents/upload（后端校验链） */
  const upload = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('project', '未归档')
    fd.append('kind', '文档')
    const token = getToken()
    try {
      const resp = await fetch('/api/v1/documents/upload', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      })
      const payload = await resp.json()
      if (!resp.ok || payload.code !== 0) throw new ApiError(payload.code ?? resp.status, payload.message || '上传失败', resp.status)
      setDocs((prev) => [payload.data.doc, ...prev])
      toast.success(`上传成功：${payload.data.doc.name}（扩展名/MIME/大小校验通过）`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '上传失败')
    }
  }

  /* 文件名点击 → 统一预览抽屉（含毒文件拦截；下载在抽屉内） */
  const openViewer = (d: DocItem) => {
    if (d.scan === '含毒') { toast.error('含毒文件已拦截，禁止预览与下载'); return }
    setViewer({ open: true, initialId: d.id })
  }

  return (
    <div className="page-container">
      <PageHeader
        title="文档中心"
        sub="MinIO 内网存储 · 文件仅通过权限代理访问短时链接 · 扩展名/MIME/大小/病毒/压缩炸弹校验"
        actions={
          <>
            <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
              onClick={() => fileRef.current?.click()}>
              <Upload className="mr-1 inline h-4 w-4" />上传文档
            </button>
            <input
              ref={fileRef} type="file" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }}
            />
          </>
        }
      />

      {/* 敏感级别提示 */}
      <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 p-3 text-[12px] leading-relaxed text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
        <ShieldAlert className="mt-0.5 h-4 w-4 flex-none" />
        <span>敏感级别策略：L3 文件禁止下载到未绑定终端、禁止发送给外部 Agent；下载、预览、版本回滚、删除与恢复均写审计日志（PRD §10）。</span>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {([
          ['all', '全部', `${docs.length}`], ['L1', 'L1 公开', '3'], ['L2', 'L2 内部', '5'], ['L3', 'L3 敏感', '2'], ['infected', '含毒拦截', '1'],
        ] as const).map(([k, label, cnt]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={cn('rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors',
              filter === k ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 hover:text-blue-600 dark:bg-slate-900 dark:text-slate-400')}>
            {label} <span className="opacity-60">{cnt}</span>
          </button>
        ))}
      </div>

      <div className="mb-4 grid gap-2.5 md:grid-cols-4">
        <input className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" placeholder="按文件名过滤…" value={name} onChange={(e) => setName(e.target.value)} />
        <input className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" placeholder="按项目过滤…" value={proj} onChange={(e) => setProj(e.target.value)} />
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="">全部级别</option><option>L1</option><option>L2</option><option>L3</option>
        </select>
        <select className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" value={scan} onChange={(e) => setScan(e.target.value)}>
          <option value="">扫描状态</option><option>已扫描</option><option>含毒</option><option>扫描中</option>
        </select>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="bg-slate-50 text-left text-xs text-slate-500 dark:bg-slate-800/60">
                <th className="px-4 py-3 font-medium">文件名</th>
                <th className="px-4 py-3 font-medium">项目</th>
                <th className="px-4 py-3 font-medium">版本</th>
                <th className="px-4 py-3 font-medium">敏感级别</th>
                <th className="px-4 py-3 font-medium">扫描</th>
                <th className="px-4 py-3 font-medium">上传人</th>
                <th className="px-4 py-3 font-medium">大小</th>
                <th className="px-4 py-3 font-medium">时间</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((d: DocItem) => (
                <tr key={d.id} className="border-t border-slate-100 transition-colors hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/40">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5">
                      <span className={cn('flex h-7 w-7 flex-none items-center justify-center rounded-md',
                        'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400')}>{kindIcon(d.kind)}</span>
                      <div className="min-w-0">
                        <div className="cursor-pointer truncate font-medium text-slate-700 hover:text-blue-600 dark:text-slate-200" onClick={() => openViewer(d)}>{d.name}</div>
                        <div className="text-[11px] text-slate-400">{d.kind}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{d.project}</td>
                  <td className="px-4 py-3"><Badge tone="info">{d.version}</Badge></td>
                  <td className="px-4 py-3"><Badge tone={levelTone[d.level]}>{d.level}</Badge></td>
                  <td className="px-4 py-3"><Badge tone={scanTone[d.scan]} dot>{d.scan}</Badge></td>
                  <td className="px-4 py-3 text-slate-500 dark:text-slate-400">{d.uploader}</td>
                  <td className="px-4 py-3 text-slate-400">{d.size}</td>
                  <td className="px-4 py-3 text-slate-400">{d.time}</td>
                </tr>
              ))}
              {searched.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">无匹配文档，调整过滤条件后重试</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-[12px] text-slate-400 dark:border-slate-800">
          <span>共 {searched.length} 个文件 · 合计 62.8 MB · 第 {safePage}/{totalPages} 页</span>
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
      <DocumentViewerDrawer
        open={viewer.open}
        docs={searched.map((d) => ({ id: d.id, name: d.name, uploader: d.uploader, kind: d.kind, time: d.time }))}
        initialDocId={viewer.initialId}
        onClose={() => setViewer({ open: false })}
      />
    </div>
  )
}
