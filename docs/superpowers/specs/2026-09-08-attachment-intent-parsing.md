# 任务附件的意图驱动解析与证据上下文（Attachment Intent-Driven Parsing & Evidence Context）

## Goal

将当前「附件名 + 少量文本片段」的 Agent 上下文方式，升级为：附件证据以 FlowHub 工具（`flowhub.attachment.*`）形式提供，Expert 运行时按需建立临时索引、检索证据片段后写入模型上下文。解析结果按文档版本缓存，避免同一 PDF 或 Axure 包被重复处理；解析状态、证据片段与来源定位在任务处理界面可见可追溯。

> 关键取向：不把附件解析/注入逻辑写死在 LangGraph 业务流程里，而是与现有 FlowHub Native Tools（repo 工具）同构——模型自主决定何时调用附件工具、调用哪些、检索什么。

## Background（现状与问题）

现状位于 `backend/src/flowhub_api/services/agent_context.py`：

- `build_task_context()` 从工作项起始表单、前序节点表单与追加信息中收集 `DocItem` 附件引用（`_file_ids` + `DocItem.wi == task.wi_id`），仅输出：
  - 附件元数据行（`_fmt_doc`：名称/类型/版本/级别/上传者/大小/受控读取链接）；
  - `.txt/.md/.yaml/.yml` 文件正文前 `_MAX_DOC_BYTES=4096` 字节（`_doc_body`），其他格式一律不读正文。
- 结果：PDF/DOCX/XLSX/PPTX/ZIP/Axure 附件在模型上下文里只有文件名，Expert 结论无法引用具体页/表/条目，只能靠文件名猜测；同一附件在每次 Run 都被重新读取。

本 spec 的目标是替换这段「附件名 + 4KB 文本片段」逻辑，引入：

1. 附件证据服务（意图选择 → 解析/缓存命中 → 索引检索 → 证据注入）。
2. 按 文档 ID + 对象版本/ETag + 解析器版本 的临时缓存。
3. 可插拔 OCR 适配器（未配置时明确降级，不伪造内容）。
4. LangGraph 上下文阶段子流程 + 运行轨迹 + 质量检查。
5. 任务处理界面的附件解析状态、证据片段查看与「重试解析」入口。

## Scope

- 后端新增附件证据服务、格式解析器、临时缓存、OCR 适配器协议、证据检索。
- LangGraph `model_node` 以 Tools 形式提供附件证据能力（`flowhub_attachment_list/search`），模型按需选择调用；`build_task_context` 仍提供附件目录元数据，`run.parsed["attachmentEvidence"]` 汇总本轮实际使用的证据与 trace。
- 质量检查：涉及附件结论必须至少附一个附件证据定位；无可用证据时明确提示。
- 前端任务处理页：附件解析状态徽标、证据片段展开、失败重试解析入口。
- 保留现有 PDF / ZIP / Axure 在线预览能力（`document-viewer-drawer.tsx`），解析不依赖浏览器 iframe 执行内容。

## 非目标（Out of Scope）

- 不建设长期向量知识库 / pgvector 附件索引。
- 不做上传时全量建库（附件在 Expert 运行时按意图解析）。
- 不改变文档上传、预览、下载、权限链路本身。
- 不建设长期 OCR 服务；OCR 为本地 CPU 推理（RapidOCR），可通过 `RuntimeConfig` 关闭，关闭/不可用时回到「需 OCR」降级。

## 决策与假设

按用户设计，明确如下落点：

1. **新增解析依赖**：`pymupdf`（PDF 文本层提取 + 页渲染一体，供 OCR 用）、`python-docx`、`openpyxl`、`python-pptx`、`rapidocr-onnxruntime`（本地 OCR，CPU 推理，支持中英文）。已验证内网 PyPI 镜像（`http://192.168.239.230:8887/repository/mc-group-pypi/simple/`）全部可达。
2. **缓存存储**：进程内 TTL LRU 缓存为主，不依赖 Redis（测试环境无 Redis，且设计明确「临时索引、不建长期库」）。对象指纹 = `doc_id:version:object_name:size:time`，与解析器版本常量共同构成缓存键；文档被重新上传（新 `DocItem.id`）或字段变化即失效。
3. **OCR**：定义 `OcrAdapter` 协议，默认实现 `RapidOcrAdapter`（`rapidocr-onnxruntime` 本地 CPU 推理，中英文）；`RuntimeConfig["attachment.ocr.enabled"]=="true"` 且依赖可用时启用，否则回退 `NoopOcrAdapter` → 扫描 PDF 标记「需 OCR」并提示，不伪造文本。OCR 仅作用于无文本页，带 页数/字符/耗时 上限，超限即标记「需 OCR」降级。
4. **「模型二次检索」**：原生 `flowhub.attachment.search` 工具支持模型多次调用（携带 `exclude_seq` 排除已见片段，等价于二次检索），带 文档数/页数/字符数/耗时/递归深度 上限；另暴露 FlowHub MCP 工具 `retrieve_attachment_evidence` 供外部 Agent 场景按需检索，复用同一检索逻辑。

## 架构总览

```
任务处理页 (node.tsx)
   │  GET /tasks/{id}  expertRuns[].parsed.attachmentEvidence / events
   │  POST /tasks/{id}/reparse-attachments （失败重试解析）
   ▼
Expert Run 触发（tasks.ai-fill / 自动节点）
   ▼
execute_run → snapshot(prompt) → flowhub_read_snapshot + build_task_context（只含附件目录清单，不注入正文）
   ▼
model_node (LangGraph)  ←── 附件证据以 Tools 形式提供，模型按需选择调用
   │  attachment_tools_factory(task) → create_attachment_tool_bundle
   │     tools: flowhub.attachment.list（附件目录+解析状态）
   │            flowhub.attachment.search（意图选择 → 解析/缓存 → 检索证据片段）
   │     run_flowhub_tool_loop：bind_tools，模型自主调用，服务器控预算
   │        （调用数/轮数/字符/耗时上限；每附件 trace：选择理由/解析器/缓存命中/耗时/提取数/片段数/失败原因）
   ▼
model_node → system prompt 仅提示「可调用 flowhub.attachment.* 检索附件证据，引用 [附件@doc:位置:seq]」
   ▼
质量校验：产出声称参考附件但本轮未调用任何 flowhub.attachment.* 工具 → 「附件未解析/需人工核对」
   ▼
ExpertRunEvent（tool trace）+ run.parsed.attachmentEvidence（模型实际调用汇总，前端展示）
```

## 数据模型与接口契约

### 后端服务文件（全部新建于 `backend/src/flowhub_api/services/`）

#### `attachment_evidence.py` — 附件证据服务（主入口）

```python
@dataclass
class AttachmentCandidate:
    doc: DocItem
    selected: bool
    reason: str              # 意图选择理由（含关键词命中）
    score: float

@dataclass
class EvidenceChunk:
    doc_id: str
    doc_name: str
    location: str            # "p12" / "data/items.json" / "Sheet2!A1" / "slide3"
    seq: int                 # 片段编号（跨附件可引用）
    kind: str                # "text" | "table" | "title" | "ocr_marker" | ...
    text: str

@dataclass
class ParsedAttachment:
    doc_id: str
    status: str              # "indexed" | "needs_ocr" | "failed" | "skipped"
    parser: str              # "pdf" | "docx" | "xlsx" | "pptx" | "plain" | "zip" | ...
    cache_hit: bool
    duration_ms: int
    chunks: list[EvidenceChunk]
    entries_or_pages: int
    error: str = ""

@dataclass
class EvidenceResult:
    candidates: list[AttachmentCandidate]
    parsed: list[ParsedAttachment]
    injected: list[EvidenceChunk]
    total_chars: int
    duration_ms: int
```

```python
async def build_attachment_evidence(
    session: AsyncSession, task: TaskItem, user: User,
    question: str, output_requirements: str = "",
    *, limit_docs: int = 5, limit_chunks: int = 20, limit_chars: int = 8000,
    timeout_ms: int = 15_000, cache: AttachmentCache | None = None,
    ocr: OcrAdapter | None = None,
) -> EvidenceResult
```

- 权限：复用 `agent_context.can_read_task`（403 语义不变）；只读取 `DocItem.wi == task.wi_id`、`deleted == False`、`scan != "含毒"`、`object_name` 非空的文档；「已授权上游节点附件」由调用方先行收集（复用 `_file_ids` 收集逻辑）。
- 意图选择：对每个候选附件用「文件名 + kind + 扩展名」与「question + output_requirements」做关键词/前缀匹配打分（复用 `flowhub_read_snapshot._extract_keywords` 的词法约定），`score > 0` 的附件进入解析；无命中时按 `limit_docs` 取前 N 个作兜底。
- 二次检索：`async def retrieve_more_evidence(session, task, user, query, exclude_chunk_seq, depth) -> EvidenceResult`，只对已缓存解析结果重检索，不重新下载对象；深度 > `MAX_RETRIEVAL_DEPTH` 拒绝。

#### `attachment_parsers.py` — 格式解析器注册表

```python
PARSER_VERSION = "1"          # 解析器实现版本；变更即全部缓存失效

def parse_attachment(doc: DocItem, data: bytes, ocr: OcrAdapter | None = None) -> ParsedAttachment
# 按扩展名派发：
#   .pdf  → _parse_pdf     .docx → _parse_docx   .xlsx → _parse_xlsx
#   .pptx → _parse_pptx    .txt/.md/.yaml/.yml/.json/.csv → _parse_plain
#   .zip  → _parse_zip     .png/.jpg/.jpeg/.webp → _parse_image(ocr)  # 仅 OCR 可用时
#   其他  → skipped
```

- **PDF**（`pymupdf`/`fitz`）：按页提取文本层；每页标题取页内最大字号文本行；表格摘要取页内含 `|`/制表符行；无文本层页 → 渲染为 PNG → `ocr.extract_text`（OCR 可用时），OCR 失败/超时/不可用 → 标记 `ocr_marker` chunk（全部页面无文本且 OCR 不可用 → `status="needs_ocr"`）。
- **DOCX**（`python-docx`）：段落（含标题样式 `Heading *` → kind `title`）、表格逐行摘要（kind `table`）；定位 `p{index}`。
- **XLSX**（`openpyxl`）：工作表名 → 每表前 N 行摘要 + 总行列数；定位 `Sheet!A1:C5`。
- **PPTX**（`python-pptx`）：幻灯片标题 + 文本；定位 `slide{n}`。
- **Axure ZIP**：沿用 `routes/documents.py` 的 `_list_zip_entries_checked` / `_extract_zip_checked` 安全校验（总量 ≤200MB、条目 ≤2000、防路径穿越、忽略 `__MACOSX`/`.DS_Store`、剥离单层包裹目录）。只静态读取：`index.html` 的 `<title>`、`data/*.js`/`data/*.css` 里形如 `pages:[{name, ...}]` 的页面名与页面树文本模式（不执行脚本）、以及白名单文本数据文件（`.txt/.md/.json/.csv`）。证据定位 = ZIP 内文件路径。
- **普通 ZIP**：只索引安全白名单文本条目（`.txt/.md/.yaml/.yml/.json/.csv`），不递归解压嵌套压缩包（含 `.zip/.gz/.tar/.7z` 的条目标记 `skipped` 并记录），不把二进制、可执行文件或前端脚本正文交给模型。

#### `attachment_cache.py` — 临时缓存

```python
class AttachmentCache:                    # 抽象基类
    async def get(self, key: str) -> ParsedAttachment | None
    async def set(self, key: str, parsed: ParsedAttachment) -> None
    async def clear(self) -> None

class ProcessLRUAttachmentCache(AttachmentCache):   # 默认实现：TTL LRU（maxsize=256, ttl=3600s）

def cache_key(doc: DocItem, parser_version: str) -> str
# = sha256(f"{doc.id}:{doc.version}:{doc.object_name}:{doc.size}:{doc.time}:{parser_version}")[:32]
```

- 对象指纹包含 `version/object_name/size/time`；文档被重新上传（新 id）或任一字段变化 → 键变化 → 重新解析。
- 解析结果可能较大（PDF 全部页文本）；进程内 LRU 天然避免 Redis 序列化与测试环境依赖。生产多 worker 时各进程独立缓存（可接受：缓存仅是提速，不承担一致性）。

#### `ocr.py` — OCR 适配器协议

```python
class OcrAdapter:                           # 抽象基类
    name: str = "unknown"
    available: bool = False                 # 依赖/配置是否就绪
    async def extract_text(self, image_bytes: bytes, page_no: int) -> str

class NoopOcrAdapter(OcrAdapter):           # 依赖缺失或未启用：返回 ""，表示无法提取
    available: bool = False
    async def extract_text(self, image_bytes, page_no) -> str: return ""

class RapidOcrAdapter(OcrAdapter):          # 默认引擎：rapidocr-onnxruntime 本地 CPU 推理（中英文）
    name: str = "rapidocr"
    available: bool = True
    # 惰性初始化引擎（首次调用加载模型，~1s），页数/字符/耗时在调用方按预算控制

def get_ocr_adapter() -> OcrAdapter
# RuntimeConfig["attachment.ocr.enabled"]=="true" 且可 import rapidocr → RapidOcrAdapter
# 否则 → NoopOcrAdapter（惰性，不因 import 失败抛错）
```

- OCR 仅作用于 PDF 无文本页与图片附件；PDF 解析对无文本页调用 `ocr.extract_text(渲染页 PNG, page_no)`，失败/超时/不可用 → 该页标记 `ocr_marker`。
- 页渲染：用 `pymupdf`（`fitz`) 的 `page.get_pixmap()` 将无文本层页面渲染为 PNG（dpi≈200）供 OCR；渲染/OCR 均带 页数上限（默认前 5 页）与单页超时（默认 8s/页），超限页直接标记「需 OCR」而非阻塞整体解析。


### LangGraph 集成点（Tools 驱动，不写死在业务流程）

参照现有 FlowHub Native Tools（`create_repo_tool_bundle` + `run_repo_tool_loop`）模式：

- 命名：本文以 `flowhub.attachment.*` 作为概念命名空间；实际 bind 给模型的工具 schema 名用 OpenAI 兼容、不带点号的 `flowhub_attachment_list` / `flowhub_attachment_search`（trace 展示名可带点号，仅作标签）。
- 新建 `AttachmentToolBundle(tools, traces, evidence_context)`（与 `RepoToolBundle` 同构）与 `create_attachment_tool_bundle(session, task, user) -> AttachmentToolBundle`：
  - `flowhub.attachment.list`：返回当前任务可见附件目录（名称/类型/解析状态/来源定位），只读元数据，不触发正文解析。
  - `flowhub.attachment.search(query, doc_ids?, exclude_seq?)`：对指定/全部候选附件做意图选择 → 解析/缓存命中 → 检索，返回带 `[附件@文档名:页码或路径:片段号]` 标注的证据片段；调用预算（文档 ≤3 / 片段 ≤10 / 字符 ≤4000 / 单次耗时 ≤10s）。
- `run_flowhub_tool_loop`（复用/泛化 `run_repo_tool_loop`：`bind_tools` 模型自主选择，服务器确定性控制 调用数/轮数/字符/耗时 上限与审计）执行附件工具；工具结果累计进 `bundle.evidence_context`，每附件一条 trace（选择理由/解析器/缓存命中/耗时/提取数/检索片段数/失败原因）。
- `execute_run` 在 `run.task_id` 存在时构建 `attachment_tools_factory(prompt) -> bundle`，传入 `model_node`；`model_node` 与 repo 工具同层运行附件工具循环（task 与 repo 工具可并存）。
- `model_node` system prompt 仅提示「当前任务有可调用的附件证据工具 flowhub.attachment.*，涉及附件时自行调用检索并引用 [附件@文档名:页码或路径:片段号]」——不强制每轮注入、不写死引用约定。
- 工具循环结束后，把实际调用的附件工具 trace 与检索到的证据片段汇总写入 `run.parsed["attachmentEvidence"]`（candidates/parsed/injected 结构不变，供前端展示「本轮实际使用的附件证据」）。
- 二次检索 MCP 工具（`mcp_server.py`）：`retrieve_attachment_evidence(task_id, query, exclude_chunk_seq, depth=1)`，只读，权限走既有 `can_read_task`，复用 `retrieve_more_evidence`（外部 Agent 场景），带 文档数 ≤3 / 片段 ≤10 / 字符 ≤4000 / 耗时 ≤10s / 深度 ≤3 上限。

### 质量检查

- 确定性校验（`execute_run` 成功分支，与现有 schema `not JSON` 检查同层，不改模型评审协议）：产出文本提到「附件」但本轮 `tool_trace` 中没有任何 `flowhub.attachment.*` 调用 → 标记 `needs_human_review` 并提示「产出涉及附件但未检索附件证据，需人工核对」；调用了附件工具但检索为空 → 提示「附件未解析/需人工核对」。不要求非附件产出携带引用。

### 前端任务处理界面（`frontend/src/pages/node.tsx`）

- `GET /tasks/{id}` 的 `expertRuns[].parsed` 增加 `attachmentEvidence`（由 `model_node` 工具循环结束后汇总写入），前端「Expert 产出」卡内展示：
  - 每个候选附件解析状态徽标：`未使用 / 已索引 / 缓存命中 / 需 OCR / 失败`（映射自 `parsed` 各附件 `selected/status/cache_hit`）；
  - 可展开查看本轮模型实际检索到的证据片段（`injected`，含来源定位 `doc_name:location:seq`）；
  - 失败附件显示失败原因 + 「重试解析」入口（触发 `POST /tasks/{task_id}/reparse-attachments`，只重跑解析与证据注入、不重跑模型，返回最新摘要并刷新）。
- 保留 `document-viewer-drawer.tsx` 全部现有预览能力，新增状态徽标不替代预览。

### 新增/修改 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks/{task_id}/reparse-attachments` | 仅重跑附件解析与证据注入，返回 `{attachments: [...]}` 摘要（含每个附件的 status/parser/cache_hit/duration/entries_or_pages/error 与 injected 片段）；不调用模型、不写 Run |
| GET | `/api/v1/tasks/{task_id}` | `expertRuns[].parsed.attachmentEvidence`（`model_node` 工具循环结束后汇总写入） |

## 安全约束（Security Constraints）

- 附件证据服务与既有文档权限一致：无权限 403；`deleted`/`scan=="含毒"`/`object_name` 空 → 跳过并记录原因，不进索引。
- ZIP 安全校验复用 `routes/documents.py` 既有实现（总量 ≤200MB、条目 ≤2000、防路径穿越）；本服务**不落盘解压**，仅静态读取白名单文本条目。
- 不递归解压嵌套压缩包；不把二进制、可执行文件或前端脚本正文交给模型。
- 解析预算：单附件读取字节上限（复用 `_load_document_bytes` 的 MinIO 读取）、`limit_docs/limit_chunks/limit_chars/timeout_ms` 全部硬上限；超限即降级为「部分证据」，绝不无界拉取。

## 测试计划（Test Plan）

- **解析与证据定位**：PDF 文本页、扫描 PDF 降级（无 OCR → `needs_ocr` + 提示）、DOCX/XLSX/PPTX、普通 ZIP（白名单条目 + 定位路径）、Axure ZIP（页面名/页面树 + `index.html` title）的解析与 `location` 断言。
- **安全边界**：ZIP 路径穿越、压缩炸弹（超 200MB / 超 2000 条目）、超限页数、嵌套压缩包、含毒（`scan=="含毒"`）、越权（非处理人/管理员 → 403）、`object_name` 为空 → 均不能进入索引。
- **缓存**：同一 `cache_key` 命中缓存（`cache_hit=True`）；`version/object_name/size/time` 任一变化 → 重新解析；`PARSER_VERSION` 变化 → 全部失效。
- **意图选择**：按问题关键词仅选中相关附件（`selected` + `reason`），无关附件 `skipped`。
- **工具驱动与引用**：`flowhub_attachment_list` / `flowhub_attachment_search` 由模型调用（bind_tools 自主选择）；`injected` 片段定位可回溯到页码或 ZIP 文件路径；产出提到「附件」但本轮未调用附件工具 → `needs_human_review`；`run.parsed.attachmentEvidence` 汇总 候选/解析/注入 供前端展示。
- **可观测性**：每个附件工具调用产生一条 `flowhub_attachment_*` trace（选择理由/解析器/缓存命中/耗时/提取数/检索片段数/失败原因）；`run.parsed.attachmentEvidence` 结构完整。
- **前端**：任务页附件状态徽标、证据片段展开、失败「重试解析」入口（`reparse-attachments`）与返回刷新。

