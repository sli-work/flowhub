import { useEffect, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { cn } from '../lib/utils'
import { api, ApiError, setStoredUser, setToken } from '../lib/api'

interface LoginResp {
  token: string
  user: { id: string; name: string; account: string; dept: string; roles: string[]; mustChangePassword?: boolean }
}

export function LoginPage() {
  const { login, openDialog } = useApp()
  const [tab, setTab] = useState<'sso' | 'local'>('sso')
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const afterLogin = (data: LoginResp, via: string) => {
    setToken(data.token)
    setStoredUser(data.user)
    toast.success(`${via}：${data.user.name}（${data.user.account}）`)
    // 先进入工作台，再按后端 must_change_password 标记决定是否强制改密（避免每次登录都弹）
    login()
    if (data.user.mustChangePassword) {
      openDialog('changePwd')
    }
  }

  const localLogin = async () => {
    if (busy) return
    setBusy(true)
    try {
      const data = await api.post<LoginResp>('/api/v1/auth/login', { account, password })
      afterLogin(data, '本地账号登录成功')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '登录失败，请检查后端服务')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    const query = new URLSearchParams(window.location.search)
    const provider = query.get('provider')
    const code = query.get('code')
    const state = query.get('state')
    if (!provider || !code || !state || (provider !== 'dingtalk' && provider !== 'wecom')) return
    setBusy(true)
    api.post<LoginResp>('/api/v1/auth/sso/verify', { provider, code, state })
      .then((data) => afterLogin(data, provider === 'dingtalk' ? '钉钉免登成功' : '企业微信免登成功'))
      .catch((e) => toast.error(e instanceof ApiError ? e.message : '企业免登失败'))
      .finally(() => { window.history.replaceState({}, '', window.location.pathname); setBusy(false) })
  }, [])

  const ssoLogin = async (provider: 'dingtalk' | 'wecom') => {
    if (busy) return
    setBusy(true)
    try {
      const data = await api.get<{ authorization_url: string }>(`/api/v1/auth/sso/${provider}/start?return_to=${encodeURIComponent('/')}`)
      window.location.assign(data.authorization_url)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '免登失败，请检查后端服务')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* 左半区：品牌与价值主张 */}
      <div className="relative hidden flex-1 flex-col overflow-hidden bg-slate-900 p-10 text-white lg:flex">
        <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-blue-600/30 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 -left-24 h-80 w-80 rounded-full bg-violet-600/20 blur-3xl" />
        <div className="relative flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 7h10a4 4 0 0 1 0 8H9" /><path d="m6 5-2 2 2 2" /><path d="M20 17H10a4 4 0 0 1 0-8h5" /><path d="m18 15 2-2-2-2" />
            </svg>
          </span>
          <span className="text-lg font-semibold">FlowHub <span className="font-normal text-slate-400">流枢</span></span>
        </div>
        <div className="relative mt-auto mb-16">
          <h1 className="text-[32px] font-semibold leading-snug">让每个流程节点<br />可运行、可追溯、可审计</h1>
          <p className="mt-4 max-w-md text-[14px] leading-relaxed text-slate-400">
            面向企业软件交付与售后支持的可审计流程协同平台。需求、问题、任务、文档与 AI Agent 在统一流程中流转，钉钉 / 企业微信免登接入。
          </p>
          <div className="mt-7 space-y-3">
            {[
              '流程模板可视化编排，发布前静态校验拦截非法流程',
              '每次状态变更可追溯到用户、Agent、授权、时间与请求',
              'AI Agent 在节点能力边界内参与，高风险操作必须用户确认',
              '企业文件内网 MinIO 存储，外部仅访问短时效授权链接',
            ].map((t) => (
              <div key={t} className="flex items-start gap-2.5 text-[13.5px] text-slate-300">
                <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-blue-500/20 text-blue-300">
                  <Check className="h-3 w-3" />
                </span>
                {t}
              </div>
            ))}
          </div>
        </div>
        <div className="relative flex gap-6 text-[12px] text-slate-500">
          <span>私有化部署 · 单实例单组织</span>
          <span>基线版本 v2.2</span>
          <span>© 2026 FlowHub</span>
        </div>
      </div>

      {/* 右半区：登录卡片 */}
      <div className="flex flex-1 items-center justify-center bg-slate-50 p-6 dark:bg-slate-950">
        <div className="w-full max-w-[400px]">
          <div className="mb-6 flex items-center gap-2.5 lg:hidden">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 7h10a4 4 0 0 1 0 8H9" /><path d="m6 5-2 2 2 2" /><path d="M20 17H10a4 4 0 0 1 0-8h5" /><path d="m18 15 2-2-2-2" />
              </svg>
            </span>
            <span className="text-lg font-semibold text-slate-900 dark:text-slate-100">FlowHub 流枢</span>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-7 shadow-m dark:border-slate-700 dark:bg-slate-900">
            <h2 className="text-xl font-semibold text-slate-900 dark:text-slate-100">欢迎回来</h2>
            <p className="mt-1 text-[12.5px] text-slate-400">免登或本地账号登录（本地账号：邮箱+密码自注册，管理员审批激活）</p>

            <div className="mt-5 grid grid-cols-2 gap-2 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
              {(['sso', 'local'] as const).map((t) => (
                <button key={t} onClick={() => setTab(t)}
                  className={cn('rounded-md py-1.5 text-[13px] font-medium transition-all',
                    tab === t ? 'bg-white text-slate-800 shadow-sm dark:bg-slate-700 dark:text-slate-100' : 'text-slate-500')}>
                  {t === 'sso' ? '企业免登' : '本地账号'}
                </button>
              ))}
            </div>

            {tab === 'sso' ? (
              <div className="mt-5 space-y-2.5">
                <button onClick={() => ssoLogin('dingtalk')} disabled={busy} className="flex h-11 w-full items-center justify-center gap-2.5 rounded-lg border border-slate-200 bg-white text-[14px] font-medium text-slate-700 transition-all hover:border-blue-400 hover:text-blue-600 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200">
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : (
                    <svg width="18" height="18" viewBox="0 0 24 24"><path fill="#0089FF" d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.6 0 12 0zm5.6 9.2-5.4 7.9c-.3.4-.9.5-1.3.2l-.3-.3-1-2.2c-.1-.3-.2-.7-.5-1l-.2-.2c-.3-.3-.8-.4-1.2-.3l-1.7.3c-.5.1-.9-.4-.7-.9l1.4-4.3c.1-.4.1-.8 0-1.2l-.1-.3c-.1-.5.3-1 .8-1h2.6c.2 0 .4 0 .5.1l.2.1c.4.2.7.6.9 1.1l1.9 4.1c.3.7 1.1 1 1.8.7.4-.2.6-.5.8-.9l.1-.2c.2-.4.2-.9-.1-1.3L12.7 4c-.2-.3-.4-.5-.6-.7-.5-.5-1.3-.6-2-.3l-1.4.6c-.6.3-1 .8-1.2 1.4l-.1.3c-.2.7-.1 1.5.3 2.1.3.4.7.8 1.1 1.1l.2.2c.3.3.5.7.7 1.1l.4.9c.1.2.2.4.4.5.2.3.5.4.8.4.3 0 .5-.1.7-.4l.4-.5c.2-.3.2-.6.1-.9l-.4-.9c-.1-.2-.3-.4-.5-.6l-.3-.3c-.1-.1-.2-.2-.2-.3-.1-.2-.1-.4 0-.6l.1-.2c.1-.3.3-.5.6-.6l.9-.3c.3-.1.6-.1.9 0l.3.1c.3.1.6.4.8.7l.1.2c.1.2.1.4 0 .6l-.6 1.6c-.1.3-.1.6 0 .9l.1.2c.1.4.5.6.9.5.2 0 .4-.1.5-.3l1.6-2.3c.2-.3.3-.7.2-1.1-.1-.4-.3-.8-.7-1z" /></svg>
                  )}
                  钉钉扫码免登
                </button>
                <button onClick={() => ssoLogin('wecom')} disabled={busy} className="flex h-11 w-full items-center justify-center gap-2.5 rounded-lg border border-slate-200 bg-white text-[14px] font-medium text-slate-700 transition-all hover:border-emerald-400 hover:text-emerald-600 disabled:opacity-60 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-200">
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : (
                    <svg width="18" height="18" viewBox="0 0 24 24"><path fill="#07C160" d="M10.6 8.9c-1.4 0-2.7.4-3.7 1.1l-.4-1.4c-.1-.3.2-.6.5-.5l.4.1.4.1c.4.1.8-.1.9-.5l.5-1.6c.1-.4-.2-.8-.6-.8l-.5-.1-.9-.3c-.6-.2-1.2-.2-1.8 0l-.9.3c-.4.1-.6.5-.6.9l.1.3c.2.7.1 1.5-.3 2.1-.4.6-1 .9-1.7.9l-.5-.1-.4-.1c-.3-.1-.6.2-.5.5l.4 1.4c.3 1 .9 1.9 1.8 2.5l.4.2c.3.2.5.5.5.9l-.1.5c-.1.4.1.8.5 1l.5.2c1.6.6 3.4.6 5-.1l-.5-1.6c-.3-.8.1-1.7.9-2l.4-.1c.3-.1.4-.5.3-.8-.4-1.1-.8-1.7-1.4-2.3zm-1.3 2.5c-.4 0-.7-.3-.7-.7s.3-.7.7-.7.7.3.7.7-.3.7-.7.7zm2.3-1.8c-.4 0-.7-.3-.7-.7s.3-.7.7-.7.7.3.7.7-.3.7-.7.7zm4.8-4.8c-3.1 0-5.6 2.4-5.8 5.4l.1-.1c1.4-.1 2.8.3 3.9 1.2.1.1.2.2.4.3l.4.3.4.2c1.4.8 2.5 2.1 3 3.7l.3 1c.2.6-.3 1.2-.9 1.2h-.4l.3-1c.1-.3-.1-.6-.4-.7l-.3-.1c-.3-.1-.6.1-.6.4l-.1.3c-.1.3-.2.5-.4.8l-.3.4c-.1.1-.2.3-.4.4-.2.2-.4.4-.7.5l-.5.3c-.3.2-.4.5-.3.8l.1.3c.1.3.4.5.7.5l.4-.1.6-.2c.7-.3 1.4-.7 1.9-1.3l.2-.2.2-.3.2-.2.1-.1.1-.1.1-.1.1-.1.1-.2.1-.2.2-.4.1-.3c.1-.4.3-.7.4-1.1l.3-.9.2-.7.2-.6c.3-.9.4-1.8.4-2.7l-.1-.6c-.2-3.2-2.9-5.8-6.3-5.8zm3.9 6.1c-.4 0-.7-.3-.7-.7s.3-.7.7-.7.7.3.7.7-.3.7-.7.7zm-1.4-1.5c-.4 0-.7-.3-.7-.7s.3-.7.7-.7.7.3.7.7-.3.7-.7.7z" /></svg>
                  )}
                  企业微信扫码免登
                </button>
              </div>
            ) : (
              <div className="mt-5 space-y-3">
                <div className="space-y-1.5">
                  <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">用户名 / 邮箱</label>
                  <Input value={account} onChange={(e) => setAccount(e.target.value)} placeholder="本地账号：用户名或邮箱" />
                </div>
                <div className="space-y-1.5">
                  <label className="text-[13px] font-medium text-slate-600 dark:text-slate-300">密码</label>
                  <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="请输入密码" />
                </div>
                <Button className="h-11 w-full" onClick={localLogin} disabled={busy}>
                  {busy ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}登 录
                </Button>
              </div>
            )}

            <div className="mt-4 flex items-center justify-between">
              <button className="text-[13px] font-medium text-blue-600 hover:underline" onClick={() => openDialog('registerAccount')}>注册本地账号</button>
              <span className="text-[11.5px] text-slate-400">忘记密码？请联系组织管理员重置</span>
            </div>
            <p className="mt-4 border-t border-slate-100 pt-3 text-[11.5px] leading-relaxed text-slate-400 dark:border-slate-800">
              三种身份来源：钉钉免登 / 企业微信免登 / 本地账号（自注册+审批）<br />
              本地账号首登（管理员创建或重置密码）必须强制改密 · 未绑定外部身份时通知走邮件+站内
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
