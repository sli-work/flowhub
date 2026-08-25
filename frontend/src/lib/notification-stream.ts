import { getToken } from './api'
import type { NotificationItem } from '../types'

const EVENT_NAME = 'flowhub:notification'

function emitNotification(notification: NotificationItem) {
  window.dispatchEvent(new CustomEvent<NotificationItem>(EVENT_NAME, { detail: notification }))
}

export function subscribeToNotifications(): () => void {
  let cancelled = false
  let abortController: AbortController | null = null
  let reconnectTimer: number | null = null

  const connect = async () => {
    const token = getToken()
    if (!token || cancelled) return
    abortController = new AbortController()
    try {
      const response = await fetch('/api/v1/notifications/stream', {
        headers: { Authorization: `Bearer ${token}` },
        signal: abortController.signal,
      })
      if (!response.ok || !response.body) throw new Error(`通知实时连接失败（HTTP ${response.status}）`)

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!cancelled) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? ''
        events.forEach((event) => {
          const lines = event.split('\n')
          if (!lines.includes('event: notification')) return
          const data = lines.find((line) => line.startsWith('data: '))?.slice(6)
          if (!data) return
          try { emitNotification(JSON.parse(data) as NotificationItem) } catch { /* 丢弃非法推送 */ }
        })
      }
    } catch {
      // 网络短暂中断时自动重连；认证失效由常规 API 请求统一处理。
    }
    if (!cancelled) reconnectTimer = window.setTimeout(connect, 2_000)
  }

  void connect()
  return () => {
    cancelled = true
    abortController?.abort()
    if (reconnectTimer !== null) window.clearTimeout(reconnectTimer)
  }
}

export function onNotification(handler: (notification: NotificationItem) => void): () => void {
  const listener = (event: Event) => handler((event as CustomEvent<NotificationItem>).detail)
  window.addEventListener(EVENT_NAME, listener)
  return () => window.removeEventListener(EVENT_NAME, listener)
}
