"""演示落库脚本：按《FlowHub 演示流程模板设计 v2》创建 7 个 Expert 与 4 套流程模板。

用法（后端服务运行在 127.0.0.1:8000）：
    .venv/bin/python scripts/seed_demo_flows.py [base_url]

幂等：slug/模板名已存在则跳过；可重复执行。
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TOKEN = None


def req(path, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method,
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        payload = json.loads(urllib.request.urlopen(r, timeout=120).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            message = json.loads(body).get("message", body[:200])
        except Exception:
            message = body[:200]
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {message}") from None
    if payload.get("code") != 0:
        raise RuntimeError(f"{method} {path} -> {payload}")
    return payload["data"]


def f(key, label, ftype, required=False, extra=None):
    field = {"key": key, "label": label, "type": ftype, "required": required}
    field.update(extra or {})
    return field


def acc(text, hint=""):
    return {"key": f"a{abs(hash(text)) % 100000}", "text": text, "hint": hint} if hint else {"key": f"a{abs(hash(text)) % 100000}", "text": text}


def deliverable(instruction, acceptance, ai_guidance="", example=""):
    return {"instruction": instruction, "acceptance": acceptance, "aiGuidance": ai_guidance, "example": example}


def node(nid, label, ntype, x, y, purpose, handler, sla, schema, deliverable=None, split="off", deployment=None):
    cfg = {"typeLine": f"{ntype.upper()} · 任务", "purpose": purpose, "handler": handler, "fallback": "", "sla": sla,
           "schema": schema, "output": "", "split": {"mode": split}}
    if deliverable:
        cfg["deliverable"] = deliverable
    if deployment:
        cfg["expert"] = {"expertDeploymentId": deployment}
    return {"id": nid, "label": label, "type": ntype, "x": x, "y": y, "width": 118, "height": 56, "cfg": cfg}


def plain(nid, label, ntype, x, y, purpose="", handler="系统", sla="", schema=None):
    cfg = {"typeLine": f"{ntype.upper()}", "purpose": purpose or label, "handler": handler, "fallback": "", "sla": sla,
           "schema": schema or [], "output": ""}
    return {"id": nid, "label": label, "type": ntype, "x": x, "y": y, "width": 118, "height": 56, "cfg": cfg}


# ============ Expert 团队（E1-E7） ============

EXPERTS = [
    {"slug": "requirement-analyst", "name": "需求分析专家", "desc": "把模糊需求变成结构化分析、拆分建议（演示模板1 需求分析节点）",
     "prompt": """【角色】你是资深需求分析师，擅长把业务语言转成结构化研发输入。
【输入】工作项背景（发起表单）、上游评论、知识库《需求分析 SOP》《历史需求库》。
【职责】1) 澄清背景与目标；2) 明确范围与非目标；3) 识别风险与依赖；
4) 给出可独立交付的模块拆分建议（每个子项含负责人建议与完成顺序）。
【输出契约】严格按节点表单字段输出 JSON：分析结论 / 影响范围 / 方案要点；
拆分建议以列表返回，每项含 title / note / assignee_hint。
【边界】不虚构需求细节，缺失信息写入「待澄清」字段；不做技术方案设计。"""},
    {"slug": "solution-architect", "name": "方案设计专家", "desc": "技术方案与接口契约起草（演示模板1 方案设计节点）",
     "prompt": """【角色】你是资深系统架构师。
【输入】需求分析结论（上游表单）、架构决策记录（ADR）、现有系统接口清单。
【职责】1) 技术方案选型与理由；2) 模块与接口契约设计；3) 数据模型变更；
4) 兼容性与迁移策略。
【输出契约】方案说明 / 接口契约 / 数据变更 / 迁移策略，按节点字段输出 JSON；
接口契约部分同时生成 Markdown 设计文档。
【边界】不直接产出生产代码；涉及容量与安全只给评估结论。"""},
    {"slug": "dev-coder", "name": "开发实现专家", "desc": "代码骨架/接口实现/变更说明起草（演示模板1 开发节点）",
     "prompt": """【角色】你是全栈开发工程师，遵循团队编码规范。
【输入】设计方案（上游表单）、代码库接口清单、编码规范知识库。
【职责】1) 按方案生成实现说明与关键代码骨架；2) 生成接口文档；
3) 自查清单（边界条件 / 错误处理 / 日志）。
【输出契约】实现说明 / 关键代码（Markdown）/ 接口文档（生成文档上传）/ 自查结果。
【边界】只产出建议性代码与文档，不直接提交代码库（写代码库由外部智能体 / CI 承担）。"""},
    {"slug": "qa-engineer", "name": "测试质量专家", "desc": "测试用例生成、缺陷定性、回归结论建议（演示模板1/2 测试节点）",
     "prompt": """【角色】你是测试质量工程师，掌握等价类 / 边界值 / 场景法。
【输入】需求分析与设计方案（上游表单）、缺陷描述、历史缺陷库。
【职责】1) 从需求与方案推导测试用例（正反例 + 边界）；2) 缺陷复现步骤与严重级别定性；
3) 回归范围建议与结论倾向。
【输出契约】测试用例清单（生成文档上传）/ 缺陷定性 / 回归结论建议，按节点字段输出 JSON。
【边界】不替需求方做验收；P0 级缺陷必须给出复现步骤。"""},
    {"slug": "release-ops", "name": "发布运维专家", "desc": "发布说明、上线检查清单、回滚预案（演示模板1 发布节点）",
     "prompt": """【角色】你是发布运维工程师（Release Manager）。
【输入】本工作项全链路上游表单（需求 / 方案 / 测试结论）、发布检查 SOP。
【职责】1) 汇总生成面向业务的发布说明；2) 生成上线检查清单（逐条可勾选）；
3) 生成回滚预案（触发条件 / 步骤 / 责任人占位）。
【输出契约】发布版本 / 发布说明（生成文档上传）/ 检查清单，按节点字段输出 JSON。
【边界】不执行真实发布动作；回滚预案不替代值班 SOP。"""},
    {"slug": "ticket-triage", "name": "智能分诊专家", "desc": "缺陷/工单自动分类分级、根因初判、自动拆分（演示模板2/4 分诊节点·自动接管）",
     "prompt": """【角色】你是智能分诊专家，负责缺陷与客户工单的自动分诊。
【输入】问题 / 工单描述与附件、影响模块清单、知识库《故障分类法》《历史案例》。
【职责】1) 分类（缺陷 / 工单 / 咨询）与分级（P0-P3）；2) 初步根因假设与排查建议；
3) 按影响模块给出子任务拆分方案（每项含负责人建议）；4) 引用历史相似案例。
【输出契约】严格按节点表单字段输出 JSON：分类 / 分级 / 诊断结论 / 疑似根因 /
排查建议 / 知识库引用；如节点要求自动拆分，额外输出 split 数组（每项含
title / note / assignee_hint）。
【边界】P0 级必须转人工复核；不确定时降级为 P2 并标注「待人工确认」；
不执行任何修复写入。"""},
    {"slug": "contract-reviewer", "name": "合同审查专家", "desc": "合同摘要、风险条款识别、审查报告生成（演示模板3 法务预审节点）",
     "prompt": """【角色】你是法务合规专家，专注商务合同审查。
【输入】合同文件（工作项文档）、合同要素（发起表单）、《合同风险条款库》《审查清单》。
【职责】1) 合同要素与背景摘要；2) 逐条识别风险条款（付款 / 违约 / 知识产权 / 保密 / 终止）；
3) 给出修改建议与谈判要点；4) 输出标准审查报告。
【输出契约】风险等级 / 风险条款（多选）/ 审查意见，按节点字段输出 JSON；
审查报告全文以 Markdown 输出，自动上传为工作项文档。
【边界】只做风险识别与建议，不做法律裁决；重大风险必须标注「建议外部律师意见」。"""},
]


def seed_experts(provider_model_id):
    dep_ids = {}
    existing = {e["slug"]: e["id"] for e in req("/api/v1/experts")["items"]}
    for e in EXPERTS:
        if e["slug"] in existing:
            print(f"  expert {e['slug']} 已存在，跳过创建")
            continue
        d = req("/api/v1/experts", "POST", {
            "name": e["name"], "slug": e["slug"], "description": e["desc"],
            "system_prompt": e["prompt"], "provider_model_id": provider_model_id,
        })
        eid, vid = d["expert"]["id"], d["version"]["id"]
        req(f"/api/v1/experts/{eid}/versions/{vid}/test", "POST", {"prompt": "自检", "write_intent": False})
        req(f"/api/v1/experts/{eid}/versions/{vid}/publish", "POST")
        print(f"  expert {e['slug']} 已发布")
    # Deployment（幂等：按名称查）
    deps = {d["name"]: d["id"] for d in req("/api/v1/expert-deployments")["items"]}
    for e in EXPERTS:
        dep_name = f"{e['slug']}-dep"
        if dep_name in deps:
            dep_ids[e["slug"]] = deps[dep_name]
            continue
        experts = {x["slug"]: x["id"] for x in req("/api/v1/experts")["items"]}
        d = req(f"/api/v1/experts/{experts[e['slug']]}/deployments", "POST",
                {"name": dep_name, "environment": "test", "alias": e["slug"]})
        dep_ids[e["slug"]] = d["deployment"]["id"]
        print(f"  deployment {dep_name} 已创建")
    return dep_ids


# ============ 流程模板（T1-T4） ============

def build_t1():
    nodes = [
        plain("n1", "需求提交", "start", 24, 24, "需求入口", "系统", "", [
            f("title", "需求标题", "input", True), f("description", "需求描述", "textarea", True),
            f("priority", "优先级", "radio", True, {"options": [{"label": v, "value": v.lower()} for v in ["P0", "P1", "P2", "P3"]]}),
            f("due_date", "期望上线日期", "date"),
            f("modules", "涉及模块", "multiselect", False, {"options": [{"label": m, "value": m} for m in ["前端", "后端", "数据", "文档"]]}),
        ]),
        node("n2", "需求分析", "task", 176, 24, "把业务语言转成结构化研发输入", "人工 + Expert 可协助", "24 小时",
             [f("analysis_conclusion", "分析结论", "textarea", True, {"placeholder": "背景 / 范围 / 非目标 / 关键风险"}),
              f("risk_level", "风险等级", "select", True, {"options": [{"label": "高", "value": "high"}, {"label": "中", "value": "medium"}, {"label": "低", "value": "low"}]}),
              f("analysis_report", "分析报告", "upload", True)],
             deliverable("输出需求分析结论：背景、范围、非目标、关键风险。",
                         [acc("范围与非目标明确", "对照发起表单逐条核对"), acc("关键风险已列出")],
                         "结合工作项背景补充遗漏的分析维度；结论需可直接用于拆分。"),
             split="ai_assist"),
        node("n3", "方案设计", "task", 328, 24, "技术方案与接口契约", "人工 + Expert 可协助", "24 小时",
             [f("solution", "方案说明", "textarea", True), f("api_contract", "接口契约", "upload", True),
              f("migration", "迁移策略", "textarea")],
             deliverable("技术选型与理由 / 模块与接口契约 / 数据变更与迁移策略。",
                         [acc("接口契约覆盖全部影响模块"), acc("有回滚或兼容策略")]),
             deployment="__E2__"),
        plain("n4", "技术评审", "decision", 480, 24, "评审方案，通过后进入并行开发", "架构师", "24 小时",
              [f("verdict", "评审结论", "radio", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "打回", "value": "reject"}]}),
               f("opinion", "评审意见", "textarea", True)]),
        node("n5", "前端开发", "task", 632, 24, "前端实现", "人工 + Expert 可协助", "48 小时",
             [f("fe_impl", "实现说明", "textarea", True), f("fe_components", "组件清单", "textarea"),
              f("fe_deliverable", "页面交付物", "upload", True)],
             deliverable("实现覆盖设计契约，含自查清单。", [acc("实现覆盖设计契约"), acc("自查清单完成")]),
             deployment="__E3__"),
        node("n6", "后端开发", "task", 632, 118, "后端实现", "人工 + Expert 可协助", "48 小时",
             [f("be_impl", "实现说明", "textarea", True), f("be_api_doc", "接口文档", "upload", True)],
             deliverable("接口实现与文档齐备。", [acc("接口文档与实现一致")]),
             deployment="__E3__"),
        node("n7", "测试", "task", 480, 118, "测试验证", "人工 + Expert 可协助", "48 小时",
             [f("test_verdict", "测试结论", "select", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "部分通过", "value": "partial"}, {"label": "失败", "value": "fail"}]}),
              f("defect_note", "缺陷说明", "textarea"), f("test_report", "测试报告", "upload", True)],
             deliverable("用例正反例 + 边界覆盖。", [acc("用例覆盖全部分支"), acc("缺陷有复现步骤")]),
             deployment="__E4__"),
        node("n8", "验收", "acceptance", 328, 118, "需求方验收", "需求提出方", "24 小时",
             [f("accept_verdict", "验收结论", "radio", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "不通过", "value": "fail"}]}),
              f("accept_note", "备注", "textarea")]),
        node("n9", "发布交付", "task", 176, 118, "发布说明与检查清单", "人工 + Expert 可协助", "24 小时",
             [f("release_version", "发布版本", "input", True),
              f("release_env", "目标环境", "select", True, {"options": [{"label": "生产", "value": "prod"}, {"label": "预发", "value": "staging"}]}),
              f("release_note", "发布说明", "upload", True), f("rollback", "回滚预案", "upload", True)],
             deliverable("面向业务的发布说明 + 上线检查清单 + 回滚预案。",
                         [acc("发布说明面向业务可读"), acc("回滚预案三要素齐全")]),
             deployment="__E5__"),
        plain("n10", "完成", "end", 24, 118),
    ]
    edges = [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n4", "n5"], ["n4", "n6"], ["n5", "n7"], ["n6", "n7"], ["n7", "n8"], ["n8", "n9"], ["n9", "n10"]]
    return {"name": "软件需求交付流程（AI 专家版）", "nodes": nodes, "edges": edges, "fallbacks": [["n4", "n2"], ["n8", "n7"]]}


def build_t2():
    nodes = [
        plain("n1", "问题登记", "start", 24, 24, "缺陷入口", "系统", "", [
            f("title", "问题标题", "input", True),
            f("severity", "严重程度", "radio", True, {"options": [{"label": v, "value": v.lower()} for v in ["P0", "P1", "P2", "P3"]]}),
            f("description", "问题描述", "textarea", True),
            f("modules", "影响模块", "multiselect", False, {"options": [{"label": m, "value": m} for m in ["模块A", "模块B", "其他"]]}),
            f("env", "发生环境", "select", False, {"options": [{"label": "生产", "value": "prod"}, {"label": "测试", "value": "test"}, {"label": "预发布", "value": "staging"}]}),
            f("screenshot", "截图 / 日志", "upload"),
        ]),
        node("n2", "智能分诊", "task", 176, 24, "自动分类分级、根因初判与拆分", "Expert 自动", "15 分钟",
             [f("triage_category", "问题分类", "select", True, {"options": [{"label": "缺陷", "value": "bug"}, {"label": "工单", "value": "ticket"}, {"label": "咨询", "value": "consult"}]}),
              f("triage_priority", "分诊级别", "select", True, {"options": [{"label": "P0", "value": "P0"}, {"label": "P1", "value": "P1"}, {"label": "P2", "value": "P2"}, {"label": "P3", "value": "P3"}]}),
              f("diagnosis", "诊断结论", "textarea", True),
              f("root_cause", "疑似根因", "textarea"),
              f("kb_refs", "知识库引用", "input")],
             deliverable("分类分级 / 根因假设 / 排查建议 / 相似案例引用。",
                         [acc("P0 附复现步骤"), acc("拆分粒度到模块")],
                         "P0 必须转人工复核；不确定时降级 P2。"),
             split="ai_auto", deployment="__E6__"),
        plain("n3", "人工复核", "decision", 328, 24, "复核 AI 分诊结论与拆分", "技术负责人", "4 小时",
              [f("review_verdict", "复核结论", "radio", True, {"options": [{"label": "内部修复", "value": "fix"}, {"label": "转外部智能体", "value": "external"}, {"label": "误报关闭", "value": "close"}]}),
               f("review_note", "复核意见", "textarea")]),
        node("n4", "修复任务", "task", 480, 24, "修复缺陷", "人工 + Expert 可协助", "24 小时",
             [f("fix_note", "修复说明", "textarea", True), f("verify_steps", "验证步骤", "textarea", True)],
             deliverable("修复说明含根因；验证步骤可执行。", [acc("修复说明含根因"), acc("验证步骤可执行")]),
             deployment="__E3__"),
        plain("n5", "外部智能体接力", "task", 480, 118, "外部智能体经 MCP 获取上下文并处理回传", "外部智能体（演示）", "24 小时",
              [f("ext_result", "处理结果", "textarea", True), f("ext_feedback", "回传说明", "textarea")]),
        node("n6", "回归验证", "acceptance", 632, 70, "问题提出人验证", "问题提出方", "24 小时",
             [f("regress_verdict", "验证结论", "radio", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "未修复", "value": "fail"}]}),
              f("regress_note", "备注", "textarea")]),
        plain("n7", "关闭复盘", "closure", 784, 70, "关闭确认与复盘", "运维负责人", "8 小时",
              [f("close_confirm", "关闭确认", "radio", True, {"options": [{"label": "确认关闭", "value": "ok"}]}),
               f("retro", "复盘要点", "textarea")]),
        plain("n8", "结束", "end", 900, 70),
    ]
    edges = [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n3", "n5"], ["n3", "n8"], ["n4", "n6"], ["n5", "n6"], ["n6", "n7"], ["n7", "n8"]]
    return {"name": "缺陷智能处理流程（AI 自动分诊）", "nodes": nodes, "edges": edges, "fallbacks": [["n6", "n4"], ["n7", "n4"]]}


def build_t3():
    nodes = [
        plain("n1", "合同发起", "start", 24, 24, "合同入口", "系统", "", [
            f("contract_name", "合同名称", "input", True),
            f("contract_type", "合同类型", "select", True, {"options": [{"label": v, "value": v} for v in ["采购", "销售", "服务", "保密"]]}),
            f("amount_tier", "金额档位", "select", True, {"options": [{"label": "100 万以内", "value": "le100"}, {"label": "100 万以上", "value": "gt100"}]}),
            f("party", "对方单位", "input", True),
            f("deadline", "合同期限", "date", True),
            f("contract_file", "合同文件", "upload", True),
            f("urgent", "紧急程度", "radio", False, {"options": [{"label": "普通", "value": "normal"}, {"label": "加急", "value": "rush"}]}),
        ]),
        node("n2", "法务预审", "task", 176, 24, "风险条款识别与审查报告", "人工 + Expert 可协助", "24 小时",
             [f("risk_level", "风险等级", "radio", True, {"options": [{"label": "高", "value": "high"}, {"label": "中", "value": "medium"}, {"label": "低", "value": "low"}]}),
              f("risk_clauses", "风险条款", "multiselect", True, {"options": [{"label": v, "value": v} for v in ["付款", "违约", "知识产权", "保密", "终止"]]}),
              f("review_opinion", "审查意见", "textarea", True),
              f("review_report", "审查报告", "upload", True)],
             deliverable("合同摘要 / 逐条风险识别（付款/违约/知产/保密/终止）/ 修改建议 / 标准审查报告。",
                         [acc("五类风险逐项核对"), acc("重大风险标注外部律师意见")],
                         "读取合同文件与条款库；报告全文输出 Markdown。"),
             deployment="__E7__"),
        plain("n3", "部门负责人审批", "task", 328, 24, "业务审批", "部门负责人", "24 小时",
              [f("approve_verdict", "审批结论", "radio", True, {"options": [{"label": "同意", "value": "agree"}, {"label": "退回", "value": "return"}]}),
               f("opinion", "审批意见", "textarea", True)]),
        plain("n4", "金额决策", "decision", 480, 24, "金额超过 100 万加签总经理", "系统", "2 小时",
              [f("amount_confirm", "金额档位确认", "select", True, {"options": [{"label": "100 万以上", "value": "gt100"}, {"label": "100 万以内", "value": "le100"}]})]),
        plain("n4b", "总经理加签", "task", 480, 118, "大额合同加签", "总经理", "24 小时",
              [f("countersign", "加签结论", "radio", True, {"options": [{"label": "同意", "value": "agree"}, {"label": "不同意", "value": "reject"}]}),
               f("cs_opinion", "加签意见", "textarea")]),
        plain("n5", "财务审批", "task", 632, 24, "付款与预算", "财务人员", "24 小时",
              [f("payment_confirm", "付款条款确认", "radio", True, {"options": [{"label": "确认", "value": "ok"}, {"label": "需调整", "value": "adjust"}]}),
               f("budget_confirm", "预算匹配", "radio", True, {"options": [{"label": "匹配", "value": "ok"}, {"label": "不匹配", "value": "mismatch"}]})]),
        plain("n6", "法务终审", "task", 784, 24, "法务终审", "法务负责人", "24 小时",
              [f("final_verdict", "终审结论", "radio", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "打回", "value": "return"}]})]),
        plain("n7", "归档", "closure", 900, 24, "合同归档", "合同管理员", "8 小时",
              [f("archive_no", "归档编号", "input", True), f("archive_note", "归档备注", "textarea")]),
        plain("n8", "结束", "end", 900, 118),
    ]
    edges = [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n4", "n4b"], ["n4", "n5"], ["n4b", "n5"], ["n5", "n6"], ["n6", "n7"], ["n7", "n8"]]
    return {"name": "合同审批流程（AI 审查版）", "nodes": nodes, "edges": edges, "fallbacks": [["n3", "n1"], ["n6", "n2"]]}


def build_t4():
    nodes = [
        plain("n1", "工单登记", "start", 24, 24, "工单入口", "系统", "", [
            f("ticket_title", "工单标题", "input", True),
            f("customer", "客户名称", "input", True),
            f("description", "问题描述", "textarea", True),
            f("category", "问题分类", "select", True, {"options": [{"label": v, "value": v} for v in ["使用咨询", "故障", "数据问题", "投诉"]]}),
            f("urgency", "紧急程度", "radio", True, {"options": [{"label": "紧急", "value": "urgent"}, {"label": "普通", "value": "normal"}]}),
        ]),
        node("n2", "智能分诊", "task", 176, 24, "自动分类分级与拆分子工单", "Expert 自动", "30 分钟",
             [f("triage_category", "工单分类", "select", True, {"options": [{"label": "使用咨询", "value": "consult"}, {"label": "故障", "value": "fault"}, {"label": "数据问题", "value": "data"}, {"label": "投诉", "value": "complaint"}]}),
              f("triage_priority", "优先级", "select", True, {"options": [{"label": "紧急", "value": "urgent"}, {"label": "高", "value": "high"}, {"label": "普通", "value": "normal"}]}),
              f("triage_note", "分诊结论", "textarea", True),
              f("kb_refs", "相似案例引用", "input")],
             deliverable("分类 / 分级 / 根因初判 / 相似案例引用。",
                         [acc("多问题工单按问题项拆分"), acc("每条子工单有责任人建议")]),
             split="ai_auto", deployment="__E6__"),
        node("n3", "专项处理", "task", 328, 24, "按知识库方案处理", "人工 + Expert 可协助", "24 小时",
             [f("solution", "处理方案", "textarea", True), f("result", "处理结果", "textarea", True)],
             deliverable("方案引用知识库案例；处理结果客户可读。", [acc("方案引用知识库案例"), acc("处理结果客户可读")]),
             deployment="__E6__"),
        plain("n4", "客户确认", "acceptance", 480, 24, "客户确认解决", "客户 / 客户成功", "24 小时",
              [f("confirm_verdict", "确认结论", "radio", True, {"options": [{"label": "已解决", "value": "solved"}, {"label": "未解决", "value": "unsolved"}]}),
               f("confirm_note", "备注", "textarea")]),
        plain("n5", "满意度回访", "closure", 632, 24, "满意度回访", "客户成功", "8 小时",
              [f("satisfaction", "满意度", "select", True, {"options": [{"label": v, "value": v} for v in ["5", "4", "3", "2", "1"]]}),
               f("visit_note", "回访记录", "textarea")]),
        plain("n6", "结束", "end", 784, 24),
    ]
    edges = [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n4", "n5"], ["n5", "n6"]]
    return {"name": "客户售后工单流程（AI 分诊版）", "nodes": nodes, "edges": edges, "fallbacks": [["n4", "n3"]]}


TEMPLATES = [
    ("__T1__", build_t1),
    ("__T2__", build_t2),
    ("__T3__", build_t3),
    ("__T4__", build_t4),
]

EXPERT_FOR = {"__E1__": "requirement-analyst", "__E2__": "solution-architect", "__E3__": "dev-coder",
              "__E4__": "qa-engineer", "__E5__": "release-ops", "__E6__": "ticket-triage", "__E7__": "contract-reviewer"}


def main():
    global TOKEN
    TOKEN = req("/api/v1/auth/login", "POST", {"account": "admin", "password": "Admin@123456"})["token"]
    providers = req("/api/v1/providers")["items"]
    model_id = None
    for p in providers:
        if p["status"] == "healthy" and p["credential"] == "configured":
            entries = p.get("modelEntries") or []
            if entries:
                model_id = entries[0]["id"]
                break
    if not model_id:
        raise SystemExit("没有健康且已配置凭据的 Provider 模型：请先在 Provider 中心配置")
    print(f"provider model: {model_id}")
    print("== 1/2 Expert 团队 ==")
    dep_ids = seed_experts(model_id)
    print("== 2/2 流程模板 ==")
    existing_names = {t["name"] for t in req("/api/v1/templates/pool")["items"]}
    for _, builder in TEMPLATES:
        payload = builder()
        if payload["name"] in existing_names:
            print(f"  template {payload['name']} 已存在，跳过")
            continue
        for n in payload["nodes"]:
            cfg = n["cfg"]
            exp = (cfg.get("expert") or {}).get("expertDeploymentId")
            if exp and exp.startswith("__"):
                cfg["expert"] = {"expertDeploymentId": dep_ids[EXPERT_FOR[exp]]}
        d = req("/api/v1/templates", "POST", {"name": payload["name"], "type": "requirement"})
        tpl_id = d["template"]["id"]
        r = req(f"/api/v1/templates/{tpl_id}/versions/save-and-publish", "POST", payload)
        print(f"  template {payload['name']} 已发布：{r.get('message')}")


if __name__ == "__main__":
    main()
