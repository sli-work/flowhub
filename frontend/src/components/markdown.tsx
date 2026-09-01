import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '../lib/utils'

/**
 * 通用 Markdown 渲染：节点表单 textarea 预览 / 任务书展示等复用。
 * 样式复用 index.css 的 .aui-markdown（标题/列表/代码/表格等排版规则）。
 */
export function MarkdownView({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn('aui-markdown', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}
