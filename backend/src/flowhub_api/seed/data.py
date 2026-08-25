"""Seed 数据：9 内置角色 + 34 权限点矩阵 + 示例用户 + 全局模板（对齐 docs/03 与前端 mock）。"""

# 角色顺序与前端 matrixRoles / matrixRows cells 列顺序一致
ROLE_ORDER = [
    "system_admin", "organization_admin", "project_admin", "leader",
    "product_manager", "developer", "after_sales", "pre_sales", "second_line",
]

ROLE_META = {
    "system_admin": ("系统管理员", "全平台最高权限，管理组织与系统配置；防自锁保护"),
    "organization_admin": ("组织管理员", "组织用户 / 角色 / 同步与审计管理"),
    "project_admin": ("项目管理员", "项目管理、成员管理与模板发布"),
    "leader": ("领导", "组织级看板与全局数据范围"),
    "product_manager": ("产品经理", "需求流程主导与验收"),
    "developer": ("开发", "研发节点处理（技能标签细分 backend/frontend/qa）"),
    "after_sales": ("售后", "问题提报、分诊与闭环确认"),
    "pre_sales": ("售前", "方案导入与需求前置"),
    "second_line": ("二线", "问题深度排查与根因分析"),
}

# 34 权限点矩阵（cells 顺序 = ROLE_ORDER）
PERM_MATRIX: list[tuple[str, list[bool]]] = [
    ("organization:user_manage", [1, 1, 0, 0, 0, 0, 0, 0, 0]),
    ("organization:role_manage", [1, 1, 0, 0, 0, 0, 0, 0, 0]),
    ("project:create", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
    ("project:read", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("project:update", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
    ("project:archive", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("project:member_manage", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_template:create", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_template:read", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("workflow_template:update", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_template:review", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_template:publish", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_template:archive", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("workflow_instance:create", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("workflow_instance:read", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("workflow_instance:pause", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
    ("workflow_instance:cancel", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("task:claim", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("task:submit", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("task:return", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("task:transfer", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("document:upload", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("document:read", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("document:download", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("document:delete", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("document:restore", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("agent:register", [1, 1, 0, 0, 0, 0, 0, 0, 0]),
    ("agent:bind", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("agent:invoke", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("agent:authorize", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("agent:revoke", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("audit:read", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
    ("audit:export", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("dashboard:read", [1, 1, 0, 1, 1, 0, 0, 0, 0]),
]

# 防自锁：system_admin 不可被降权的权限点
ANTI_LOCK_PERMS = {"organization:user_manage"}

# 示例用户（对齐前端 mock users，密码统一 Demo@1234，后续可改）
SEED_USERS = [
    ("u1", "张伟", "zhang.wei", "售后服务部 / 深圳二部", ["after_sales", "leader"], [], "g1", "active", "ding_zw", "wx_zw", 4),
    ("u2", "李婷", "liting", "平台研发部 / 平台组", ["organization_admin", "project_admin"], ["backend", "devops"], "g6", "active", "ding_lt", "wx_lt", 2),
    ("u3", "孙琳", "sunlin", "质量保障部 / 测试组", ["developer"], ["qa", "frontend"], "g3", "active", None, "wx_sl", 5),
    ("u4", "陈新", "chenxin", "售前方案部 / 华南区", ["pre_sales"], [], "g2", "active", "ding_cx", None, 1),
    ("u5", "王强", "wangqiang", "平台研发部 / 二线组", ["second_line"], ["backend"], "g4", "active", None, "wx_wq", 3),
    ("u6", "赵岩", "zhaoyan", "平台研发部 / 后端组", ["developer"], ["backend"], "g5", "active", None, None, 2),
    ("u7", "何静", "hejing", "质量保障部 / 测试组", ["developer"], ["qa"], "g3", "active", None, None, 2),
    ("u8", "孙磊", "sunlei", "平台研发部 / 前端组", ["developer"], ["frontend"], "g6", "disabled", None, None, 0),
    ("u9", "吴凡", "wufan", "产品中心 / 产品组", ["product_manager"], [], "g2", "active", None, None, 6),
    ("u10", "钱多多", "qiandd", "售后服务部 / 上海一部", ["after_sales"], [], "g1", "active", None, None, 3),
    ("u11", "周敏", "zhoumin", "质量保障部 / 测试组", ["developer"], ["qa", "backend"], "g4", "locked", None, None, 0),
    ("u12", "郑直", "zhengzhi", "平台研发部 / 后端组", ["developer"], ["backend", "devops"], "g5", "active", None, None, 4),
]

# 全局模板（对齐前端 globalTemplates）
GLOBAL_TEMPLATES = [
    {
        "id": "tpl-req", "name": "需求流程", "type": "requirement", "versions": ["v1", "v2", "v3", "v4"],
        "nodes": [
            {"id": "n1", "label": "需求提交", "type": "start"}, {"id": "n2", "label": "需求分析", "type": "task"},
            {"id": "n3", "label": "产品评审", "type": "task"}, {"id": "n4", "label": "需求拆分", "type": "task"},
            {"id": "n5", "label": "后端开发", "type": "task"}, {"id": "n6", "label": "前端开发", "type": "task"},
            {"id": "n7", "label": "测试", "type": "task"}, {"id": "n8", "label": "产品验收", "type": "acceptance"},
            {"id": "n9", "label": "发布交付", "type": "task"}, {"id": "n10", "label": "完成", "type": "end"},
        ],
    },
    {
        "id": "tpl-issue", "name": "问题流程", "type": "issue", "versions": ["v1", "v2"],
        "nodes": [
            {"id": "i1", "label": "问题提报", "type": "start"}, {"id": "i2", "label": "分诊", "type": "task"},
            {"id": "i3", "label": "二线分析", "type": "task"}, {"id": "i4", "label": "开发排查", "type": "task"},
            {"id": "i5", "label": "修复", "type": "task"}, {"id": "i6", "label": "验证", "type": "task"},
            {"id": "i7", "label": "售后确认", "type": "acceptance"}, {"id": "i8", "label": "关闭", "type": "end"},
        ],
    },
]

# 可配置模型（创建 Agent 时按厂商分组选择；base_url 为 OpenAI 兼容 endpoint，api 引擎直连用）
AGENT_MODELS = [
    {"provider": "OpenAI", "model": "gpt-4o-mini", "label": "GPT-4o mini",
     "base_url": "https://api.openai.com/v1",
     "desc": "OpenAI 通用小模型：速度快、成本低，适合摘要与常规分析"},
    {"provider": "OpenAI", "model": "gpt-4o", "label": "GPT-4o",
     "base_url": "https://api.openai.com/v1",
     "desc": "OpenAI 旗舰多模态模型：复杂推理与长文本能力强"},
    {"provider": "DeepSeek", "model": "deepseek-chat", "label": "DeepSeek Chat",
     "base_url": "https://api.deepseek.com/v1",
     "desc": "DeepSeek 官方对话模型：中文理解强，性价比高"},
    {"provider": "DeepSeek", "model": "deepseek-reasoner", "label": "DeepSeek Reasoner",
     "base_url": "https://api.deepseek.com/v1",
     "desc": "DeepSeek 推理模型：复杂逻辑与数学问题表现好"},
    {"provider": "通义", "model": "qwen-plus", "label": "通义千问 Plus",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "desc": "阿里通义千问平衡型模型：综合能力均衡"},
    {"provider": "通义", "model": "qwen-max", "label": "通义千问 Max",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "desc": "阿里通义千问最强模型：复杂任务首选"},
    {"provider": "Kimi", "model": "moonshot-v1-8k", "label": "Kimi 8K",
     "base_url": "https://api.moonshot.cn/v1",
     "desc": "月之暗面 Kimi：长上下文与中文对话表现好"},
    {"provider": "豆包", "model": "doubao-pro-32k", "label": "豆包 Pro 32K",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "desc": "字节豆包 Pro：生成类任务响应快、成本低"},
    {"provider": "智谱", "model": "glm-4-flash", "label": "GLM-4 Flash",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "desc": "智谱 GLM-4 Flash：免费额度友好，轻量任务适用"},
    # 以下两条仅服务 opencode CLI 引擎（base_url 留空，无需 API Key）
    {"provider": "OpenCode Go", "model": "go-sonnet", "label": "Go Sonnet（OpenCode Go）",
     "base_url": "", "desc": "OpenCode Go 托管模型（需 opencode CLI 登录）"},
    {"provider": "mcai", "model": "mcai-chat", "label": "MCAI Chat",
     "base_url": "", "desc": "MCAI 对话模型（需 opencode CLI 登录）"},
]

# Agent 业务角色类型（第二类 Agent 配置：类型→系统提示词模板；default_caps 取 14 项能力子集）
AGENT_TYPES = [
    {"code": "data_analysis", "label": "数据分析", "desc": "读取上下文/文档，产出数据分析结论与风险提示",
     "default_caps": {"read_context": "direct", "read_documents": "direct", "read_history": "direct", "generate_content": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的数据分析 Agent。\n职责：基于任务上下文与关联文档，进行数据分析，输出结论、数据要点与风险提示。\n输出要求：结论先行，给出关键数据与依据，必要时列出风险与建议；直接输出最终结果，不要过程性对话。"},
    {"code": "test_case", "label": "测试用例", "desc": "按验收标准生成测试用例草稿",
     "default_caps": {"read_context": "direct", "read_documents": "direct", "generate_content": "confirm", "write_form": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的测试用例 Agent。\n职责：根据需求上下文、验收标准与历史节点结论，生成测试用例草稿，覆盖正常、边界与异常场景。\n输出要求：用例编号、前置条件、步骤、预期结果结构化输出；直接输出最终结果。"},
    {"code": "code_review", "label": "代码审查", "desc": "审查实现说明与交付物，输出审查意见",
     "default_caps": {"read_context": "direct", "read_history": "direct", "generate_content": "confirm", "write_form": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的代码审查 Agent。\n职责：审查实现说明与交付物，检查与需求/验收标准的符合性，输出审查意见。\n输出要求：按问题严重度分类（阻断/建议/提示），每条附依据；直接输出最终结果。"},
    {"code": "doc_generate", "label": "文档生成", "desc": "生成需求/接口/测试文档草稿",
     "default_caps": {"read_context": "direct", "read_documents": "direct", "generate_content": "confirm", "upload_document": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的文档生成 Agent。\n职责：根据需求与上下文生成需求/接口/测试文档草稿，结构清晰、表述准确。\n输出要求：按文档章节组织内容，术语一致，直接输出最终文档文本。"},
    {"code": "notify_probe", "label": "通知探测", "desc": "检查通知渠道状态与失败重试",
     "default_caps": {"read_context": "direct", "read_history": "direct", "generate_content": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的通知探测 Agent。\n职责：检查通知渠道（企微/邮件等）状态与失败重试情况，输出渠道健康结论与建议。\n输出要求：渠道维度列出状态、失败项与重试建议；直接输出最终结果。"},
    {"code": "summary", "label": "摘要", "desc": "汇总节点上下文生成摘要",
     "default_caps": {"read_context": "direct", "read_history": "direct", "generate_content": "confirm"},
     "system_prompt": "你是 FlowHub 流程协同平台中的摘要 Agent。\n职责：汇总当前任务与历史节点上下文，生成简洁摘要，突出关键结论与待办。\n输出要求：要点式输出，100 字内概述 + 关键条目列表；直接输出最终结果。"},
]

# 客户端工具配置（第一类）：Agent 执行客户端。目前仅内置 opencode（CLI 引擎，容器内置免 API Key）；
# 用户可在页面新增 API 直连工具（选厂商/模型/API Key）。
AGENT_TOOLS = [
    {"name": "opencode", "engine": "opencode", "desc": "opencode CLI 引擎：容器内置，使用容器内 opencode 登录态与模型配置，免 API Key。"},
]
