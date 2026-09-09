import { useEffect, useRef, useState } from 'react'
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor, type NodeViewProps } from '@tiptap/react'
import { Node, mergeAttributes } from '@tiptap/core'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import { Bold, Code2, Image as ImageIcon, Italic, List, ListOrdered, Quote, Redo2, Undo2 } from 'lucide-react'
import { api, getToken } from '../lib/api'

export type IssueDocument = { type: 'doc'; content?: Array<Record<string, unknown>> }
export type IssueImage = { id: string; name: string }

export function issueDocumentText(doc: IssueDocument | undefined): string {
  const text: string[] = []
  const visit = (node: unknown) => {
    if (!node || typeof node !== 'object') return
    const item = node as { type?: string; text?: string; content?: unknown[] }
    if (item.type === 'text' && typeof item.text === 'string') text.push(item.text)
    item.content?.forEach(visit)
  }
  visit(doc)
  return text.join('').trim()
}

export function legacyIssueDocument(text: string, images: IssueImage[] = []): IssueDocument {
  return {
    type: 'doc',
    content: [
      ...text.split(/\n+/).filter(Boolean).map((line) => ({ type: 'paragraph', content: [{ type: 'text', text: line }] })),
      ...images.map((image) => ({ type: 'flowhubImage', attrs: { documentId: image.id, name: image.name } })),
    ],
  }
}

function FlowhubImageNodeView({ node, extension, deleteNode }: NodeViewProps) {
  const documentId = String(node.attrs.documentId ?? '')
  const name = String(node.attrs.name ?? '截图')
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!documentId) return
    const controller = new AbortController()
    let objectUrl = ''
    void api.getBlob(`/api/v1/documents/${documentId}/download`, controller.signal).then((blob) => {
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
    }).catch(() => setUrl(''))
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [documentId])
  const preview = (extension.options as { onPreview?: (image: IssueImage) => void }).onPreview
  return <NodeViewWrapper className="my-2 block max-w-md" data-drag-handle="">
    <figure className="group relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-800">
      <button type="button" contentEditable={false} className="block w-full" onClick={() => preview?.({ id: documentId, name })} title={`放大预览 ${name}`}>
        {url ? <img src={url} alt={name} className="max-h-80 w-full object-contain" /> : <span className="flex aspect-video items-center justify-center text-slate-400"><ImageIcon className="mr-1 h-4 w-4" />加载截图…</span>}
      </button>
      <figcaption className="flex items-center gap-2 px-2 py-1 text-[11px] text-slate-500"><span className="min-w-0 flex-1 truncate">{name}</span>{deleteNode && <button type="button" contentEditable={false} className="opacity-0 transition-opacity group-hover:opacity-100 hover:text-red-500" onClick={() => deleteNode()} aria-label={`移除 ${name}`}>移除</button>}</figcaption>
    </figure>
  </NodeViewWrapper>
}

const FlowhubImage = Node.create<{ onPreview?: (image: IssueImage) => void }>({
  name: 'flowhubImage', group: 'block', atom: true, draggable: true,
  addOptions() { return { onPreview: undefined } },
  addAttributes() { return { documentId: { default: null }, name: { default: '截图' } } },
  parseHTML() { return [{ tag: 'figure[data-flowhub-image]' }] },
  renderHTML({ HTMLAttributes }) { return ['figure', mergeAttributes({ 'data-flowhub-image': '' }, HTMLAttributes), ['figcaption', HTMLAttributes.name || '截图']] },
  addNodeView() { return ReactNodeViewRenderer(FlowhubImageNodeView) },
})

async function uploadImages(files: File[], project: string, workItemId: string): Promise<IssueImage[]> {
  const refs: IssueImage[] = []
  for (const file of files) {
    const body = new FormData()
    body.append('file', file); body.append('project', project); body.append('wi', workItemId)
    const token = getToken()
    const response = await fetch('/api/v1/documents/images/upload', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body })
    const payload = await response.json().catch(() => ({})) as { code?: number; message?: string; data?: { doc?: IssueImage } }
    if (!response.ok || payload.code !== 0 || !payload.data?.doc) throw new Error(payload.message || `${file.name} 上传失败`)
    refs.push(payload.data.doc)
  }
  return refs
}

function ToolbarButton({ label, onClick, children, active }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return <button type="button" title={label} aria-label={label} onClick={onClick} className={`rounded p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-slate-800 dark:hover:text-slate-100 ${active ? 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300' : ''}`}>{children}</button>
}

export function IssueRichTextEditor({ value, onChange, project, workItemId, onPreview }: { value: IssueDocument; onChange: (doc: IssueDocument) => void; project: string; workItemId: string; onPreview: (image: IssueImage) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const editor = useEditor({
    extensions: [StarterKit, Placeholder.configure({ placeholder: '问题现象、复现步骤和期望结果' }), FlowhubImage.configure({ onPreview })],
    content: value,
    editorProps: {
      attributes: { class: 'min-h-28 max-h-[70vh] resize-y overflow-auto p-3 text-sm leading-6 outline-none prose prose-sm max-w-none dark:prose-invert' },
      handlePaste: (_view, event) => {
        const files = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
        if (!files.length) return false
        void addImages(files); return true
      },
      handleDrop: (_view, event) => {
        const files = Array.from(event.dataTransfer?.files ?? []).filter((file) => file.type.startsWith('image/'))
        if (!files.length) return false
        void addImages(files); return true
      },
    },
    onUpdate: ({ editor: current }) => onChange(current.getJSON() as IssueDocument),
  })
  const addImages = async (files: File[]) => {
    if (!editor || uploading || !files.length) return
    const currentCount = editor.getJSON().content?.filter((node) => node.type === 'flowhubImage').length ?? 0
    if (currentCount + files.length > 20) { setError(`正文最多添加 20 张截图，当前还能添加 ${20 - currentCount} 张`); return }
    setUploading(true); setError('')
    try { (await uploadImages(files, project, workItemId)).forEach((image) => editor.chain().focus().insertContent({ type: 'flowhubImage', attrs: { documentId: image.id, name: image.name } }).run()) }
    catch (err) { setError(err instanceof Error ? err.message : '截图上传失败') }
    finally { setUploading(false) }
  }
  if (!editor) return null
  return <div className="rounded-lg border border-slate-300 dark:border-slate-700">
    <div className="flex flex-wrap items-center gap-0.5 border-b border-slate-200 p-1 dark:border-slate-700">
      <ToolbarButton label="加粗" active={editor.isActive('bold')} onClick={() => editor.chain().focus().toggleBold().run()}><Bold className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="斜体" active={editor.isActive('italic')} onClick={() => editor.chain().focus().toggleItalic().run()}><Italic className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="无序列表" active={editor.isActive('bulletList')} onClick={() => editor.chain().focus().toggleBulletList().run()}><List className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="有序列表" active={editor.isActive('orderedList')} onClick={() => editor.chain().focus().toggleOrderedList().run()}><ListOrdered className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="引用" active={editor.isActive('blockquote')} onClick={() => editor.chain().focus().toggleBlockquote().run()}><Quote className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="代码块" active={editor.isActive('codeBlock')} onClick={() => editor.chain().focus().toggleCodeBlock().run()}><Code2 className="h-3.5 w-3.5" /></ToolbarButton>
      <span className="mx-1 h-4 border-l border-slate-200 dark:border-slate-700" />
      <ToolbarButton label="撤销" onClick={() => editor.chain().focus().undo().run()}><Undo2 className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="重做" onClick={() => editor.chain().focus().redo().run()}><Redo2 className="h-3.5 w-3.5" /></ToolbarButton>
      <ToolbarButton label="上传截图" onClick={() => inputRef.current?.click()}><ImageIcon className="h-3.5 w-3.5" /></ToolbarButton>
      {uploading && <span className="ml-1 text-[11px] text-slate-400">上传截图中…</span>}
    </div>
    <input ref={inputRef} type="file" className="hidden" accept="image/png,image/jpeg,image/webp" multiple onChange={(event) => { void addImages(Array.from(event.target.files ?? [])); event.target.value = '' }} />
    <EditorContent editor={editor} />
    {error && <p className="px-3 pb-2 text-[11px] text-red-500">{error}</p>}
  </div>
}

export function IssueRichTextView({ value, onPreview }: { value: IssueDocument; onPreview: (image: IssueImage) => void }) {
  const editor = useEditor({ extensions: [StarterKit, FlowhubImage.configure({ onPreview })], content: value, editable: false, editorProps: { attributes: { class: 'issue-rich-text mt-1 text-slate-500 dark:text-slate-400 prose prose-sm max-w-none dark:prose-invert' } } })
  return editor ? <EditorContent editor={editor} /> : null
}
