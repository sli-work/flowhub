/* ============ FlowHub 领域类型（与 PRD v2.0 / 原型一致） ============ */

export type PageId =
  | 'tasks' | 'notif' | 'dashboard' | 'projects' | 'workitem' | 'node'
  | 'templates' | 'canvas' | 'docs' | 'agents' | 'org' | 'matrix' | 'audit' | 'channel'

export type RoleKey = 'leader' | 'org' | 'dev' | 'sales'

export interface User {
  id: string
  name: string
  account: string
  dept: string
  roles: string[]
  skills: string[]
  avatarGrad?: string // g1-g6
  status: 'active' | 'disabled' | 'locked' | 'invited'
  dingTalk?: string
  wecom?: string
  load?: number
}

export type WorkItemType = 'requirement' | 'issue'
export type WorkItemStatus =
  | 'draft' | 'submitted' | 'in_progress' | 'waiting_for_information'
  | 'waiting_for_verification' | 'resolved' | 'accepted' | 'rejected'
  | 'closed' | 'cancelled' | 'archived'

export interface WorkItem {
  id: string
  type: WorkItemType
  title: string
  project: string
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  status: WorkItemStatus
  assignee: string
  creator: string
  due: string
  labels: string[]
  progress?: string // 当前节点
  tags?: string[]
}

export type TaskStatus =
  | 'assigned' | 'accepted' | 'in_progress' | 'waiting_for_information'
  | 'pending_confirmation' | 'submitted' | 'returned' | 'transferred'
  | 'completed' | 'cancelled'

export interface TaskItem {
  id: string
  title: string
  wiId: string
  project: string
  node: string
  nodeId?: string
  type: WorkItemType
  priority: 'P0' | 'P1' | 'P2' | 'P3'
  status: TaskStatus
  assignee: string
  due: string
  slaHours: number
  overdue?: boolean
  agentPending?: boolean
  source?: string
  /** 项目归档冻结：任务仅可查看，不可流转 */
  frozen?: boolean
}

/* 节点处理人绑定：模板节点 → 具体用户 + 角色（流转时自动分配，PRD §4.5） */
export interface NodeAssignment {
  nodeId: string
  nodeLabel: string
  /** 绑定的具体用户 id */
  users: string[]
  /** 绑定的角色 / 技能标签，流转时解析为该角色所在用户 */
  roles: string[]
}

export interface ProjectTemplateBinding {
  templateId: string
  name: string
  type: 'requirement' | 'issue' | 'change'
  version: string
  status: 'active' | 'disabled'
  /** 节点处理人绑定（项目 × 模板实例维度） */
  assignments: NodeAssignment[]
}

export interface Project {
  id: string
  name: string
  code: string
  status: 'draft' | 'active' | 'paused' | 'completed' | 'cancelled' | 'archived'
  desc: string
  members: number
  workItems: number
  progress: number
  manager: string
  owner: string
  updated: string
  readOnly?: boolean
  templateBindings: ProjectTemplateBinding[]
}

export interface GlobalTemplate {
  id: string
  name: string
  type: 'requirement' | 'issue' | 'change'
  versions: string[]
  /** 新建工作项时的硬性要求表单（起始节点 schema） */
  startSchema: FormField[]
  /** 模板节点清单（供项目维度绑定处理人） */
  nodes: { id: string; label: string; type: string }[]
}

export interface TemplateVersion {
  version: string
  status: 'draft' | 'reviewing' | 'published' | 'deprecated' | 'archived'
  updated: string
  updatedBy: string
  instances: number
  nodes: number
}

/* 结构化表单 Schema（节点表单 / 新建工作项硬性要求） */
export type FormFieldType =
  | 'input' | 'textarea' | 'select' | 'multiselect' | 'radio'
  | 'date' | 'number' | 'upload' | 'file'

export interface FormFieldOption {
  label: string
  value: string
}

export interface FormField {
  key: string
  label: string
  type: FormFieldType
  required: boolean
  placeholder?: string
  hint?: string
  options?: FormFieldOption[]
}

export interface CanvasNode {
  id: string
  label: string
  type: 'start' | 'task' | 'decision' | 'parallel_split' | 'parallel_join' | 'acceptance' | 'closure' | 'timer' | 'end'
  sub?: string
  x: number
  y: number
  width: number
  height: number
  cfg: {
    typeLine: string
    purpose: string
    handler: string
    fallback: string
    sla: string
    schema: FormField[]
    output: string
    /** 节点级 Agent 绑定（docs/05 §5）：handler=「Agent 自动」时必填 agentId */
    agent?: { agentId: string; caps: Record<string, string> }
    /** 决策节点分支条件：{ 目标节点id: {field, op(eq/ne/contains), value} } + 默认分支 */
    branches?: Record<string, { field: string; op: string; value: string }>
    defaultBranch?: string
    /** 定时节点等待时长（小时） */
    waitHours?: number
    /** 并行分叉节点分支数 */
    branchCount?: number
  }
}

export interface AgentCap {
  name: string
  mode: 'direct' | 'confirm' | 'forbid'
}

export interface Agent {
  id: string
  name: string
  code: string
  desc: string
  status: 'pending' | 'active' | 'suspended' | 'revoked'
  scope: string
  bindings: string
  owner: string
  calls: number
  successRate: number
  avgMs: number
  updated: string
  engine?: string
  provider?: string
  model?: string
  agentType?: string
  toolId?: string
  baseUrl?: string
  systemPrompt?: string
}

/** 客户端工具配置（第一类）：opencode 等。api_key 永不回显。 */
export interface AgentTool {
  id: string
  name: string
  engine: 'opencode' | 'api'
  provider?: string
  model?: string
  models?: string[]
  baseUrl?: string
  desc?: string
  status: string
  createdAt?: string
}

/** 用户 access key（外部 Agent MCP/Skill 接入凭证）。明文仅创建时展示一次。 */
export interface AccessKeyItem {
  id: string
  name: string
  prefix: string
  status: string
  createdAt?: string
  lastUsed?: string
}

export interface DocItem {
  id: string
  name: string
  project: string
  version: string
  level: 'L1' | 'L2' | 'L3'
  scan: '已扫描' | '含毒' | '扫描中'
  uploader: string
  size: string
  time: string
  kind: string
  wi?: string
}

export interface NotificationItem {
  id: string
  title: string
  body: string
  time: string
  channels: { name: string; ok: boolean }[]
  unread?: boolean
  kind: 'arrive' | 'agent' | 'timeout' | 'return' | 'complete' | 'transfer' | 'info' | 'fail'
  failed?: boolean
  retries?: number
  /** 业务跳转目标：点击通知 → 对应工作项 / 任务（空则跳通知中心列表） */
  wiId?: string
  taskId?: string
}

export interface AuditRow {
  time: string
  actor: string
  actorType: 'user' | 'agent' | 'system'
  authorized: string
  action: string
  target: string
  result: 'success' | 'failed' | 'denied'
  reqId: string
  ip: string
}

export interface ActivityItem {
  time: string
  title: string
  desc: string
  by: string
  kind: 'done' | 'cur' | 'wait' | 'agent'
}

export interface FlowNode {
  name: string
  status: 'done' | 'current' | 'wait' | 'return'
  assignee?: string
  time?: string
}

export interface TimelineEvent {
  id: string
  time: string
  title: string
  desc: string
  by: string
  kind: 'user' | 'agent' | 'system' | 'action'
}
