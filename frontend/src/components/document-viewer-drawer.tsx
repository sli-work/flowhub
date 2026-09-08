import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { Download, FileText, LoaderCircle, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react'
import FileViewer from '@file-viewer/react'
import type { FileViewerOptions } from '@file-viewer/core'
import officePreset from '@file-viewer/preset-office'
import litePreset from '@file-viewer/preset-lite'
import { api } from '../lib/api'
import { cn } from '../lib/utils'
import { Badge } from './common'

export interface ViewerDoc {
  id: string
  name: string
  uploader?: string
  kind?: string
  time?: string
}

type ThemeMode = 'light' | 'dark'
type ArchiveEntry = { path: string; size: number }
type ArchivePreview = { preview_type: 'axure' | 'archive'; entries: ArchiveEntry[] }

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function useThemeMode(): ThemeMode {
  const [mode, setMode] = useState<ThemeMode>(() => (document.documentElement.classList.contains('dark') ? 'dark' : 'light'))
  useEffect(() => {
    const observer = new MutationObserver(() => setMode(document.documentElement.classList.contains('dark') ? 'dark' : 'light'))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [])
  return mode
}

/**
 * 统一文档预览抽屉：AiChat 产出 / 任务处理上传 / Expert 生成的文档都在这里预览。
 * 鉴权：fetchFile 钩子带 Bearer 请求后端 download 接口，组件按需拉取（支持 Blob/File 返回）。
 */
export function DocumentViewerDrawer({ open, docs, initialDocId, onClose }: { open: boolean; docs: ViewerDoc[]; initialDocId?: string; onClose: () => void }) {
  const themeMode = useThemeMode()
  const [currentId, setCurrentId] = useState<string | null>(initialDocId ?? docs[0]?.id ?? null)
  const [listOpen, setListOpen] = useState(true)
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [archiveEntries, setArchiveEntries] = useState<ArchiveEntry[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const blobRef = useRef<string | null>(null)
  /* 左缘拖拽调宽：默认 min(1100px, 94vw)，可向左拉伸扩展阅读范围 */
  const [width, setWidth] = useState(() => Math.min(1100, Math.round(window.innerWidth * 0.94)))
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null)

  const startResize = (e: ReactMouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startWidth: width }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return
      const max = Math.max(480, Math.min(window.innerWidth * 0.94, 2200))
      const next = dragRef.current.startWidth + (dragRef.current.startX - ev.clientX)
      setWidth(Math.round(Math.min(Math.max(next, 480), max)))
    }
    const onUp = () => {
      dragRef.current = null
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const current = useMemo(() => docs.find((d) => d.id === currentId) ?? null, [docs, currentId])
  const resolvedTheme: ThemeMode = themeMode
  const isZip = !!current && current.name.toLowerCase().endsWith('.zip')
  /* FileViewer 的 options 按引用比较（变化即触发 controller.update 重载文档），
     必须 memoize，否则父组件任意重渲染（如任务页 3s 轮询）都会让 PDF 反复刷新 */
  const viewerOptions = useMemo<FileViewerOptions>(() => ({
    preset: [officePreset, litePreset],
    rendererMode: 'replace',
    theme: resolvedTheme,
    search: { enabled: true },
    toolbar: { position: 'bottom-right' },
  }), [resolvedTheme])

  /* 选中记忆：仅在抽屉打开或 initialDocId 变化时重置；
     docs 引用变化（内容相同，如父组件轮询重建数组）不打断用户当前选择 */
  const wasOpenRef = useRef(false)
  const lastInitRef = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!open) { wasOpenRef.current = false; return }
    const justOpened = !wasOpenRef.current
    wasOpenRef.current = true
    if (justOpened || initialDocId !== lastInitRef.current) {
      lastInitRef.current = initialDocId
      setCurrentId(initialDocId ?? docs[0]?.id ?? null)
      return
    }
    setCurrentId((prev) => (docs.some((d) => d.id === prev) ? prev : (initialDocId ?? docs[0]?.id ?? null)))
  }, [open, initialDocId, docs])

  /* 加载效果以稳定原始值为依赖：docs/current 引用变化不触发重复下载 */
  const currentDocId = current?.id ?? null
  const currentDocName = current?.name ?? ''
  useEffect(() => {
    const doc = open ? docs.find((d) => d.id === currentDocId) : null
    if (!open || !doc) { setBlobUrl(null); setPreviewUrl(null); setArchiveEntries(null); setError(''); return }
    let revoked = false
    const controller = new AbortController()
    setLoading(true)
    setError('')
    setPreviewUrl(null)
    setArchiveEntries(null)
    // ZIP 先读取安全目录清单：包含根 index.html 的 Axure 包走 iframe，其余显示文件浏览器。
    const previewTask = doc.name.toLowerCase().endsWith('.zip')
      ? api.post<{ link: string }>(`/api/v1/documents/${doc.id}/link`)
          .then(async (d) => {
            const token = new URLSearchParams(d.link.split('?')[1] ?? '').get('token')
            if (!token) throw new Error('预览链接无效')
            const archive = await api.get<ArchivePreview>(`/api/v1/documents/${doc.id}/archive?token=${encodeURIComponent(token)}`)
            if (revoked) return
            if (archive.preview_type === 'axure') {
              setPreviewUrl(`/api/v1/documents/${doc.id}/preview/index.html?token=${encodeURIComponent(token)}`)
            } else {
              setArchiveEntries(archive.entries)
            }
          })
      : Promise.resolve()
    const blobTask = api.getBlob(`/api/v1/documents/${doc.id}/download`, controller.signal)
      .then((blob) => {
        if (revoked) return
        const url = URL.createObjectURL(blob)
        blobRef.current = url
        setBlobUrl(url)
      })
    Promise.all([previewTask, blobTask])
      .catch((e) => {
        if (!revoked) {
          const msg = e instanceof Error ? e.message : "文档加载失败";
          setError(/404|不存在|不可用/.test(msg) ? `${msg}（该文件可能来自已删除的会话产出，或存储对象已清理）` : msg);
        }
      })
      .finally(() => { if (!revoked) setLoading(false) })
    return () => {
      revoked = true
      controller.abort()
      if (blobRef.current) { URL.revokeObjectURL(blobRef.current); blobRef.current = null }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, currentDocId, currentDocName])

  if (!open) return null

  const download = () => {
    if (!blobUrl || !current) return
    const anchor = document.createElement('a')
    anchor.href = blobUrl
    anchor.download = current.name
    anchor.click()
  }

  return (
    <div className="fixed inset-0 z-[80] flex justify-end" role="dialog" aria-modal="true" aria-label="文档预览">
      <button className="absolute inset-0 cursor-default bg-slate-950/40 backdrop-blur-[1px]" aria-label="关闭预览" onClick={onClose} />
      <aside style={{ width }} className="relative flex h-full flex-col border-l border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-950">
        {/* 左缘拖拽手柄：向左拖动拉宽预览区 */}
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="拖拽调整预览宽度"
          onMouseDown={startResize}
          onDoubleClick={() => setWidth(Math.min(1100, Math.round(window.innerWidth * 0.94)))}
          className="group absolute inset-y-0 left-0 z-20 flex w-1.5 cursor-col-resize items-center justify-center hover:bg-blue-500/20"
        >
          <div className="h-10 w-1 rounded-full bg-slate-200 transition-colors group-hover:bg-blue-500 dark:bg-slate-600" />
        </div>
        <header className="flex flex-none items-center gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <button className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            onClick={() => setListOpen((v) => !v)} aria-label={listOpen ? '收起文件列表' : '展开文件列表'}>
            {listOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
          </button>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[14px] font-semibold text-slate-900 dark:text-slate-100">{current?.name ?? '文档预览'}</h2>
            {current && (
              <p className="mt-0.5 truncate text-[11px] text-slate-400">
                {docs.length} 个文档{current.uploader ? ` · ${current.uploader}` : ''}{current.kind ? ` · ${current.kind}` : ''}{current.time ? ` · ${current.time}` : ''}
              </p>
            )}
          </div>
          {current && (
            <button className="flex h-8 flex-none items-center gap-1 rounded-lg border border-slate-200 px-2.5 text-[11.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300"
              disabled={!blobUrl} onClick={download}>
              <Download className="h-3.5 w-3.5" />下载
            </button>
          )}
          <button className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200" onClick={onClose} aria-label="关闭预览"><X className="h-4 w-4" /></button>
        </header>

        <div className="flex min-h-0 flex-1">
          {listOpen && (
            <nav className="w-60 flex-none overflow-y-auto border-r border-slate-100 bg-slate-50/70 p-2 dark:border-slate-800 dark:bg-slate-900/60">
              {docs.map((doc) => (
                <button key={doc.id}
                  className={cn('mb-1 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors',
                    doc.id === currentId ? 'bg-white shadow-sm dark:bg-slate-800' : 'hover:bg-white/70 dark:hover:bg-slate-800/60')}
                  onClick={() => setCurrentId(doc.id)}>
                  <FileText className="mt-0.5 h-4 w-4 flex-none text-blue-500" />
                  <span className="min-w-0 flex-1">
                    <span className={cn('block truncate text-[12px] font-medium', doc.id === currentId ? 'text-slate-800 dark:text-slate-100' : 'text-slate-600 dark:text-slate-300')}>{doc.name}</span>
                    <span className="block truncate text-[10.5px] text-slate-400">{[doc.kind, doc.uploader, doc.time].filter(Boolean).join(' · ') || '文档'}</span>
                  </span>
                  {doc.id === currentId && <Badge tone="info">当前</Badge>}
                </button>
              ))}
              {!docs.length && <p className="p-3 text-center text-[11.5px] text-slate-400">暂无文档</p>}
            </nav>
          )}

          <div className="relative min-w-0 flex-1 bg-slate-100 dark:bg-slate-900">
            {loading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 bg-white/70 text-[12.5px] text-slate-500 dark:bg-slate-950/70 dark:text-slate-300">
                <LoaderCircle className="h-4 w-4 animate-spin" />正在加载文档…
              </div>
            )}
            {error ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
                <p className="text-[12.5px] text-red-500">{error}</p>
                <p className="text-[11px] text-slate-400">可尝试下载后本地查看</p>
              </div>
            ) : isZip && previewUrl ? (
              <iframe
                key={current.id}
                src={previewUrl}
                title={`Axure 预览：${current.name}`}
                className="h-full w-full border-0 bg-white"
                sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
              />
            ) : isZip && archiveEntries ? (
              <div className="h-full overflow-auto bg-white p-5 dark:bg-slate-950">
                <div className="mb-4">
                  <h3 className="text-[14px] font-semibold text-slate-800 dark:text-slate-100">压缩包文件列表</h3>
                  <p className="mt-1 text-[11.5px] text-slate-400">共 {archiveEntries.length} 个文件；可下载后解压查看内容</p>
                </div>
                <ul className="overflow-hidden rounded-lg border border-slate-200 text-[12px] dark:border-slate-700">
                  {archiveEntries.map((entry) => (
                    <li key={entry.path} className="flex items-center gap-3 border-b border-slate-100 px-3 py-2 last:border-b-0 dark:border-slate-800">
                      <FileText className="h-3.5 w-3.5 flex-none text-blue-500" />
                      <span className="min-w-0 flex-1 break-all text-slate-700 dark:text-slate-200">{entry.path}</span>
                      <span className="flex-none text-[10.5px] text-slate-400">{formatFileSize(entry.size)}</span>
                    </li>
                  ))}
                  {!archiveEntries.length && <li className="px-3 py-5 text-center text-slate-400">压缩包内没有可显示的文件</li>}
                </ul>
              </div>
            ) : blobUrl && current ? (
              <FileViewer
                key={currentDocId ?? 'none'}
                url={blobUrl}
                filename={current.name}
                options={viewerOptions}
              />
            ) : null}
          </div>
        </div>
      </aside>
    </div>
  )
}
