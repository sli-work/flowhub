import { api } from '../lib/api'
import { toast } from '../store/app-store'
import { resultDocuments } from '../lib/tool-contract.mjs'
export function ToolResultDocuments({ result }: { result: unknown }) {
  const download = async (id: string, name: string) => {
    try {
      const blob = await api.getBlob(`/api/v1/documents/${encodeURIComponent(id)}/download`)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a'); link.href = url; link.download = name; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (error) { toast.error(error instanceof Error ? error.message : '下载失败') }
  }
  return <div className="space-y-2">{resultDocuments(result).map(doc => <button key={doc.id} className="block text-left text-xs text-blue-600 underline" onClick={() => void download(doc.id, doc.name)}>下载归档：{doc.name}</button>)}</div>
}
