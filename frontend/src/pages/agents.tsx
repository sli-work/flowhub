import { useEffect, useState } from 'react'
import { Bot, Plus, ShieldAlert, KeyRound, PauseCircle, Ban, Wrench, Cpu } from 'lucide-react'
import { useApp, toast } from '../store/app-store'
import { api, ApiError } from '../lib/api'
import { Avatar, Badge, KpiCard, PageHeader, type Tone } from '../components/common'
import { cn } from '../lib/utils'
import type { AgentTool } from '../types'
import { ExternalAccess } from './external-access'

const statusMap: Record<string, [Tone, string]> = {
  active: ['suc', '活跃'], pending: ['warn', '待激活'], suspended: ['orgx', '已挂起'], revoked: ['err', '已吊销'],
}

interface AgentItem {
  id: string; name: string; code: string; desc: string; status: string;
  scope: string; bindings: string; owner: string; calls: number; successRate: number;
  avgMs: number; updated: string; capabilities?: { name: string; mode: string }[];
  engine?: string; provider?: string; model?: string; agentType?: string; toolId?: string;
  baseUrl?: string; systemPrompt?: string;
}

export function AgentsPage() {
  const { openDialog, openAgentDetail, openAgentEdit, openToolEdit } = useApp()
  const [tab, setTab] = useState<'agent' | 'tool' | 'external'>('agent')
  const [filter, setFilter] = useState<'all' | 'active' | 'pending' | 'suspended' | 'revoked'>('all')
  const [agents, setAgents] = useState<AgentItem[]>([])
  const [tools, setTools] = useState<AgentTool[]>([])
  const [kpi, setKpi] = useState({ total: 0, active: 0, monthlyCalls: 0, avgMs: 0, pendingApprove: 0 })

  /* 接后端：GET /agents（items+kpi）与 GET /agents/tools（Agent 客户端配置） */
  useEffect(() => {
    api.get<{ items: AgentItem[]; kpi: { total: number; active: number; monthlyCalls: number; avgMs: number; pendingApprove: number } }>('/api/v1/agents')
      .then((d) => {
        if (d.items.length) setAgents(d.items)
        setKpi(d.kpi)
      })
      .catch(() => { /* 后端不可用：空列表 */ })
    api.get<{ items: AgentTool[] }>('/api/v1/agents/tools')
      .then((d) => setTools(d.items))
      .catch(() => {})
  }, [])

  const toolName = (id?: string) => tools.find((t) => t.id === id)?.name ?? (id ? '未知工具' : '—')

  const changeStatus = async (id: string, action: 'suspend' | 'activate' | 'revoke') => {
    try {
      await api.post(`/api/v1/agents/${id}/status`, { action })
      setAgents((prev) => prev.map((a) => (a.id === id ? { ...a, status: action === 'suspend' ? 'suspended' : action === 'activate' ? 'active' : 'revoked' } : a)))
      toast.success(`Agent 已${action === 'suspend' ? '挂起：拒绝新调用，存量请求完成后退场' : action === 'activate' ? '激活' : '吊销：密钥立即失效，请求拒绝并审计'}`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  const deleteAgent = async (agent: AgentItem) => {
    if (agent.status !== 'revoked') return
    if (!window.confirm(`确定删除已吊销的 Agent「${agent.name}」？删除后不可恢复。`)) return
    try {
      await api.del(`/api/v1/agents/${agent.id}`)
      setAgents((prev) => prev.filter((item) => item.id !== agent.id))
      setKpi((prev) => ({ ...prev, total: Math.max(0, prev.total - 1) }))
      toast.success('Agent 已删除')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  const list = agents.filter((a) => filter === 'all' || a.status === filter)

  return (
    <div className="page-container">
      <PageHeader
        title="Agent 管理"
        sub="先配置可用 Provider 与模型，再为 Agent 类型和具体 Agent 选择执行模型；系统提示词可自由自定义 · 创建后可在画布节点绑定使用"
        actions={
          tab === 'tool' ? (
            tools.some((t) => t.engine === 'opencode') ? (
              <button
                className="cursor-not-allowed rounded-lg bg-slate-300 px-3.5 py-2 text-[13px] font-medium text-slate-500 dark:bg-slate-700 dark:text-slate-400"
                disabled
                title="opencode 客户端全局唯一（已配置），请编辑现有 opencode 工具"
              >
                <Plus className="mr-1 inline h-4 w-4" />新增客户端
              </button>
            ) : (
              <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
                onClick={() => openToolEdit(null)}>
                <Plus className="mr-1 inline h-4 w-4" />新增客户端
              </button>
            )
          ) : (
            <button className="rounded-lg bg-blue-600 px-3.5 py-2 text-[13px] font-medium text-white shadow-sm transition-all hover:bg-blue-700"
              onClick={() => openDialog('agentCreate')}>
              <Plus className="mr-1 inline h-4 w-4" />创建 Agent
            </button>
          )
        }
      />

      {/* 两类配置 Tab */}
      <div className="mb-4 flex items-center gap-2 border-b border-slate-200 dark:border-slate-800">
        {([
          ['agent', 'Agent 配置', <Bot key="i" className="h-4 w-4" />],
          ['tool', 'Provider 管理', <Wrench key="i" className="h-4 w-4" />],
          ['external', '外部接入', <KeyRound key="i" className="h-4 w-4" />],
        ] as const).map(([k, label, icon]) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn('flex items-center gap-1.5 border-b-2 px-3.5 py-2.5 text-[13px] font-medium transition-colors',
              tab === k ? 'border-blue-600 text-blue-600 dark:text-blue-400' : 'border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400')}>
            {icon}{label}
          </button>
        ))}
        <span className="ml-auto pb-2 text-[11.5px] text-slate-400">{tab === 'tool' ? 'Provider 管理连接方式、Base URL、API Key 与可用模型；不设置默认模型' : tab === 'external' ? '外部 Agent 通过 MCP + Skill 接入平台（access key 认证，仅可访问你的任务数据）' : 'Agent 配置：选择类型、Provider 与模型（系统提示词模板可自定义）'}</span>
      </div>

      {tab === 'external' ? <ExternalAccess /> : tab === 'tool' ? (
        <div className="grid gap-4 md:grid-cols-2">
          {tools.map((t) => (
            <div key={t.id} className="rounded-xl border border-slate-200 bg-white p-5 shadow-s transition-all hover:-translate-y-0.5 hover:shadow-m dark:border-slate-700 dark:bg-slate-900">
              <div className="flex items-start gap-3">
                <span className={cn('flex h-10 w-10 flex-none items-center justify-center rounded-lg',
                  t.engine === 'api' ? 'bg-gradient-to-br from-emerald-500 to-teal-700 text-white' : 'bg-gradient-to-br from-slate-600 to-slate-900 text-white')}>
                  {t.engine === 'api' ? <Cpu className="h-5 w-5" /> : <Bot className="h-5 w-5" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[14.5px] font-semibold text-slate-800 dark:text-slate-100">{t.name}</span>
                    <Badge tone={t.engine === 'api' ? 'suc' : 'info'}>{t.engine === 'api' ? 'API 直连' : 'CLI 引擎'}</Badge>
                  </div>
                  <p className="mt-1 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{t.desc || '—'}</p>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3 text-[11.5px] dark:bg-slate-800/60">
                <div><span className="text-slate-400">引擎：</span><span className="font-medium text-slate-600 dark:text-slate-300">{t.engine}</span></div>
                 <div><span className="text-slate-400">Provider：</span><span className="font-medium text-slate-600 dark:text-slate-300">{t.provider || '未命名'}</span></div>
                 <div className="col-span-2"><span className="text-slate-400">Base URL：</span><span className="font-medium text-slate-600 dark:text-slate-300">{t.baseUrl || '—（使用 OpenCode 内置端点）'}</span></div>
                 <div className="col-span-2"><span className="text-slate-400">模型：</span><span className="font-medium text-slate-600 dark:text-slate-300">{t.models?.length ? t.models.join(' · ') : '使用引擎内置模型列表'}</span></div>
              </div>
              <div className="mt-3 text-[11px] text-slate-400">{t.engine === 'opencode' ? '使用容器内置 opencode 引擎执行' : 'API Key 已加密存储，不展示明文'}</div>
              <div className="mt-3 flex justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                 <button className="rounded-lg border border-slate-300 px-3 py-1.5 text-[12px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300"
                   onClick={() => openToolEdit(t.id)}>
                   编辑 Provider
                </button>
              </div>
            </div>
          ))}
          {tools.length === 0 && (
            <div className="col-span-full rounded-lg border border-dashed border-slate-300 p-8 text-center text-[13px] text-slate-400 dark:border-slate-700">
               暂无 Provider 配置，点击右上角「新增客户端」添加 OpenCode 或 API 直连 Provider
            </div>
          )}
        </div>
      ) : (
        <>
      <div className="mb-5 grid grid-cols-2 gap-4 xl:grid-cols-4">
        <KpiCard label="Agent 总数" value={kpi.total} icon={<Bot className="h-4 w-4" />} tone="blue" />
        <KpiCard label="活跃 Agent" value={kpi.active} icon={<Bot className="h-4 w-4" />} tone="green" />
        <KpiCard label="本月调用" value={kpi.monthlyCalls} icon={<Bot className="h-4 w-4" />} tone="violet" />
        <KpiCard label="平均响应" value={kpi.avgMs ? `${(kpi.avgMs / 1000).toFixed(1)}s` : '—'} icon={<Bot className="h-4 w-4" />} tone="amber" />
      </div>

      {/* 授权交集提示 */}
      <div className="mb-4 flex items-start gap-2.5 rounded-lg border border-violet-200 bg-violet-50 p-3 text-[12px] leading-relaxed text-violet-700 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-300">
        <ShieldAlert className="mt-0.5 h-4 w-4 flex-none" />
        <span>每次 Agent 操作的有效权限 = Agent 能力 ∩ 授权用户功能权限 ∩ 数据范围 ∩ 项目成员关系 ∩ 节点角色/技能 ∩ Agent 绑定 ∩ 本次授权范围；高风险操作（提交/退回/转办/暂停/恢复/关闭）默认需用户确认（PRD §8.2/8.3）。</span>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {([
          ['all', '全部'], ['active', '活跃'], ['pending', '待激活'], ['suspended', '已挂起'], ['revoked', '已吊销'],
        ] as const).map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
            className={cn('rounded-full px-3.5 py-1.5 text-[12.5px] font-medium transition-colors',
              filter === k ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 hover:text-blue-600 dark:bg-slate-900 dark:text-slate-400')}>
            {label}
          </button>
        ))}
        <span className="ml-auto text-[12px] text-slate-400">未注册 / 已吊销 / 未绑定的 Agent 请求将被拒绝并审计（PRD §16.5）</span>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {list.map((a) => {
          const [tone, label] = statusMap[a.status]
          return (
            <div key={a.id} className={cn('rounded-xl border bg-white p-5 shadow-s transition-all hover:-translate-y-0.5 hover:shadow-m dark:bg-slate-900',
              a.status === 'revoked' ? 'border-red-200 opacity-70 dark:border-red-500/30' : 'border-slate-200 hover:border-blue-300 dark:border-slate-700 dark:hover:border-blue-500/40')}>
              <div className="flex items-start gap-3">
                <span className={cn('flex h-10 w-10 flex-none items-center justify-center rounded-lg',
                  a.status === 'revoked' ? 'bg-slate-200 text-slate-500' : a.status === 'suspended' ? 'bg-amber-50 text-amber-600' : 'bg-gradient-to-br from-violet-500 to-purple-700 text-white')}>
                  {a.status === 'revoked' ? <Ban className="h-5 w-5" /> : a.status === 'suspended' ? <PauseCircle className="h-5 w-5" /> : <Bot className="h-5 w-5" />}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[14.5px] font-semibold text-slate-800 dark:text-slate-100">{a.name}</span>
                    <Badge tone={tone} dot>{label}</Badge>
                  </div>
                  <div className="font-mono text-[11px] text-slate-400">{a.code}</div>
                  <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-slate-500 dark:text-slate-400">{a.systemPrompt || a.desc || '未配置系统提示词'}</p>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-slate-50 p-3 text-[11.5px] dark:bg-slate-800/60">
                <div><span className="text-slate-400">工具：</span><span className="font-medium text-slate-600 dark:text-slate-300">{toolName(a.toolId)}</span></div>
                <div><span className="text-slate-400">类型：</span><span className="font-medium text-slate-600 dark:text-slate-300">{a.agentType || '通用'}</span></div>
                <div><span className="text-slate-400">引擎：</span><span className="font-medium text-slate-600 dark:text-slate-300">{a.engine === 'api' ? 'API 直连' : a.engine || '—'}</span></div>
                <div><span className="text-slate-400">模型：</span><span className="font-medium text-slate-600 dark:text-slate-300">{a.model || 'opencode 默认'}</span></div>
                <div><span className="text-slate-400">注册人：</span><span className="inline-flex items-center gap-1 font-medium text-slate-600 dark:text-slate-300"><Avatar name={a.owner} grad="g5" size={16} />{a.owner}</span></div>
                <div><span className="text-slate-400">最近更新：</span><span className="font-medium text-slate-600 dark:text-slate-300">{a.updated}</span></div>
              </div>
              <div className="mt-3 flex items-center gap-4 text-[12px] text-slate-500 dark:text-slate-400">
                <span>累计调用 <b className="text-slate-700 dark:text-slate-200">{a.calls}</b></span>
                <span>成功率 <b className={cn(a.successRate >= 90 ? 'text-emerald-600' : 'text-red-500')}>{a.successRate}%</b></span>
                <span>平均响应 <b className="text-slate-700 dark:text-slate-200">{a.avgMs ? `${(a.avgMs / 1000).toFixed(1)}s` : '—'}</b></span>
              </div>
              <div className="mt-3.5 flex gap-2 border-t border-slate-100 pt-3 dark:border-slate-800">
                <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300" onClick={() => openAgentEdit(a.id)}>编辑</button>
                {a.status === 'active' ? (
                  <>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300" onClick={() => openDialog('agentConfirm')}>授权 / 确认</button>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-amber-400 hover:text-amber-600 dark:border-slate-700 dark:text-slate-300" onClick={() => changeStatus(a.id, 'suspend')}>挂起</button>
                    <button className="flex-1 rounded-lg border border-red-200 py-1.5 text-[12.5px] font-medium text-red-500 hover:bg-red-50 dark:border-red-500/30 dark:hover:bg-red-500/10" onClick={() => changeStatus(a.id, 'revoke')}>吊销</button>
                  </>
                ) : a.status === 'pending' ? (
                  <>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300" onClick={() => changeStatus(a.id, 'activate')}>激活</button>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300" onClick={() => openAgentDetail(a.id)}>详情</button>
                  </>
                ) : a.status === 'suspended' ? (
                  <>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300" onClick={() => changeStatus(a.id, 'activate')}>恢复</button>
                    <button className="flex-1 rounded-lg border border-slate-300 py-1.5 text-[12.5px] font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300" onClick={() => openAgentDetail(a.id)}>详情</button>
                  </>
                ) : (
                  <>
                    <button className="flex-1 items-center justify-center gap-1.5 rounded-lg border border-red-200 py-1.5 text-[12.5px] font-medium text-red-500 dark:border-red-500/30" onClick={() => openAgentDetail(a.id)}>
                      <KeyRound className="mr-1 inline h-3.5 w-3.5" />查看吊销记录
                    </button>
                    <button className="flex-1 rounded-lg border border-red-300 py-1.5 text-[12.5px] font-medium text-red-600 hover:bg-red-50 dark:border-red-500/40 dark:hover:bg-red-500/10" onClick={() => deleteAgent(a)}>删除</button>
                  </>
                )}
              </div>
            </div>
          )
        })}
      </div>
        </>
      )}
    </div>
  )
}
