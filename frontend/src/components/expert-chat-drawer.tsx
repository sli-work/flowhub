import { nextDraft, type DraftState } from '@/lib/chat-draft.mjs'
import { useEffect, useRef, useState } from 'react'
import { CircleAlert, LoaderCircle, Send, Square, X } from 'lucide-react'
import { getToken } from '../lib/api'
import { cn } from '../lib/utils'
import { ApprovalPendingCard } from './approval-card'

interface TraceItem { kind?: 'skill' | 'mcp' | 'tool' | 'approval' | 'model' | 'quality'; tool: string; status: string; summary: string | Record<string, unknown> }

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  toolTrace?: TraceItem[]
  /** 受治理写入中断时关联的 run，用于内联审批 */
  runId?: string
}

interface AssistantPayload {
  runId?: string
  id: string
  content: string
  status?: string
  files?: unknown[]
  toolTrace?: TraceItem[]
  compacted?: boolean
}

/** 与 AiChat 主页面保持一致的治理写入推导规则 */
const deriveWriteIntent = (content: string) => /提交|创建|写入|删除|发布/.test(content)

/**
 * 编辑器「在 AiChat 中测试」抽屉：绑定当前草稿版本的多轮对话。
 * 支持带修订版本的草稿流，最终以服务端持久化消息为准。
 */
export function ExpertChatDrawer({ sessionId, sessionTitle, subtitle, modelLabel, onClose, onTested }: { sessionId: string | null; sessionTitle: string; subtitle?: string; modelLabel?: string; onClose: () => void; onTested?: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [streamingTraces, setStreamingTraces] = useState<TraceItem[]>([])
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const testedRef = useRef(false)
  const callbacksRef = useRef({ onTested })
  callbacksRef.current = { onTested }

  useEffect(() => {
    // 切换/新开会话时复位线程状态；测试标记仅在首个成功回复后触发一次
    setMessages([]); setInput(''); setBusy(false); setStreamText(''); setStreamingTraces([]); setNotice(null)
    testedRef.current = false
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages.length, streamText, notice])

  if (!sessionId) return null

  const absorb = (payload: AssistantPayload, toolTrace: TraceItem[], runId?: string) => {
    const message: ChatMessage = { id: payload.id, role: 'assistant', content: payload.content || '（无文本输出）', status: payload.status, toolTrace: payload.toolTrace?.length ? payload.toolTrace : toolTrace, runId: runId ?? payload.runId }
    setMessages((current) => [...current, message])
    setStreamText('')
    setStreamingTraces([])
    if (!testedRef.current) {
      testedRef.current = true
      callbacksRef.current.onTested?.()
    }
  }

  const send = async () => {
    const content = input.trim()
    if (!content || busy) return
    setMessages((current) => [...current, { id: `u-${Date.now()}`, role: 'user', content }])
    setInput(''); setStreamText(''); setStreamingTraces([]); setNotice(null); setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller
    // 跨分支累积：SSE 与 JSON 路径、以及 abort 时都能拿到当前已生成内容
    let accumulated = ''
    let draft: DraftState | undefined
    let accumulatedTraces: TraceItem[] = []
    try {
      const response = await fetch(`/api/v1/expert-chat/sessions/${sessionId}/messages/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
        body: JSON.stringify({ content, write_intent: deriveWriteIntent(content) }),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error(`请求失败（HTTP ${response.status}）`)
      if (!response.headers.get('content-type')?.startsWith('text/event-stream')) {
        // 版本/Deployment 绑定会话：服务端返回整段 JSON 结果（ok 包络），这是正确语义而非降级错误
        const payload = await response.json() as { code?: number; message?: string; data?: { assistantMessage?: AssistantPayload; run?: { id: string; status: string } } }
        if (payload.code !== 0 || !payload.data?.assistantMessage) throw new Error(payload.message || '消息处理失败')
        absorb(payload.data.assistantMessage, [], payload.data.run?.id)
      } else {
        const reader = response.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let finalMessage: AssistantPayload | null = null
        let finished = false
        try {
          while (true) {
            const chunk = await reader.read()
            buffer += decoder.decode(chunk.value ?? new Uint8Array(), { stream: !chunk.done })
            const blocks = buffer.split('\n\n')
            buffer = blocks.pop() ?? ''
            for (const block of blocks) {
              const lines = block.split('\n')
              const event = lines.find((line) => line.startsWith('event: '))?.slice(7)
              const raw = lines.find((line) => line.startsWith('data: '))?.slice(6)
              if (!event || !raw) continue
              const data = JSON.parse(raw) as TraceItem & { text?: string; attemptId?: string | number; message?: AssistantPayload }
              if (event === 'trace') {
                accumulatedTraces = [...accumulatedTraces.filter((item) => item.tool !== data.tool), { tool: data.tool, status: data.status, summary: data.summary }]
                setStreamingTraces(accumulatedTraces)
              }
              if (event === 'error') throw new Error(typeof data.message === 'string' ? data.message : '生成失败')
              if (event === 'draft_start' || event === 'token') {
                draft = nextDraft(draft, event, data)
                accumulated = draft.text
                setStreamText(accumulated)
              }
              if (event === 'done' && data.message) {
                finalMessage = data.message
                finished = true
              }
            }
            if (finished || chunk.done) break
          }
        } finally {
          await reader.cancel().catch(() => {})
        }
        if (!finalMessage) throw new Error('连接中断，回答尚未完成校验，请刷新查看状态')
        absorb(finalMessage, accumulatedTraces)
      }
    } catch (error) {
      if (controller.signal.aborted) {
        // 用户主动停止：保留已生成内容并落为消息，便于继续追问
        if (accumulated.trim()) absorb({ id: `a-${Date.now()}`, content: accumulated }, accumulatedTraces)
        setNotice({ ok: true, text: '已停止生成。当前部分内容尚未校验。' })
      } else {
        setNotice({ ok: false, text: error instanceof Error ? error.message : '消息发送失败' })
      }
    } finally {
      setBusy(false)
      setStreamText('')
      setStreamingTraces([])
      abortRef.current = null
    }
  }

  const renderedTraces = (message: ChatMessage) => (
    <div className="mt-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[10.5px] leading-relaxed text-slate-500 dark:bg-slate-800/70 dark:text-slate-400">
      {(message.toolTrace ?? []).map((trace, index) => (
        <div key={`${trace.tool}-${index}`} className="truncate"><b>{trace.tool}</b> · {trace.status}{typeof trace.summary === 'string' ? ` · ${trace.summary}` : ''}</div>
      ))}
    </div>
  )

  return (
    <div className="fixed inset-0 z-[70] flex justify-end" role="dialog" aria-modal="true" aria-label={sessionTitle}>
      <button className="absolute inset-0 cursor-default bg-slate-950/30 backdrop-blur-[1px]" aria-label="关闭测试对话" onClick={onClose} />
      <aside className="relative flex h-full w-full max-w-[560px] flex-col border-l border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-950">
        <header className="flex items-start gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-violet-50 text-violet-600 dark:bg-violet-500/15"><Send className="h-4 w-4" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[15px] font-semibold text-slate-900 dark:text-slate-100">{sessionTitle}</h2>
            <p className="mt-0.5 text-[11px] text-slate-400">{subtitle ?? '与当前保存的配置多轮对话；首轮回复完成后即视为通过测试'}</p>
            {modelLabel && <p className="mt-1 text-[10.5px] font-medium text-violet-600 dark:text-violet-300">测试模型 · {modelLabel}</p>}
          </div>
          <button className="flex h-8 w-8 flex-none items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200" onClick={onClose} aria-label="关闭测试对话"><X className="h-4 w-4" /></button>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {!messages.length && !busy && <p className="mt-10 rounded-xl border border-dashed border-slate-200 p-5 text-center text-[12.5px] text-slate-400 dark:border-slate-700">输入第一条测试消息开始对话，例如让该 Expert 处理一个真实场景任务。</p>}
          {messages.map((message) => message.role === 'user' ? (
            <div key={message.id} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-xl bg-blue-600 px-3.5 py-2.5 text-[13px] leading-relaxed text-white">{message.content}</div>
            </div>
          ) : (
            <div key={message.id} className="max-w-[88%] rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 dark:border-slate-700 dark:bg-slate-900">
              <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-700 dark:text-slate-200">{message.content}</p>
              {!!message.toolTrace?.length && renderedTraces(message)}
              {message.runId && message.status === 'interrupted' && <ApprovalPendingCard runId={message.runId} />}
            </div>
          ))}
          {streamText && (
            <div className="max-w-[88%] rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 dark:border-slate-700 dark:bg-slate-900">
              <p className="text-xs text-amber-600">待校验草稿</p>
              <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-slate-700 dark:text-slate-200">{streamText}</p>
              {!!streamingTraces.length && renderedTraces({ id: 'streaming', role: 'assistant', content: '', toolTrace: streamingTraces })}
            </div>
          )}
          {busy && !streamText && (
            <div className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[11.5px] text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />正在读取上下文、调用工具并生成待校验草稿…
            </div>
          )}
          {notice && (
            <div className={cn('flex items-start gap-2 rounded-lg border px-3 py-2 text-[11.5px]', notice.ok ? 'border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-900' : 'border-red-200 bg-red-50 text-red-600 dark:border-red-500/30 dark:bg-red-500/10')}>
              {!notice.ok && <CircleAlert className="mt-0.5 h-3.5 w-3.5 flex-none" />}<span>{notice.text}</span>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-slate-200 p-4 dark:border-slate-800">
          <div className="flex items-end gap-2 rounded-xl border border-slate-300 p-2 focus-within:border-blue-500 dark:border-slate-700">
            <textarea value={input} rows={2} disabled={busy} placeholder="输入测试消息，Enter 发送（Shift+Enter 换行）" className="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-[13px] outline-none disabled:opacity-60 dark:placeholder:text-slate-500 dark:text-slate-200" onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} />
            {busy ? (
              <button className="flex h-9 flex-none items-center gap-1 rounded-lg border border-red-200 px-3 text-xs font-medium text-red-600 hover:bg-red-50 dark:border-red-500/30 dark:text-red-400" onClick={() => abortRef.current?.abort()}><Square className="h-3 w-3 fill-current" />停止</button>
            ) : (
              <button className="flex h-9 flex-none items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white disabled:opacity-40" disabled={!input.trim()} onClick={() => void send()}><Send className="h-3.5 w-3.5" />发送</button>
            )}
          </div>
        </div>
      </aside>
    </div>
  )
}
