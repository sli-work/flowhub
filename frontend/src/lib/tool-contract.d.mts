import type { ToolDefinition, ToolVersion, ToolBinding, AutomationRule } from './tools'
export function availableToolVersions(tools: ToolDefinition[]): { tool: ToolDefinition; version: ToolVersion }[]
export function automationErrors(rules: AutomationRule[], bindings: ToolBinding[], tools: ToolDefinition[]): string[]
export function parseToolArguments(text: string): Record<string, unknown>
export function resultDocuments(result: unknown): { id: string; name: string }[]
