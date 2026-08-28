"""演示业务数据（迁移自前端 mock.ts）：项目/工作项/任务/文档/Agent/通知/审计/模板起始表单。

删除前端 mock 后，页面数据统一来自后端 seed，保证演示不中断。
"""

# ---------- 项目（含模板绑定 + 节点处理人） ----------
DEMO_PROJECTS = [
    {
        "id": "p1", "name": "订单中心重构", "code": "ORDER", "status": "active",
        "desc": "订单全生命周期重构：分派、履行、售后闭环", "members": 14, "work_items": 86,
        "progress": 72, "manager": "李婷", "owner": "平台研发部", "updated": "08-16",
        "bindings": [
            {"template_id": "tpl-req", "version": "v3", "status": "active",
             "assignments": [
                 {"node_id": "n2", "node_label": "需求分析", "users": ["u9"], "roles": ["product_manager"]},
                 {"node_id": "n4", "node_label": "需求拆分", "users": ["u6"], "roles": ["developer"]},
                 {"node_id": "n5", "node_label": "后端开发", "users": ["u6", "u12"], "roles": ["developer"]},
                 {"node_id": "n6", "node_label": "前端开发", "users": ["u8"], "roles": ["developer"]},
                 {"node_id": "n7", "node_label": "测试", "users": ["u3", "u7"], "roles": ["qa"]},
                 {"node_id": "n8", "node_label": "产品验收", "users": ["u9"], "roles": ["product_manager"]},
                 {"node_id": "n9", "node_label": "发布交付", "users": ["u2"], "roles": ["project_admin"]},
             ]},
            {"template_id": "tpl-issue", "version": "v2", "status": "active", "assignments": []},
        ],
    },
    {"id": "p2", "name": "工单平台", "code": "TICKET", "status": "active",
     "desc": "售后工单协同：去重、分诊、SLA 与 AI 辅助", "members": 22, "work_items": 143,
     "progress": 58, "manager": "吴凡", "owner": "产品中心", "updated": "08-16",
     "bindings": [
         {"template_id": "tpl-req", "version": "v3", "status": "active", "assignments": []},
         {"template_id": "tpl-issue", "version": "v2", "status": "active", "assignments": []},
     ]},
    {"id": "p3", "name": "通知服务", "code": "NOTIF", "status": "active",
     "desc": "钉钉 / 企微 / 邮件 / 站内多渠道通知中台", "members": 8, "work_items": 41,
     "progress": 85, "manager": "郑直", "owner": "平台研发部", "updated": "08-15",
     "bindings": [
         {"template_id": "tpl-req", "version": "v3", "status": "active", "assignments": []},
     ]},
    {"id": "p4", "name": "报表服务", "code": "REPORT", "status": "paused",
     "desc": "经营报表与导出能力（暂停：等待数据源评审）", "members": 9, "work_items": 32,
     "progress": 45, "manager": "王强", "owner": "平台研发部", "updated": "08-12",
     "bindings": [{"template_id": "tpl-issue", "version": "v2", "status": "active", "assignments": []}]},
    {"id": "p5", "name": "文档服务", "code": "DOCSVC", "status": "active",
     "desc": "MinIO 文件管理、短时链接与敏感级别策略", "members": 6, "work_items": 28,
     "progress": 63, "manager": "郑直", "owner": "平台研发部", "updated": "08-14",
     "bindings": [
         {"template_id": "tpl-req", "version": "v3", "status": "active", "assignments": []},
     ]},
    {"id": "p6", "name": "Agent 平台", "code": "AGENT", "status": "active",
     "desc": "外部 AI Agent 接入、授权与节点能力边界", "members": 10, "work_items": 37,
     "progress": 51, "manager": "李婷", "owner": "平台研发部", "updated": "08-16",
     "bindings": [
         {"template_id": "tpl-req", "version": "v3", "status": "active", "assignments": []},
     ]},
    {"id": "p7", "name": "客户门户 v2", "code": "PORTAL", "status": "completed",
     "desc": "客户自助查询与问题提报门户", "members": 12, "work_items": 96,
     "progress": 100, "manager": "陈新", "owner": "售前方案部", "updated": "08-01",
     "bindings": [
         {"template_id": "tpl-req", "version": "v2", "status": "active", "assignments": []},
         {"template_id": "tpl-issue", "version": "v1", "status": "active", "assignments": []},
     ]},
    {"id": "p8", "name": "旧结算系统迁移", "code": "SETTLE", "status": "cancelled",
     "desc": "结算引擎迁移（已取消：业务口径变更）", "members": 5, "work_items": 18,
     "progress": 20, "manager": "赵岩", "owner": "平台研发部", "updated": "07-28",
     "bindings": [{"template_id": "tpl-req", "version": "v1", "status": "disabled", "assignments": []}]},
    {"id": "p9", "name": "历史数据归档", "code": "ARCHIVE", "status": "archived",
     "desc": "2025 年度数据归档与冷存储（只读）", "members": 4, "work_items": 66,
     "progress": 100, "manager": "卫岚", "owner": "平台研发部", "updated": "2025-12-30",
     "read_only": True, "bindings": []},
]

# ---------- 工作项 + 流程实例 ----------
DEMO_WORK_ITEMS = [
    {"id": "REQ-2026-0241", "type": "requirement", "title": "订单中心：周末值班分派规则优化", "project": "订单中心",
     "priority": "P1", "status": "in_progress", "assignee": "张伟", "creator": "李婷", "due": "08-18",
     "labels": ["分派规则", "值班"], "progress": "测试", "start_values": {"title": "订单中心：周末值班分派规则优化"}},
    {"id": "REQ-2026-0238", "type": "requirement", "title": "售后工单 SLA 看板（二期）", "project": "订单中心",
     "priority": "P1", "status": "in_progress", "assignee": "吴凡", "creator": "吴凡", "due": "08-20",
     "labels": ["看板", "SLA"], "progress": "需求分析", "start_values": {"title": "售后工单 SLA 看板（二期）"}},
    {"id": "REQ-2026-0251", "type": "requirement", "title": "工单去重：同客户同问题合并", "project": "工单平台",
     "priority": "P2", "status": "in_progress", "assignee": "孙琳", "creator": "钱多多", "due": "08-25",
     "labels": ["去重", "AI"], "progress": "前端开发", "start_values": {"title": "工单去重：同客户同问题合并"}},
    {"id": "ISSUE-2026-0520", "type": "issue", "title": "生产环境：导出报表偶发超时 30s+", "project": "报表服务",
     "priority": "P0", "status": "waiting_for_information", "assignee": "王强", "creator": "钱多多", "due": "08-16",
     "labels": ["性能", "导出"], "progress": "二线分析", "start_values": {"title": "生产环境：导出报表偶发超时 30s+"}},
    {"id": "ISSUE-2026-0509", "type": "issue", "title": "企微通知未送达：任务到达无提醒", "project": "通知服务",
     "priority": "P1", "status": "closed", "assignee": "何静", "creator": "张伟", "due": "08-14",
     "labels": ["通知", "企微"], "progress": "关闭", "start_values": {"title": "企微通知未送达：任务到达无提醒"}},
    {"id": "REQ-2026-0247", "type": "requirement", "title": "验收意见支持模板化常用语", "project": "工单平台",
     "priority": "P3", "status": "draft", "assignee": "—", "creator": "张伟", "due": "09-01",
     "labels": ["验收"], "progress": "未提交", "start_values": {"title": "验收意见支持模板化常用语"}},
    {"id": "ISSUE-2026-0517", "type": "issue", "title": "文件预览偶现 403：短时链接过期", "project": "文档服务",
     "priority": "P2", "status": "resolved", "assignee": "郑直", "creator": "陈新", "due": "08-15",
     "labels": ["文件", "链接"], "progress": "验证", "start_values": {"title": "文件预览偶现 403：短时链接过期"}},
    {"id": "REQ-2026-0255", "type": "requirement", "title": "Agent 操作确认记录导出", "project": "Agent 平台",
     "priority": "P2", "status": "submitted", "assignee": "赵岩", "creator": "李婷", "due": "08-28",
     "labels": ["Agent", "审计"], "progress": "需求提交", "start_values": {"title": "Agent 操作确认记录导出"}},
]

# ---------- 任务（挂在工作项下） ----------
DEMO_TASKS = [
    {"id": "T-2026-0912", "wi": "REQ-2026-0241", "title": "订单中心：周末值班分派规则优化", "project": "订单中心",
     "node": "测试", "node_id": "n7", "type": "requirement", "priority": "P1", "status": "in_progress",
      "assignee": "张伟", "due": "08-18 10:00", "sla": 48, "expert_pending": True},
    {"id": "T-2026-0913", "wi": "ISSUE-2026-0520", "title": "生产环境：导出报表偶发超时 30s+", "project": "报表服务",
     "node": "二线分析", "node_id": "i3", "type": "issue", "priority": "P0", "status": "waiting_for_information",
     "assignee": "张伟", "due": "08-16 12:00", "sla": 24, "overdue": True},
    {"id": "T-2026-0914", "wi": "REQ-2026-0238", "title": "售后工单 SLA 看板（二期）", "project": "订单中心",
     "node": "需求分析", "node_id": "n2", "type": "requirement", "priority": "P1", "status": "assigned",
     "assignee": "张伟", "due": "08-20 18:00", "sla": 24},
    {"id": "T-2026-0915", "wi": "REQ-2026-0251", "title": "工单去重：同客户同问题合并", "project": "工单平台",
     "node": "前端开发", "node_id": "n6", "type": "requirement", "priority": "P2", "status": "transferred",
     "assignee": "张伟", "due": "08-25 18:00", "sla": 72, "source": "李婷转办"},
    {"id": "T-2026-0916", "wi": "REQ-2026-0247", "title": "验收意见支持模板化常用语", "project": "工单平台",
     "node": "需求提交", "node_id": "n1", "type": "requirement", "priority": "P3", "status": "assigned",
     "assignee": "张伟", "due": "09-01 18:00", "sla": 48},
    {"id": "T-2026-0917", "wi": "REQ-2026-0255", "title": "Agent 操作确认记录导出", "project": "Agent 平台",
     "node": "需求提交", "node_id": "n1", "type": "requirement", "priority": "P2", "status": "pending_confirmation",
      "assignee": "张伟", "due": "08-28 18:00", "sla": 48, "expert_pending": True},
    {"id": "T-2026-0918", "wi": "ISSUE-2026-0517", "title": "文件预览偶现 403：短时链接过期", "project": "文档服务",
     "node": "验证", "node_id": "i6", "type": "issue", "priority": "P2", "status": "accepted",
     "assignee": "郑直", "due": "08-15 18:00", "sla": 24},
]

# ---------- 文档 ----------
DEMO_DOCUMENTS = [
    {"id": "d1", "name": "需求文档_v3.pdf", "project": "订单中心", "version": "v3", "level": "L2", "scan": "已扫描",
     "uploader": "李婷", "size": "2.4MB", "time": "08-15 16:20", "kind": "需求文档", "wi": "REQ-2026-0241"},
    {"id": "d2", "name": "分派规则设计.md", "project": "订单中心", "version": "v2", "level": "L2", "scan": "已扫描",
     "uploader": "赵岩", "size": "86KB", "time": "08-15 15:47", "kind": "设计文档", "wi": "REQ-2026-0241"},
    {"id": "d3", "name": "测试报告_v1.4.pdf", "project": "订单中心", "version": "v1.4", "level": "L1", "scan": "已扫描",
     "uploader": "孙琳", "size": "1.2MB", "time": "08-16 18:02", "kind": "测试结果", "wi": "REQ-2026-0241"},
    {"id": "d4", "name": "原型_分派规则优化.fig", "project": "订单中心", "version": "v1", "level": "L3", "scan": "扫描中",
     "uploader": "吴凡", "size": "8.6MB", "time": "08-15 10:11", "kind": "原型", "wi": "REQ-2026-0241"},
    {"id": "d5", "name": "监控指标截图_08-15.png", "project": "报表服务", "version": "v1", "level": "L1", "scan": "已扫描",
     "uploader": "王强", "size": "312KB", "time": "08-15 22:40", "kind": "截图", "wi": "ISSUE-2026-0520"},
    {"id": "d6", "name": "导出日志导出_全量.zip", "project": "报表服务", "version": "v1", "level": "L2", "scan": "含毒",
     "uploader": "王强", "size": "45MB", "time": "08-16 09:12", "kind": "日志", "wi": "ISSUE-2026-0520"},
    {"id": "d7", "name": "接口文档_openapi.yaml", "project": "工单平台", "version": "v4", "level": "L2", "scan": "已扫描",
     "uploader": "郑直", "size": "128KB", "time": "08-16 14:33", "kind": "接口文档", "wi": "REQ-2026-0251"},
    {"id": "d8", "name": "去重算法评审记录.pdf", "project": "工单平台", "version": "v1", "level": "L3", "scan": "已扫描",
     "uploader": "吴凡", "size": "680KB", "time": "08-14 17:05", "kind": "测试用例", "wi": "REQ-2026-0251"},
    {"id": "d9", "name": "问题报告_通知未送达.docx", "project": "通知服务", "version": "v2", "level": "L2", "scan": "已扫描",
     "uploader": "张伟", "size": "240KB", "time": "08-12 11:28", "kind": "问题报告", "wi": "ISSUE-2026-0509"},
    {"id": "d10", "name": "分派规则伪代码.txt", "project": "订单中心", "version": "v1", "level": "L2", "scan": "已扫描",
     "uploader": "钱多多", "size": "8KB", "time": "08-15 15:47", "kind": "代码片段", "wi": "REQ-2026-0241"},
]

# ---------- 通知 ----------
DEMO_NOTIFICATIONS = [
    {"id": "n1", "title": "任务到达", "body": "「测试」节点任务已分配给你 — REQ-2026-0241", "time": "今日 11:02",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "unread": True, "kind": "arrive", "target": "张伟"},
    {"id": "n2", "title": "Agent 确认请求", "body": "FlowBot-DA 请求提交分析结果 — 需你确认", "time": "今日 11:02",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": False}], "unread": True, "kind": "agent", "target": "张伟"},
    {"id": "n3", "title": "任务转办", "body": "REQ-2026-0251 已转办给你 — 前端开发", "time": "今日 11:48",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "unread": True, "kind": "transfer", "target": "张伟"},
    {"id": "n4", "title": "补充信息请求", "body": "ISSUE-2026-0520 请求你补充监控指标", "time": "今日 11:20",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "unread": True, "kind": "info", "target": "张伟"},
    {"id": "n5", "title": "SLA 超时", "body": "REQ-2026-0241「测试」节点已超时 1.5h", "time": "今日 10:45",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "kind": "timeout", "target": "张伟"},
    {"id": "n6", "title": "任务退回", "body": "REQ-2026-0241 已从「测试」回退到「需求拆分」", "time": "08-16 12:04",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "kind": "return", "target": "张伟"},
    {"id": "n7", "title": "流程完成", "body": "ISSUE-2026-0509 问题流程已完成闭环", "time": "昨日 10:15",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "kind": "complete", "target": "张伟"},
    {"id": "n8", "title": "发送失败", "body": "任务转办通知（企微渠道）第 2 次重试失败", "time": "08-16 16:20",
     "channels": [{"name": "企微", "ok": False}], "kind": "fail", "failed": True, "retries": 2, "target": "张伟"},
    {"id": "n9", "title": "发送失败", "body": "Agent 确认请求通知（企微渠道）重试中", "time": "今日 11:05",
     "channels": [{"name": "企微", "ok": False}], "kind": "fail", "failed": True, "retries": 1, "unread": True, "target": "张伟"},
    {"id": "n10", "title": "密码即将过期", "body": "你的本地密码将于 3 天后过期，请及时修改", "time": "08-15 09:00",
     "channels": [{"name": "站内", "ok": True}], "kind": "info", "target": "张伟"},
    {"id": "n11", "title": "组织同步完成", "body": "钉钉组织同步：新增 3 人 / 变更 2 人 / 停用 1 人", "time": "08-17 09:58",
     "channels": [{"name": "站内", "ok": True}], "kind": "info", "target": ""},
    {"id": "n12", "title": "流程取消", "body": "REQ-2026-0222 需求流程已取消（业务口径变更）", "time": "08-14 15:30",
     "channels": [{"name": "钉钉", "ok": True}, {"name": "企微", "ok": True}], "kind": "complete", "target": ""},
]

# ---------- 审计 ----------
DEMO_AUDITS = [
    {"time": "08-17 10:21:33", "actor": "张伟", "actor_type": "user", "action": "task:claim", "target": "T-2026-0912 · 测试节点", "result": "success", "req_id": "req_f2a9", "ip": "10.2.1.88"},
    {"time": "08-17 10:21:34", "actor": "FlowBot-DA", "actor_type": "agent", "action": "agent:generate_content", "target": "REQ-2026-0238 · 需求分析", "result": "success", "req_id": "req_7c1e", "ip": "10.2.1.102"},
    {"time": "08-17 10:15:02", "actor": "孙磊", "actor_type": "user", "action": "task:claim", "target": "T-2026-0907 · 前端开发", "result": "denied", "req_id": "req_b8d3", "ip": "10.2.1.77"},
    {"time": "08-17 10:05:47", "actor": "何静", "actor_type": "user", "action": "task:submit", "target": "ISSUE-2026-0509 · 验证", "result": "success", "req_id": "req_9e0c", "ip": "10.2.1.65"},
    {"time": "08-17 09:58:07", "actor": "Connector", "actor_type": "system", "action": "org:sync", "target": "钉钉组织同步 · 变更 3 / 停用 1", "result": "success", "req_id": "req_a3d1", "ip": "127.0.0.1"},
    {"time": "08-16 18:02:11", "actor": "孙琳", "actor_type": "user", "action": "document:upload", "target": "测试报告_v1.4.pdf · REQ-2026-0241", "result": "success", "req_id": "req_5b2f", "ip": "10.2.1.90"},
    {"time": "08-16 16:20:45", "actor": "Worker", "actor_type": "system", "action": "notification:retry", "target": "task_transferred · 企微渠道", "result": "failed", "req_id": "req_d4e7", "ip": "127.0.0.1"},
    {"time": "08-16 16:12:03", "actor": "张伟", "actor_type": "user", "action": "task:transfer", "target": "REQ-2026-0251 → 孙琳", "result": "success", "req_id": "req_1c8a", "ip": "10.2.1.88"},
    {"time": "08-16 12:04:22", "actor": "孙琳", "actor_type": "user", "action": "task:return", "target": "REQ-2026-0241 测试 → 需求拆分", "result": "success", "req_id": "req_6f3b", "ip": "10.2.1.90"},
    {"time": "08-16 11:30:18", "actor": "Legacy-OCR", "actor_type": "agent", "action": "agent:upload_document", "target": "文档服务 · 上传节点", "result": "failed", "req_id": "req_9a2e", "ip": "10.2.1.110"},
    {"time": "08-15 22:40:56", "actor": "王强", "actor_type": "user", "action": "document:download", "target": "监控指标截图_08-15.png · 短时链接", "result": "success", "req_id": "req_8b4e", "ip": "10.2.1.70"},
    {"time": "08-15 15:47:31", "actor": "李婷", "actor_type": "user", "action": "workflow_template:edit", "target": "需求流程 v4 草稿 · 节点「测试」", "result": "success", "req_id": "req_2d5c", "ip": "10.2.1.31"},
    {"time": "08-15 09:12:44", "actor": "Worker", "actor_type": "system", "action": "file:scan", "target": "导出日志导出_全量.zip · 检出病毒", "result": "failed", "req_id": "req_c0f9", "ip": "127.0.0.1"},
    {"time": "08-14 15:30:08", "actor": "李婷", "actor_type": "user", "action": "workflow_instance:cancel", "target": "REQ-2026-0222 · 业务口径变更", "result": "success", "req_id": "req_7a6b", "ip": "10.2.1.31"},
]

# ---------- 模板起始表单（startSchema） ----------
REQ_START_SCHEMA = [
    {"key": "title", "label": "需求标题", "type": "input", "required": True, "placeholder": "一句话描述需求", "hint": "全局唯一，重复提交将被去重拦截"},
    {"key": "description", "label": "需求描述", "type": "textarea", "required": True, "placeholder": "需求背景与内容详述"},
    {"key": "background", "label": "背景", "type": "textarea", "required": False},
    {"key": "goal", "label": "目标", "type": "textarea", "required": False},
    {"key": "scope", "label": "范围", "type": "textarea", "required": False},
    {"key": "reqDoc", "label": "需求文档", "type": "upload", "required": False, "hint": "支持 PDF / Markdown / Office"},
    {"key": "prototype", "label": "原型", "type": "file", "required": False},
]

ISSUE_START_SCHEMA = [
    {"key": "title", "label": "问题标题", "type": "input", "required": True, "placeholder": "一句话描述问题"},
    {"key": "occurredAt", "label": "发生时间", "type": "date", "required": True},
    {"key": "env", "label": "客户 / 环境", "type": "select", "required": True,
     "options": [{"label": "生产环境 · 华南区", "value": "prod-south"}, {"label": "生产环境 · 华东区", "value": "prod-east"}, {"label": "预发环境", "value": "staging"}]},
    {"key": "severity", "label": "严重程度", "type": "radio", "required": True,
     "options": [{"label": "P0 · 紧急", "value": "P0"}, {"label": "P1 · 高", "value": "P1"}, {"label": "P2 · 中", "value": "P2"}, {"label": "P3 · 低", "value": "P3"}]},
    {"key": "reproduce", "label": "复现步骤", "type": "textarea", "required": True, "placeholder": "步骤 / 频次 / 触发条件"},
    {"key": "expected", "label": "期望行为", "type": "textarea", "required": True},
    {"key": "actual", "label": "实际行为", "type": "textarea", "required": True},
    {"key": "logs", "label": "日志 / 截图", "type": "file", "required": False, "hint": "可多选附件"},
]

CHANGE_START_SCHEMA = [
    {"key": "title", "label": "变更标题", "type": "input", "required": True, "placeholder": "如：数据库连接池参数调整"},
    {"key": "reason", "label": "变更原因", "type": "textarea", "required": True},
    {"key": "impact", "label": "影响范围", "type": "multiselect", "required": True,
     "options": [{"label": "订单中心", "value": "order"}, {"label": "工单平台", "value": "ticket"}, {"label": "通知服务", "value": "notif"}, {"label": "报表服务", "value": "report"}, {"label": "文档服务", "value": "docs"}]},
    {"key": "owner", "label": "申请人", "type": "input", "required": True, "placeholder": "变更申请人"},
    {"key": "planTime", "label": "期望实施时间", "type": "date", "required": True},
    {"key": "plan", "label": "实施方案 / 回滚预案", "type": "upload", "required": True, "hint": "必填产出物"},
]
