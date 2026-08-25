/* ============ API Client：封装 fetch + token + 统一响应 {code,message,data} ============ */

const TOKEN_KEY = 'flowhub_token'
const USER_KEY = 'flowhub_user'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function getStoredUser(): Record<string, unknown> | null {
  const raw = localStorage.getItem(USER_KEY)
  return raw ? JSON.parse(raw) : null
}

export function setStoredUser(user: unknown) {
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
  else localStorage.removeItem(USER_KEY)
}

/** 认证缓存 key 全集（新增认证相关缓存时在此登记，保证退出登录可完整清理） */
const AUTH_CACHE_KEYS = [TOKEN_KEY, USER_KEY]

/** 清理本站点内以该前缀命名的历史残留缓存（防御性：兼容早期版本遗留 key） */
function clearPrefixed(storage: Storage, prefix: string) {
  const stale: string[] = []
  for (let i = 0; i < storage.length; i++) {
    const k = storage.key(i)
    if (k && k.startsWith(prefix)) stale.push(k)
  }
  stale.forEach((k) => storage.removeItem(k))
}

/**
 * 退出登录：清理全部认证相关缓存（localStorage + sessionStorage）。
 * - 显式删除已登记的 key（flowhub_token / flowhub_user）
 * - 防御性清理 flowhub_ 前缀历史残留
 * - 同步清空会话级缓存，避免会话失效数据残留
 */
export function clearAuthCache() {
  AUTH_CACHE_KEYS.forEach((k) => localStorage.removeItem(k))
  clearPrefixed(localStorage, 'flowhub_')
  clearPrefixed(sessionStorage, 'flowhub_')
}

export class ApiError extends Error {
  code: number
  status: number

  constructor(code: number, message: string, status = 400) {
    super(message)
    this.code = code
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const resp = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  let payload: { code: number; message: string; data: T }
  try {
    payload = await resp.json()
  } catch {
    throw new ApiError(resp.status, `服务响应异常（HTTP ${resp.status}）`, resp.status)
  }

  if (!resp.ok || payload.code !== 0) {
    // 401 清 token（会话失效）
    if (resp.status === 401 || payload.code === 40101) setToken(null)
    throw new ApiError(payload.code ?? resp.status, payload.message || '请求失败', resp.status)
  }
  return payload.data
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}
