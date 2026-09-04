import { useDeferredValue, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { Upload, Paperclip, Calendar, AlertCircle, LoaderCircle } from 'lucide-react'
import { cn } from '../lib/utils'
import { getToken } from '../lib/api'
import type { FormField } from '../types'
import { Badge } from './common'
import { MarkdownView } from './markdown'

export type SchemaValues = Record<string, unknown>
type UploadedFileRef = { id: string; name: string }
type UploadValue = string | UploadedFileRef

function asUploadedFileRef(value: UploadValue): UploadedFileRef {
  return typeof value === 'string' ? { id: value, name: value } : value
}

/* 字段类型 → 中文标签 */
export const fieldTypeLabel: Record<string, string> = {
  input: '单行文本', textarea: '多行文本', select: '下拉单选', multiselect: '下拉多选',
  radio: '单选按钮', date: '日期', number: '数字', upload: '文件上传', file: '附件多选',
}

/* 必填校验：返回缺失字段的 key 列表 */
export function validateSchema(fields: FormField[], values: SchemaValues): string[] {
  const missing: string[] = []
  for (const f of fields) {
    if (!f.required) continue
    const v = values[f.key]
    const empty = v === undefined || v === null || v === '' ||
      (Array.isArray(v) && v.length === 0)
    if (empty) missing.push(f.label)
  }
  return missing
}

/* 多行文本保留原始 Markdown 输入，并在下方稳定预览。
   原生 textarea 不会因富文本编辑器的文档序列化而重置光标或改写采纳内容。 */
function MarkdownTextarea({ value, onChange, placeholder, error }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  error?: boolean
}) {
  const previewValue = useDeferredValue(value)

  return (
    <div className="space-y-2">
      <textarea
        className={cn(
          'min-h-32 w-full resize-y rounded-lg border bg-white px-3 py-2.5 font-mono text-[13px] leading-6 outline-none transition-all dark:bg-slate-900 dark:text-slate-200',
          error
            ? 'border-red-400 focus:border-red-500 focus:ring-[3px] focus:ring-red-500/10'
            : 'border-slate-300 focus:border-blue-500 focus:ring-[3px] focus:ring-blue-500/10 dark:border-slate-700',
        )}
        value={value}
        placeholder={placeholder ?? '支持 Markdown：# 标题、**加粗**、- 列表、| 表格 |'}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
      />
      {previewValue.trim() && (
        <details className="min-w-0 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/40">
          <summary className="cursor-pointer text-[10.5px] font-medium text-slate-400">内容预览（展开查看）</summary>
          <div className="mt-2 h-80 min-h-48 max-h-[70vh] resize-y overflow-auto pr-1" title="可拖动右下角调整预览高度">
            <MarkdownView text={previewValue} className="min-w-0 max-w-full break-words text-[12.5px] leading-relaxed" />
          </div>
        </details>
      )}
    </div>
  )
}

/* 单字段控件 */
function FieldControl({ field, value, onChange, error, workItemId, project, onPreviewDocument }: {
  field: FormField
  value: unknown
  onChange: (v: unknown) => void
  error?: boolean
  workItemId?: string
  project?: string
  onPreviewDocument?: (document: UploadedFileRef) => void
}) {
  const base = cn(
    'w-full rounded-lg border bg-white text-sm outline-none transition-all',
    error
      ? 'border-red-400 focus:border-red-500 focus:ring-[3px] focus:ring-red-500/10'
      : 'border-slate-300 focus:border-blue-500 focus:ring-[3px] focus:ring-blue-500/10',
    'dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200',
  )
  const str = typeof value === 'string' ? value : value == null ? '' : String(value)
  const arr = (Array.isArray(value) ? value : []) as UploadValue[]

  switch (field.type) {
    case 'textarea':
      return (
        <MarkdownTextarea
          value={str}
          onChange={onChange}
          placeholder={field.placeholder}
          error={error}
        />
      )
    case 'number':
      return (
        <input
          type="number"
          className={cn(base, 'h-9 px-3')}
          placeholder={field.placeholder}
          value={str}
          onChange={(e) => onChange(e.target.value)}
        />
      )
    case 'date':
      return (
        <div className="relative">
          <input
            type="date"
            className={cn(base, 'h-9 px-3 pr-9')}
            value={str}
            onChange={(e) => onChange(e.target.value)}
          />
          <Calendar className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        </div>
      )
    case 'select':
      return (
        <select
          className={cn(base, 'h-9 px-3')}
          value={str}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{field.placeholder ?? '请选择…'}</option>
          {(field.options ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      )
    case 'multiselect':
      return (
        <div className={cn(base, 'space-y-1.5 p-3')}>
          {(field.options ?? []).map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2.5 text-[13px] text-slate-600 dark:text-slate-300">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-slate-300 accent-blue-600"
                checked={arr.includes(o.value)}
                onChange={(e) => onChange(e.target.checked ? [...arr, o.value] : arr.filter((v) => v !== o.value))}
              />
              {o.label}
            </label>
          ))}
        </div>
      )
    case 'radio':
      return (
        <div className={cn(base, 'space-y-1.5 p-3')}>
          {(field.options ?? []).map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2.5 text-[13px] text-slate-600 dark:text-slate-300">
              <input
                type="radio"
                name={`radio-${field.key}`}
                className="h-4 w-4 accent-blue-600"
                checked={str === o.value}
                onChange={() => onChange(o.value)}
              />
              {o.label}
            </label>
          ))}
        </div>
      )
    case 'upload':
    case 'file': {
      const multiple = field.type === 'file'
      return <UploadWidget value={arr} multiple={multiple} onChange={onChange} error={error} workItemId={workItemId} project={project} onPreviewDocument={onPreviewDocument} />
    }
    default:
      return (
        <input
          type="text"
          className={cn(base, 'h-9 px-3')}
          placeholder={field.placeholder}
          value={str}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

/* 上传/附件控件：真实文件选择 → 上传后端 → 记录文档 ID + 名称。 */
function UploadWidget({ value, multiple, onChange, error, workItemId, project, onPreviewDocument }: {
  value: UploadValue[]
  multiple: boolean
  onChange: (v: unknown) => void
  error?: boolean
  workItemId?: string
  project?: string
  onPreviewDocument?: (document: UploadedFileRef) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [errMsg, setErrMsg] = useState('')

  const uploadFiles = async (files: File[]) => {
    if (!files.length || busy) return
    setBusy(true)
    setErrMsg('')
    try {
      const refs: UploadedFileRef[] = []
      for (const f of files) {
        const fd = new FormData()
        fd.append('file', f)
        fd.append('kind', '节点表单附件')
        if (workItemId) fd.append('wi', workItemId)
        if (project) fd.append('project', project)
        const token = getToken()
        const r = await fetch('/api/v1/documents/upload', {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: fd,
        })
        const j = await r.json().catch(() => ({})
        ) as { code?: number; message?: string; data?: { doc?: { id: string; name: string } } }
        if (!r.ok || j.code !== 0) throw new Error(`${f.name}：${j.message || `HTTP ${r.status}`}`)
        if (!j.data?.doc) throw new Error(`${f.name}：响应缺少文档信息`)
        refs.push({ id: j.data.doc.id, name: j.data.doc.name })
      }
      const existing = value.map(asUploadedFileRef)
      const next = multiple ? [...new Map([...existing, ...refs].map((item) => [item.id, item])).values()] : refs.slice(0, 1)
      onChange(next)
    } catch (e) {
      setErrMsg(e instanceof Error ? e.message : '上传失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <input
        ref={fileRef} type="file" className="hidden" multiple={multiple}
        onChange={(e) => {
          const fs = e.target.files ? Array.from(e.target.files) : []
          uploadFiles(fs)
          e.target.value = ''
        }}
      />
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); uploadFiles(Array.from(e.dataTransfer.files)) }}
        onClick={() => { if (!busy) fileRef.current?.click() }}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-3 py-5 text-center transition-colors',
          dragOver ? 'border-blue-500 bg-blue-50 dark:bg-blue-500/10' : 'border-slate-300 dark:border-slate-700',
          error && 'border-red-400',
        )}
      >
        <span className={cn('flex h-8 w-8 items-center justify-center rounded-full', error ? 'bg-red-50 text-red-500' : 'bg-blue-50 text-blue-600 dark:bg-blue-500/15 dark:text-blue-400')}>
          {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : multiple ? <Paperclip className="h-4 w-4" /> : <Upload className="h-4 w-4" />}
        </span>
        <span className="text-[12px] font-medium text-slate-500 dark:text-slate-400">
          {busy ? '上传中…' : `点击或拖拽${multiple ? '选择附件' : '上传文件'}`}
        </span>
        <span className="text-[10.5px] text-slate-400">扩展名 / MIME / 大小校验后入库（上限 50MB）</span>
      </div>
      {errMsg && (
        <p className="mt-1.5 flex items-center gap-1 text-[11.5px] text-red-500">
          <AlertCircle className="h-3.5 w-3.5" />{errMsg}
        </p>
      )}
      {value.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {value.map((item) => {
            const ref = asUploadedFileRef(item)
            return <span key={ref.id} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              <button type="button" onClick={(event) => { event.stopPropagation(); onPreviewDocument?.(ref) }}
                className="max-w-44 truncate hover:text-blue-600 hover:underline" title={`预览 ${ref.name}`}>{ref.name}</button>
              <button type="button" onClick={(event) => { event.stopPropagation(); onChange(value.filter((entry) => (typeof entry === 'string' ? entry : entry.id) !== ref.id)) }}
                className="text-slate-400 hover:text-red-500" aria-label={`移除 ${ref.name}`}>×</button>
            </span>
          })}
        </div>
      )}
    </div>
  )
}

/* SchemaForm：按字段渲染真实表单 + 必填校验 */
export function SchemaForm({ fields, values, onChange, compact, workItemId, project, onPreviewDocument }: {
  fields: FormField[]
  values: SchemaValues
  onChange: Dispatch<SetStateAction<SchemaValues>>
  compact?: boolean
  workItemId?: string
  project?: string
  onPreviewDocument?: (document: UploadedFileRef) => void
}) {
  const [tried, setTried] = useState(false)
  const missing = validateSchema(fields, values)
  const set = (key: string, v: unknown) => onChange((current) => ({ ...current, [key]: v }))

  return (
    <div className="space-y-3.5">
      {fields.map((f) => {
        const error = tried && f.required && missing.includes(f.label)
        return (
          <div key={f.key} className={compact ? '' : 'space-y-1.5'}>
            <label className="flex items-center gap-1.5 text-[13px] font-medium text-slate-600 dark:text-slate-300">
              {f.label}
              {f.required && <span className="text-red-500">*</span>}
              <span className="hidden text-[10.5px] font-normal text-slate-300 sm:inline dark:text-slate-600">
                {fieldTypeLabel[f.type]}
              </span>
            </label>
            <FieldControl field={f} value={values[f.key]} onChange={(v) => set(f.key, v)} error={error} workItemId={workItemId} project={project} onPreviewDocument={onPreviewDocument} />
            {f.hint && <p className="text-[11px] text-slate-400">{f.hint}</p>}
            {error && (
              <p className="flex items-center gap-1 text-[11.5px] text-red-500">
                <AlertCircle className="h-3 w-3" />「{f.label}」为必填项
              </p>
            )}
          </div>
        )
      })}
      {fields.length === 0 && (
        <div className="rounded-lg border border-dashed border-slate-300 p-4 text-center text-[12px] text-slate-400 dark:border-slate-700">
          该模板起始节点未配置表单字段
        </div>
      )}
      {tried && missing.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-2.5 text-[12px] text-red-600 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-400">
          还有 {missing.length} 个必填项未填写：{missing.join('、')}
        </div>
      )}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setTried(true)}
          className="sr-only"
        />
      </div>
    </div>
  )
}

/* Schema 字段摘要（属性面板展示用） */
export function SchemaSummary({ fields }: { fields: FormField[] }) {
  return (
    <div className="space-y-1">
      {fields.map((f) => (
        <div key={f.key} className="flex items-center gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-[12px] dark:bg-slate-800/60">
          <span className={cn('min-w-0 flex-1 truncate font-medium', f.required ? 'text-slate-700 dark:text-slate-200' : 'text-slate-500 dark:text-slate-400')}>
            {f.label}
            {f.required && <span className="text-red-500"> *</span>}
          </span>
          <Badge tone={f.required ? 'err' : 'gry'} className="!px-1.5 !text-[10px]">
            {f.required ? '必填' : '选填'}
          </Badge>
          <span className="flex-none text-[10.5px] text-slate-400">{fieldTypeLabel[f.type]}</span>
        </div>
      ))}
      {fields.length === 0 && <div className="text-[11.5px] text-slate-400">未配置表单字段</div>}
    </div>
  )
}
