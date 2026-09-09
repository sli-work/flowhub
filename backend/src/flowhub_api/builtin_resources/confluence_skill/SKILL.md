---
name: confluence-query-routing
description: Route Hermes Confluence lookups to robust MCP tools with minimal retries. Use when user asks to search wiki/Confluence, find member info, phone/contact details, incident cases, or knowledge pages.
version: 1.0.0
author: Team MCP
license: MIT
metadata:
  hermes:
    tags: [Confluence, MCP, search, member-lookup]
---

# Confluence Query Routing

Use this skill whenever the user intent is "find information in Confluence".

## Tool Selection Rules

1. For people/member lookups (e.g. "王志文手机号", "李松成员信息"), call:
   - `confluence_search_users`
   - **Fallback**: If `search_users` returns no phone/contact info, search emergency contact documents:
     - Query terms: `运行安全*假期*产品紧急联系人`, `产品紧急联系人`, `春节*紧急联系人`
     - Target space: `OMC` or `operationalSafety`
     - These docs (e.g. `运行安全2026年春节假期产品紧急联系人`) contain phone numbers for product groups (OSM, DRCC, DBRA, etc.)
2. For general content/case/doc lookup (e.g. "oracle切换失败案例"), call:
   - `confluence_quick_search`
3. Use `confluence_search_content` only when the user explicitly gives valid CQL.

## Retry Guard (important)

- Never call the same MCP tool with the same arguments more than 2 times.
- If no result after 2 attempts, stop retrying and return:
  - what was searched
  - which tool was used
  - top candidate results or "no results"
  - one suggested refined query

## Rate Limit Handling (critical)

Confluence MCP tools have **family-level rate limits** (typically 2-4 calls per type per window):
- `confluence_quick_search`: limit of 10
- `confluence_get_page_by_id`: limit of 10
- `confluence_search_content`: limit of 10

When `tool_family_limit_reached` fires:
1. **Stop immediately** — do not retry the same tool type
2. **Summarize what you have** found so far
3. **Ask the user** which specific page to inspect next (they can give a title or ID to prioritize)
4. Switch to a different tool family if possible (e.g., `search_content` instead of `quick_search`)

## Large Page Handling (critical)

Some Confluence pages are extremely large (~175K+ chars) and will be **truncated** by the API:
- `DRCC 发布版本管理` (pageId=26357313) — 474 version entries
- The truncated response is unusable as JSON

### Technique: Verify-then-Direct
1. Use `confluence_search_content` with CQL to **verify** content exists:
   - `type=page AND id={pageId} AND text ~ "keyword"`
2. If CQL confirms the keyword exists on the page, **don't re-fetch** — direct the user to:
   - The page WebUI link for Ctrl+F
   - Say: "The page contains references to '{keyword}' — please open the [link](webui) and Ctrl+F to find the exact row"

### Technique: Cross-Reference with Auxiliary Sources
For "which DRCC version" questions, when the big table is inaccessible:
1. Check `DRCC - 版本记录` (pageId=28869350) — detailed release notes up to V2.20.0
2. Check `DRCC-Fusion {version}` pages in OMC space — feature breakdowns
3. Check `2024迭代` (pageId=36668707) — month-by-month planning (cross-reference with actual release dates)
4. Use the release cadence (~monthly) to estimate which version corresponds to a planned date

## Query Rewriting

When query is vague, generate at most 2 alternatives:
- original query
- one refined query with key nouns

Do not generate long chains of rewritten queries.

## Reference Files

This skill ships with reference files for domain-specific knowledge:
- `references/member-contact-lookup.md` — member phone/contact lookup patterns
- `references/drcc-knowledge-patterns.md` — DRCC version records, asset switch docs, failure case locations, emergency contacts

Read these references via `skill_view(name='confluence-query-routing', file_path='references/drcc-knowledge-patterns.md')` when the user's query targets one of these domains.

## Switch/DRILL Step Query Pattern

When user asks for asset-specific switch/drill steps (Oracle, HANA, SQLServer, MySQL, etc.):

1. **Search scope**: Start in the `ywnr` (运维内容交付) space — this is where detailed switch step docs live.
2. **Query strategy**: Search for asset type + "切换" or "演练" (e.g. "HANA切换", "SQLSERVER适配", "Oracle演练切换").
3. **Single-query fallback**: If no explicit space-scoped match, search broadly with `confluence_quick_search` for the asset type + "切换 流程".
4. **Expected page types found**:
   - `{资产}切换` — switch execution steps (pre-check → switch → post-check)
   - `{资产}桌面演练` — desktop/lvm-snapshot based drill steps
   - `{资产}切换评估` — pre-assessment risk checks
5. **Limit reads**: Only read the 2-3 most relevant pages. Prefer the main switch doc + the desktop drill doc.
6. **Render pattern**: Always structure the response as:
   - Pre-check table (检查项, 执行库, 关键脚本, 异常/排查方向)
   - Switch execution steps table (步骤, 操作, 脚本, 重试策略)
   - Post-check table (检查项, 重试)
   - Key script inventory
   - Warnings / known pitfalls section
   - Doc links

## Response Format

Return concise output:

1. `查询词`
2. `命中结果` (title + link)
3. `成员信息` (if user lookup)
4. `未命中时建议`

## Examples

- User: "查询oracle切换失败的案例"
  - Tool: `confluence_quick_search(query="oracle切换失败 案例", limit=10)`

- User: "查王志文的成员信息"
  - Tool: `confluence_search_users(query="王志文", max_results=20)`
  - If no hit, try once: `query="wangzhiwen"`
