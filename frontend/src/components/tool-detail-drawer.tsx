import type { ReactNode } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'

export function ToolDetailDrawer({ open, title, eyebrow, onClose, children }: { open: boolean; title: string; eyebrow?: string; onClose: () => void; children: ReactNode }) {
  return <Dialog.Root open={open} onOpenChange={value => { if (!value) onClose() }}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[90] bg-slate-950/30 backdrop-blur-[1px]" /><Dialog.Content className="fixed inset-y-0 right-0 z-[91] w-full max-w-[560px] overflow-y-auto border-l bg-white p-5 shadow-2xl dark:border-slate-700 dark:bg-slate-950"><header className="mb-5 border-b pb-4 pr-8"><Dialog.Description className="text-xs text-blue-600">{eyebrow ?? '工具详情'}</Dialog.Description><Dialog.Title className="mt-1 text-lg font-semibold">{title}</Dialog.Title><Dialog.Close aria-label="关闭工具详情" className="absolute right-4 top-4 rounded p-2 hover:bg-slate-100 dark:hover:bg-slate-800"><X className="h-4 w-4" /></Dialog.Close></header>{children}</Dialog.Content></Dialog.Portal></Dialog.Root>
}
