# Expert Run 抽屉化 + 采纳二次格式修正 · 交互设计

> 状态：待评审 ｜ 关联页面：节点处理页（`frontend/src/pages/node.tsx`）
> 关联后端：`POST /tasks/{id}/adopt-run`、`POST /tasks/{id}/ai-fill`、自动节点 `ai_autosubmit` 链路

---

## 1. 背景与目标

**现状问题**：节点处理页中栏以「Expert Run 结果」SectionCard 承载全部 Expert 产出——多 Run 列表、全文渲染、字段回填摘要、卡片内重跑面板、Runtime 策略。问题：

1. 中栏纵向被产出卡片撑得很长，产出区 `max-h-96` 压缩阅读体验，长 Markdown/表格仍显局促；
2. 采纳是「原始解析 → 直接回填」，非 JSON 产出只能全文灌进首个 textarea，字段归位差、长文本排版乱；
3. 自动节点的过程信息（生成中 → 已流转）只能靠表单卡片上的一行提示，缺少观察窗口。

**目标**：

1. 移除中栏 Expert Run 卡片，产出改由**右侧抽屉**承载（复用 `DocumentViewerDrawer` 的抽屉模式：遮罩、拖拽调宽、左侧列表导航）；
2. 点「采纳」时新增 **AI 二次格式修正**：把 Expert 输出按节点表单契约做格式规范化后回填，**只调格式、不改内容**；
3. 全自动节点（handler = 「Expert 自动」）保持**自动采纳 + 自动流转**，且在采纳前同样执行格式修正（无人审核，格式质量更要保证）。

---

## 2. 总体方案

```
中栏                                    右侧抽屉（新增 ExpertRunDrawer）
┌─────────────────────────┐            ┌──────────────────────────────────┐
│ 任务书                    │            │ Header: Expert Run · 节点名 · 状态 │
├─────────────────────────┤            ├────────┬─────────────────────────┤
│ 表单（extra: 采纳/协助填充）│  ──打开──▶ │ Run    │ 产出视图                 │
├─────────────────────────┤            │ 历史    │  [全文] [字段预览]        │
│ ★ Expert Run 状态条（新） │            │ 列表   │  修正 diff（折叠）        │
│  状态徽标·相对时间·操作    │            │ (可收起)│  Runtime 策略（折叠）     │
├─────────────────────────┤            ├────────┴─────────────────────────┤
│ 继承上下文 / 子任务        │            │ 底部操作栏: 重新执行 │ AI修正+采纳 │
└─────────────────────────┘            └──────────────────────────────────┘
```

- **中栏**：Expert Run 卡片整块删除，替换为一条单行「状态条」（约 44px），只承载可见性 + 快捷动作。
- **抽屉**：产出阅读、Run 历史切换、重新执行（含上下文输入）、采纳（含格式修正）、Runtime 策略全部收进来。
- **表单卡片 extra**：保留三个快捷按钮（生成中 / 采纳 / 协助填充），与抽屉共享同一套状态与调用。

---

## 3. 中栏：Expert Run 状态条

位置：表单 SectionCard 与「继承上下文」之间（采纳动作作用于表单，紧邻表单）。起始节点 / 未绑定 Expert 的节点不渲染。

单行结构：`Bot 图标 + 状态徽标 + 相对时间 + 主操作按钮 + 「查看详情」`，点击条身任意处打开抽屉。

| 状态 | 状态条形态 | 主操作 |
|---|---|---|
| 无 Run（canExpert 但尚无产出） | 灰底细条「尚未执行 Expert，产出将出现在这里」 | 「Expert 协助填充」→ 打开抽屉并聚焦重执行输入 |
| running | 紫底 + 脉冲图标「Expert 生成中…（轮询中）」 | 「查看进度」→ 打开抽屉 |
| succeeded | 紫底「最新产出已生成 · {相对时间}」 | 「采纳（AI 规范格式）」→ 走 §5 流程 |
| failed | 红底「上次执行失败：{error 摘要截断}」 | 「重新执行」→ 打开抽屉并展开上下文输入 |
| interrupted | 橙底「有 {n} 条 Expert 操作待审批」 | 「去审批」→ 打开现有 `expertApproval` 弹窗 |

多 Run 历史不再在中栏展开（原卡片逐条罗列），统一收进抽屉左侧列表。

---

## 4. 抽屉：ExpertRunDrawer

新组件 `frontend/src/components/expert-run-drawer.tsx`，交互基线对齐 `DocumentViewerDrawer`：

- 右侧滑出 + 遮罩，默认宽 **720px**，左缘可拖拽（480px ~ 94vw），双击手柄复位；Esc / 点遮罩关闭（重执行输入有草稿时 Esc 先关输入面板，再关抽屉）。
- 打开时不抢中栏表单状态；采纳/重跑进行中抽屉保持打开并同步按钮态。

### 4.1 布局

```
┌────────────────────────────────────────────────────────┐
│ Header：Bot「Expert Run」· 节点名  [状态徽标]      [✕]   │
├──────┬─────────────────────────────────────────────────┤
│ Run  │ ① 状态提示条（随 run 状态变化的横幅）              │
│ 历史  │ ② 视图 Tab：[全文] [字段预览]                    │
│ 列表  │ ③ 产出内容区（滚动）                             │
│ w-56 │ ④ ▸ 修正 diff（采纳且做过修正后出现，默认折叠）    │
│ 可收起│ ⑤ ▸ Runtime 策略（能力标签，默认折叠）            │
├──────┴─────────────────────────────────────────────────┤
│ 底部操作栏（sticky）：[重新执行]        [AI 修正并采纳]   │
└────────────────────────────────────────────────────────┘
```

### 4.2 Run 历史列表（左列，w-56，可收起）

每项：状态圆点 + `startedAt`（MM-DD HH:mm）+ 上下文标记（带补充上下文的 Run 加 ✎ 角标）。当前项高亮，切换仅换主区内容，不重新请求（数据已在 `expertRuns` 中）。倒序（最新在上），与现有排序一致。

### 4.3 状态提示条（主区顶部）

- running：`LoaderCircle 脉冲`「Expert 正在生成本节点产出…」（沿用 3s 轮询）；
- failed：红条展示完整 `run.error`（抽屉宽度足够，不再截断），下方给「重新执行」快捷链；
- interrupted：橙条「Expert 执行中请求人工审批」+「去审批」按钮（打开 `expertApproval`）；
- succeeded 且已采纳（`parsed.normalized` 标记存在）：绿条「已采纳：产出经 AI 格式规范后回填表单」。

### 4.4 产出视图（两个 Tab）

1. **全文**：复用现有 `RunOutputPreview` 的渲染逻辑（Markdown 直读 / JSON 美化 / 错误文本），去掉 `max-h-96`，改为抽屉主区自然滚动——这是抽屉化的核心收益：长产出完整可读。
2. **字段预览**：按节点 schema 逐字段卡片展示解析值——`label` + 值（textarea 用 Markdown 渲染，其余原样；upload 字段显示将生成的文档名）。空缺字段灰显「未生成」。此 Tab 即「采纳将回填什么」的精确预览，替代原卡片底部的「采纳将回填 N 个字段」一行字。

无 schema 的节点（仅重跑无回填）不显示字段预览 Tab 与采纳按钮。

### 4.5 修正 diff 区（④）

采纳执行过 AI 格式修正后出现，默认折叠：

- 摘要行：「AI 格式规范：调整了 {n} 个字段的排版，内容未改动 · {时间}」；
- 展开后逐字段 before / after 对照（非文本字段显示 `旧值 → 新值`；textarea 字段上下两段 Markdown 渲染对照）。v1 用简单并排对照即可，不做行级 LCS diff。

### 4.6 底部操作栏

| 按钮 | 可见条件 | 行为 |
|---|---|---|
| 重新执行（次按钮） | `canExpertRerun` 且当前 Run 非 running | 操作栏上方展开上下文输入面板（原卡片内面板迁移至此，交互不变：2000 字上限、⌘/Ctrl+↵ 提交、Esc 收起），提交后覆盖重跑、抽屉切到当前 Run 的 running 态 |
| AI 修正并采纳（主按钮） | `canExpert` 且 Run succeeded 且节点有 schema | 走 §5 采纳流程（含格式修正） |
| ——自动节点替代文案 | `isAutoNode` | 操作栏替换为只读说明「全自动节点：Run 成功后自动修正格式、采纳并流转至下一节点，无需人工操作」 |

---

## 5. 核心流程：采纳 × AI 二次格式修正

### 5.1 交互状态机

```
点击「AI 修正并采纳」
  → 按钮「AI 规范格式中…」（全站同一时刻仅一个采纳请求，沿用 adoptBusy）
  → 成功：values 回填表单 + aiFilledKeys 高亮
        toast「已采纳 Expert 产出（经 AI 格式规范），请审核后提交」
        run.parsed 打上 normalized 标记 → 抽屉出现 diff 区、状态条变绿
  → 修正失败：自动降级按原始解析采纳
        toast.warning「AI 格式规范失败，已按原始解析结果采纳」
  → 产出解析不出任何表单值：沿用现有 422 报错文案（可重新生成）
```

- **不新增确认步骤**：现有流程本就是「采纳后人工审核表单再提交」，格式修正直接落表单，审核面不变；diff 在抽屉可查，信任链不缺。
- **超时兜底**：修正单次模型调用目标 < 15s；请求失败/超时一律降级原样采纳，**采纳永不因修正而失败**。
- 修正期间若抽屉开着，抽屉内主按钮与状态条按钮同步为 loading。

### 5.2 修正范围（AI 做什么 / 不做什么）

| 输入形态 | 修正动作 |
|---|---|
| 已解析 JSON（多数情况） | 仅对 textarea/input 长文本做 Markdown 规范（标题层级、列表、表格、断行、术语排版统一）；剥离围栏/杂讯、键名映射、类型规整（number/date）、select 的 label→value 映射 |
| 非 JSON 全文产出 | 按输出契约把内容**归位**到各字段（只搬运、不撰写），再做排版规范——替代现在「全文灌进首个 textarea」的兜底 |
| 空输出 / failed Run | 不提供采纳，无修正 |

**硬约束（写进修正 prompt）**：

> 你是格式规范化器。仅做格式调整：Markdown 结构、断行、键名与值类型映射、内容按字段归位。禁止增删改任何事实与语义：不新增观点/数据/结论，不删减要点，不改写措辞。输出仍须严格遵守给定 JSON 输出契约。

**内容不变的三重保障**：

1. prompt 硬约束 + 输出走同一 `parse_schema_output` 契约解析（非法字段值仍会被告警）；
2. 非自由文本字段（select/radio/multiselect/number/date）程序校验：修正前后 value 不一致时**放弃该字段修正**、保留原值并计入 warnings；
3. 修正结果留痕可查（见 5.3）。

### 5.3 后端设计

`POST /tasks/{task_id}/adopt-run` body 增加 `normalize: boolean`（前端显式传 true）：

```
guard（现有 _expert_fill_guard）
→ run 校验（现有：非 running、succeeded）
→ 若 normalize 且 run.parsed 未带 normalized 标记：
    normalize_run_output(run, schema, deployment)     # 新增，expert_runtime.py
      · 构造修正 prompt = 原 output + schema 契约 + §5.2 硬约束
      · 模型来源：节点绑定的 Expert Deployment（与生成同源，不新增配置）
      · 解析走 parse_schema_output，非文本字段逐项 diff 校验（不一致回退原值）
    修正结果写回 run.parsed：{ values, warnings, normalized: true, formattedAt }
    （run.output 永不覆写——原始产出始终可追溯）
→ fill_task_from_run 消费（新）快照 → upload 字段生成文档（现有幂等逻辑不变）
→ 审计 task:adopt_run 的 after 增加 { normalized, diffFields }
```

- **幂等**：`normalized` 标记落在 run.parsed 上，重复采纳不重复调模型、不重复生成文档（沿用现有 upload 快照替换机制）。
- 降级路径：normalize 抛错 → 捕获后用原 parsed 快照继续走 fill_task_from_run，warnings 附加「格式规范失败」。

---

## 6. 全自动节点（Expert 全自动 / handler = 「Expert 自动」）

自动采纳流转链路（后台 `_on_auto_finished → ai_autosubmit → advance`）**保持不变**，插入两处增强：

1. **采纳前同样格式修正**：`ai_autosubmit` 在 `fill_task_from_run` 前调用同一 `normalize_run_output`（is_auto 时）。失败静默降级原解析，不阻塞流转。
2. **完成通知与跳转**：前端现有轮询（3s，`expertProcessing`）检测到任务从 `pending_confirmation` 变为 `completed` 时：
   - toast「Expert 已自动完成本节点并流转至『{下一节点}』」；
   - 优先直达下一任务：后端在任务详情响应中新增 `nextTaskId`（`ai_autosubmit` 的 advance 结果回写任务/Run 元数据）；兜底打开工作项详情（工作项页当前节点已是下一节点）。
   - 抽屉若开着，状态条同步为「已流转」态。

**人工兜底态不变**：Run 失败 / 校验不过 / 深度超限时任务回退 `assigned`，此时状态条与抽屉均按「人工兜底」呈现（现有蓝条文案保留在表单卡内），人可重执行或手填提交。

---

## 7. 边界与异常

| 场景 | 行为 |
|---|---|
| 归档冻结 / 历史任务 | 抽屉只读：可看全文与字段预览，操作栏隐藏（沿用 `readonlyTask`） |
| 无 schema 的 Expert 节点 | 只有全文视图与重新执行，无采纳按钮（沿用 `canExpertRerun` 语义） |
| 多 Run | 一切操作仅针对抽屉当前选中的 Run；「采纳」始终对最新 succeeded Run（状态条与表单卡 extra 的快捷按钮固定指向 `expertRuns[0]`） |
| 重跑进行中再次点重跑 | 后端 423 已有；前端沿用 rerunBusy + 「仍有 Run 执行中」禁用态 |
| 采纳与重执行并发 | 重执行发起时若正在采纳，先拒绝重执行（提示「采纳进行中」），反之亦然（共用互斥 busy 位） |
| interrupted 审批流 | 审批仍走 `expertApproval` 弹窗（本设计不改），抽屉提供跳转入口 |
| 原始输出追溯 | `run.output` 任何情况下不覆写；修正只落 parsed 快照 |

---

## 8. 实现改动清单

**前端（node.tsx + 新组件）**
1. 删除中栏「Expert Run 结果」SectionCard（含卡片循环、卡片内重执行面板、Runtime 策略块，node.tsx:772-865）；
2. 新增 `components/expert-run-drawer.tsx`（抽屉本体）+ 中栏状态条；
3. `RunOutputPreview` 逻辑迁入抽屉（去掉高度限制），字段预览 Tab 复用 parsed 快照渲染；
4. 表单卡 extra 三按钮保留：「Expert 协助填充」改为打开抽屉并展开重执行输入；「采纳 Expert 结果」→「采纳（AI 规范格式）」，adopt-run 请求带 `normalize: true`；
5. 轮询检测自动节点完成 → toast + 跳转（依赖后端 nextTaskId，兜底跳工作项）。

**后端**
1. `adopt-run` 增加 `normalize` 参数 + `normalize_run_output`（expert_runtime.py：修正 prompt、契约解析、非文本字段 diff 校验、parsed 快照打标、降级）；
2. `ai_autosubmit` 接入 normalize（失败静默降级）；
3. 任务详情响应增加 `nextTaskId`（自动流转后回写）；
4. 审计 `task:adopt_run` after 增加 normalized / diffFields。

---

## 9. 验收要点

- [ ] 中栏不再出现 Expert Run 卡片；状态条五种状态（无 Run/running/succeeded/failed/interrupted）形态正确；
- [ ] 抽屉可打开/拖宽/关闭，Run 历史可切换，全文视图长 Markdown 完整滚动可读；
- [ ] 采纳后表单值排版明显规范（标题/列表/表格），抽屉 diff 区可见 before/after；
- [ ] 人为构造修正失败（如断网/模型异常）→ 仍完成采纳，toast 提示降级；
- [ ] 修正前后 select/number/date 值完全一致；重复采纳不重复调模型、不重复生成文档；
- [ ] 全自动节点：生成 → 自动修正 → 自动采纳 → 自动流转全程无需人工，完成后有 toast 且能直达下一任务；
- [ ] 自动节点失败回退人工兜底后，可重执行/手填提交；
- [ ] `run.output` 在数据库中始终为模型原始输出。
