import { api } from './api'
export interface ToolBinding { tool_version_id: string; scope: 'current_work_item'; approval: 'inherit' | 'required' }
export interface AutomationRule { id: string; event: 'node_entered' | 'expert_output_validated'; toolVersionId: string; enabled: boolean }
export type ToolContext = 'work_item_id' | 'task_id' | 'project'
export interface ToolVersion { contextBindings?: Record<string, ToolContext>; taskScopeReady?: boolean; id: string; revision: number; status: 'draft' | 'tested' | 'published'; inputSchema: Record<string, unknown>; outputSchema: Record<string, unknown>; risk: string; approval: string; testedAt?: string }
export interface ToolDefinition { id: string; name: string; slug: string; description: string; source: 'native' | 'mcp'; status: 'active' | 'disabled'; serverId?: string; currentVersionId?: string; versions: ToolVersion[] }
export interface ToolInvocation { id: string; toolVersionId: string; expertVersionId?: string; runId?: string; taskId?: string; status: string; approvalStatus: string; input: unknown; result: unknown; error?: string; createdAt: string }
export const fetchTools = () => api.get<{ items: ToolDefinition[] }>('/api/v1/tools').then(data => data.items)
