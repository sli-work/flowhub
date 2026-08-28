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
    ("expert:read", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("expert:create", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("expert:update", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("expert:publish", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("expert:deploy", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("expert:run", [1, 1, 1, 1, 1, 1, 1, 1, 1]),
    ("expert:approve", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
    ("expert:delete", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("expert:resource_manage", [1, 1, 1, 0, 0, 0, 0, 0, 0]),
    ("expert:runtime_read", [1, 1, 1, 1, 0, 0, 0, 0, 0]),
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
