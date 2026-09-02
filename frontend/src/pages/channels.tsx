import { useEffect, useState } from 'react'
import {
  Bell, Mail, MessageSquare, Send, Webhook, Copy, Check, CircleCheck, CircleX, LoaderCircle,
} from 'lucide-react'
import { toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Badge, PageHeader } from '../components/common'
import { cn } from '../lib/utils'

interface ChannelState { enabled: boolean; ok: boolean; desc: string }

/* 各渠道配置引导（.env 变量 + 示例 + 说明） */
const CHANNEL_GUIDE: Record<string, { vars: string[]; example: string; note: string }> = {
  站内: { vars: [], example: '', note: '通知中心站内消息，落库即达，无需任何配置' },
  钉钉: {
    vars: ['DINGTALK_WEBHOOK', 'DINGTALK_SECRET'],
    example: 'DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=xxx\nDINGTALK_SECRET=SECxxxxxxxx',
    note: '钉钉群 → 群机器人 → 自定义机器人，安全设置选「加签」时需配置 DINGTALK_SECRET（免加签可留空）',
  },
  企微: {
    vars: ['WECOM_WEBHOOK'],
    example: 'WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx',
    note: '企业微信群 → 群机器人 → 添加机器人，复制 webhook 地址即可',
  },
  邮件: {
    vars: ['SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM'],
    example: 'SMTP_HOST=smtp.example.com\nSMTP_PORT=465\nSMTP_USER=no-reply@example.com\nSMTP_PASSWORD=xxx\nSMTP_FROM=FlowHub <no-reply@example.com>',
    note: 'SSL 用 465，STARTTLS 用 587；SMTP_FROM 可选，默认用 SMTP_USER',
  },
}

const CHANNEL_ICON: Record<string, React.ReactNode> = {
  站内: <Bell className="h-4.5 w-4.5" />, 钉钉: <MessageSquare className="h-4.5 w-4.5" />,
  企微: <MessageSquare className="h-4.5 w-4.5" />, 邮件: <Mail className="h-4.5 w-4.5" />,
}

export function ChannelsPage() {
  const [health, setHealth] = useState<Record<string, ChannelState>>({})
  const [email, setEmail] = useState('')
  const [testResults, setTestResults] = useState<{ name: string; ok: boolean }[] | null>(null)
  const [testing, setTesting] = useState(false)
  const [copied, setCopied] = useState(false)
  const [config, setConfig] = useState<Record<string, string>>({})
  const [configured, setConfigured] = useState<Record<string, boolean>>({})
  const [saving, setSaving] = useState(false)

  const refresh = () => {
    api.get<{ channels: Record<string, ChannelState> }>('/api/v1/notifications/channels/health')
      .then((d) => setHealth(d.channels))
      .catch(() => {})
  }
  useEffect(() => {
    refresh()
    api.get<{ values: Record<string, string>; configured: Record<string, boolean> }>('/api/v1/notifications/channels/config')
      .then((d) => { setConfig(d.values); setConfigured(d.configured) }).catch(() => {})
  }, [])

  const saveConfig = async () => {
    setSaving(true)
    try {
      await api.put('/api/v1/notifications/channels/config', { values: config })
      toast.success('企业应用配置已保存'); refresh()
    } catch (e) { toast.error(e instanceof ApiError ? e.message : '保存失败') } finally { setSaving(false) }
  }

  const sendTest = async () => {
    setTesting(true)
    try {
      const d = await api.post<{ results: { name: string; ok: boolean }[] }>('/api/v1/notifications/channels/test', { email: email.trim() })
      setTestResults(d.results)
      toast.success('测试消息已发送：已投递到配置渠道，并记录到通知中心')
      refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '测试发送失败')
    } finally {
      setTesting(false)
    }
  }

  const copyEnv = async () => {
    const lines = Object.values(CHANNEL_GUIDE).flatMap((g) => g.example ? g.example.split('\n') : [])
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setCopied(true); toast.success('.env 配置示例已复制'); setTimeout(() => setCopied(false), 1500)
    } catch { toast('复制失败，请手动复制') }
  }

  return (
    <div className="page-container">
      <PageHeader
        title="通知渠道配置"
        sub="钉钉 / 企微 / 邮件 / 站内 四渠道 · 在 backend/.env 配置后即时生效（无需重启服务）· 配置完可发送测试消息验证连通性（PRD §11）"
      />

      {/* 渠道状态卡片 */}
      <div className="mb-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(health).map(([name, h]) => (
          <div key={name} className={cn('rounded-xl border bg-white p-4 shadow-s dark:bg-slate-900',
            h.enabled ? 'border-emerald-200 dark:border-emerald-500/30' : 'border-slate-200 dark:border-slate-700')}>
            <div className="flex items-center justify-between">
              <span className={cn('flex h-9 w-9 items-center justify-center rounded-lg',
                h.enabled ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400' : 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500')}>
                {CHANNEL_ICON[name]}
              </span>
              <Badge tone={h.enabled ? 'suc' : 'gry'}>{h.enabled ? '可用' : '未配置'}</Badge>
            </div>
            <div className="mt-3 text-[14px] font-semibold text-slate-800 dark:text-slate-100">{name}</div>
            <p className="mt-1 text-[11.5px] leading-relaxed text-slate-400">{h.desc}</p>
          </div>
        ))}
      </div>

      {/* 测试发送 */}
      <section className="mb-5 rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-slate-800 dark:text-slate-100">
          <Send className="h-4 w-4 text-blue-500" />连通性测试
        </div>
        <p className="mb-3 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">
          向所有已配置渠道发送一条测试消息：钉钉 / 企微机器人直接投递到群；邮件需填写收件邮箱才会验证 SMTP。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="收件邮箱（可选，验证邮件渠道）"
            className="h-9 w-64 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200" />
          <button onClick={sendTest} disabled={testing}
            className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-blue-700 disabled:opacity-60">
            {testing ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {testing ? '发送中…' : '发送测试消息'}
          </button>
        </div>
        {testResults && (
          <div className="mt-3 flex flex-wrap gap-2">
            {testResults.map((r) => (
              <span key={r.name} className={cn('inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12px] font-medium',
                r.ok ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300' : 'bg-red-50 text-red-600 dark:bg-red-500/15 dark:text-red-400')}>
                {r.ok ? <CircleCheck className="h-3.5 w-3.5" /> : <CircleX className="h-3.5 w-3.5" />}
                {r.name} {r.ok ? '投递成功' : '投递失败'}
              </span>
            ))}
            {testResults.every((r) => r.ok) && <span className="text-[12px] text-slate-400">✓ 全部渠道连通</span>}
          </div>
        )}
      </section>

      <section className="mb-5 rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-1 text-[14px] font-semibold text-slate-800 dark:text-slate-100">邮件渠道（SMTP）</div>
        <p className="mb-4 text-[12px] text-slate-500">配置后用户激活欢迎邮件、节点通知将真实发信；凭证加密保存不回显，保存后立即生效。465 端口走 SSL，其他端口走 STARTTLS。</p>
        <div className="grid gap-3 md:grid-cols-2">
          {[['smtp_host', 'SMTP 服务器', 'smtp.example.com'], ['smtp_port', 'SMTP 端口', '465'], ['smtp_user', 'SMTP 用户名', 'noreply@example.com'], ['smtp_password', configured.smtp_password ? 'SMTP 密码/授权码（已配置，留空不修改）' : 'SMTP 密码/授权码', ''], ['smtp_from', '发件人地址（可选，默认同用户名）', 'noreply@example.com']].map(([key, label, placeholder]) => (
            <label key={key} className="space-y-1 text-[12px] font-medium text-slate-600 dark:text-slate-300"><span>{label}</span><input type={key === 'smtp_password' ? 'password' : 'text'} value={config[key] ?? ''} placeholder={placeholder} onChange={(e) => setConfig({ ...config, [key]: e.target.value })} className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900" /></label>
          ))}
        </div>
        <button onClick={saveConfig} disabled={saving} className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-medium text-white disabled:opacity-60">{saving ? '保存中…' : '保存邮件配置'}</button>
      </section>

      <section className="mb-5 rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-1 text-[14px] font-semibold text-slate-800 dark:text-slate-100">企业应用与免登配置</div>
        <p className="mb-4 text-[12px] text-slate-500">凭证加密保存且不会回显；保存后立即生效。请将应用主页与 OAuth 回调配置为「公网地址/login」。</p>
        <div className="grid gap-3 md:grid-cols-2">
          {[['public_base_url', 'FlowHub 公网 HTTPS 地址', 'https://flowhub.example.com'], ['dingtalk_app_key', '钉钉 AppKey', ''], ['dingtalk_app_secret', configured.dingtalk_app_secret ? '钉钉 AppSecret（已配置，留空不修改）' : '钉钉 AppSecret', ''], ['dingtalk_agent_id', '钉钉 AgentId', ''], ['wecom_corp_id', '企业微信 CorpID', ''], ['wecom_app_secret', configured.wecom_app_secret ? '企业微信应用 Secret（已配置，留空不修改）' : '企业微信应用 Secret', ''], ['wecom_agent_id', '企业微信 AgentId', '']].map(([key, label, placeholder]) => (
            <label key={key} className="space-y-1 text-[12px] font-medium text-slate-600 dark:text-slate-300"><span>{label}</span><input type={key.includes('secret') ? 'password' : 'text'} value={config[key] ?? ''} placeholder={placeholder} onChange={(e) => setConfig({ ...config, [key]: e.target.value })} className="h-9 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-900" /></label>
          ))}
        </div>
        <button onClick={saveConfig} disabled={saving} className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-[13px] font-medium text-white disabled:opacity-60">{saving ? '保存中…' : '保存企业应用配置'}</button>
      </section>

      {/* 配置引导 */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-s dark:border-slate-700 dark:bg-slate-900">
        <div className="mb-3 flex items-center gap-2 text-[14px] font-semibold text-slate-800 dark:text-slate-100">
          <Webhook className="h-4 w-4 text-violet-500" />配置引导（backend/.env）
          <span className="text-[11.5px] font-normal text-slate-400">配置后即时生效，无需重启；未配置的渠道自动跳过</span>
        </div>
        <div className="space-y-3">
          {Object.entries(CHANNEL_GUIDE).map(([name, g]) => (
            <div key={name} className="rounded-lg border border-slate-100 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-800/40">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-semibold text-slate-700 dark:text-slate-200">{name}</span>
                {g.vars.length > 0 && <span className="font-mono text-[11px] text-slate-400">{g.vars.join('、')}</span>}
              </div>
              {g.example && (
                <pre className="mt-2 overflow-x-auto rounded-lg bg-slate-950 p-3 font-mono text-[11.5px] leading-relaxed text-emerald-300 dark:bg-slate-950">{g.example}</pre>
              )}
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-slate-500 dark:text-slate-400">{g.note}</p>
            </div>
          ))}
        </div>
        <button onClick={copyEnv}
          className="mt-3 flex items-center gap-1.5 rounded-lg border border-slate-300 px-3.5 py-2 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300">
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}{copied ? '已复制' : '复制全部 .env 示例'}
        </button>
      </section>
    </div>
  )
}
