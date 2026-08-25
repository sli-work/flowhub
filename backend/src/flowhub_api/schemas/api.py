"""Pydantic 请求/响应模型（对齐前端字段名）。"""
from pydantic import BaseModel, Field


# ---------- 认证 ----------
class LoginReq(BaseModel):
    account: str
    password: str


class SsoVerifyReq(BaseModel):
    provider: str = Field(pattern=r"^(dingtalk|wecom)$")
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=4096)


class RegisterReq(BaseModel):
    account: str = Field(min_length=3, max_length=64)
    name: str
    email: str
    dept: str = ""
    role_id: str
    password: str = Field(min_length=8, max_length=64)


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


# ---------- 组织 / 用户 ----------
class UserUpdateReq(BaseModel):
    dept: str | None = None
    status: str | None = None
    roles: list[str] | None = None
    skills: list[str] | None = None


class CreateUserReq(BaseModel):
    account: str = Field(min_length=3, max_length=64)
    name: str
    email: str = ""
    dept: str = ""
    role_id: str
    skills: list[str] = []
    password: str = Field(min_length=8, max_length=64)


class RoleUpsertReq(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=48)
    label: str
    desc: str = ""
    copy_from: str | None = None
    perms: dict[str, bool] | None = None
    members: list[str] | None = None


# ---------- 节点绑定 ----------
class NodeAssignmentReq(BaseModel):
    node_id: str
    node_label: str = ""
    users: list[str] = []
    roles: list[str] = []


class TemplateBindingReq(BaseModel):
    template_id: str
    version: str = "v1"
    status: str = "active"
    assignments: list[NodeAssignmentReq] = []


class CreateTemplateReq(BaseModel):
    """新建流程模板（docs/02 §四）：名称 + 类型线（类型由系统默认，前端无需选择）。"""
    name: str
    type: str = "requirement"  # requirement / issue / change
    desc: str = ""


class ProjectUpsertReq(BaseModel):
    name: str
    code: str
    status: str = "draft"
    desc: str = ""
    manager: str = ""
    template_bindings: list[TemplateBindingReq] = []


# ---------- 工作项 / 任务 ----------
class CreateWorkItemReq(BaseModel):
    project_id: str
    template_id: str
    start_values: dict


class TaskActionReq(BaseModel):
    action: str  # submit | return | transfer | claim | request_info | pause | cancel
    node_id: str | None = None
    form_values: dict | None = None
    to_node_id: str | None = None
    to_user_id: str | None = None
    reason: str | None = None


# ---------- Agent ----------
class AgentRegisterReq(BaseModel):
    name: str
    desc: str = ""
    scope: str = ""
    capabilities: list[str] = []
    # 新链路：工具+类型（第一类/第二类配置）
    tool_id: str = ""                 # 客户端工具（agent_tools.id）；空=旧逻辑
    agent_type: str = ""              # 类型 code（带出系统提示词模板）
    system_prompt: str = ""           # 默认系统提示词（描述即提示词）
    # 旧链路字段（无 tool_id 时兼容；api 引擎必填）
    engine: str = "opencode"          # opencode（CLI 引擎）/ api（API Key 直连）
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class AgentUpdateReq(BaseModel):
    name: str
    agent_type: str = ""
    provider: str
    model: str
    system_prompt: str = ""


class AgentToolReq(BaseModel):
    name: str
    engine: str = "opencode"          # opencode（CLI 引擎）/ api（API Key 直连）
    provider: str = ""
    model: str = ""
    models: list[str] = []
    base_url: str = ""
    api_key: str = ""
    desc: str = ""


class TestConnectionReq(BaseModel):
    base_url: str
    api_key: str
    model: str


class OpencodeApplyReq(BaseModel):
    provider: str
    api_key: str
    kind: str = "api"          # api / oauth
    base_url: str = ""         # OpenAI 兼容端点（自定义 provider 必填）
    models: list[str] = []     # 该 provider 下模型列表（可空=用内置列表）


class AgentAuthorizeReq(BaseModel):
    user_ids: list[str] = []
    capabilities: dict[str, str] | None = None  # {name: mode}


class AgentBindReq(BaseModel):
    scope: str = ""
    bindings: str = ""
    capabilities: dict[str, str] | None = None


class AgentConfirmReq(BaseModel):
    action: str
    op_scope: str = ""
    expire_minutes: int = 120


class AgentInvokeReq(BaseModel):
    capability: str
    provider: str = ""
    model: str = ""
    action: str = ""
    prompt: str = ""
    task_id: str | None = None
    node_id: str | None = None
    wi_id: str | None = None
    op_scope: str = ""
    expire_minutes: int = 120


class ConfirmDecisionReq(BaseModel):
    note: str = ""


class AgentModelReq(BaseModel):
    provider: str
    model: str
    label: str = ""
    desc: str = ""
    base_url: str = ""   # OpenAI 兼容 endpoint（含 /v1）


class AgentTypeReq(BaseModel):
    code: str
    label: str
    desc: str = ""
    default_caps: dict = {}
    system_prompt: str = ""
    provider: str = ""
    model: str = ""
