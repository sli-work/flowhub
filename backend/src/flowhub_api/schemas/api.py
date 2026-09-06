"""Pydantic 请求/响应模型（对齐前端字段名）。"""
from typing import Literal

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


class ThemePreferenceReq(BaseModel):
    theme: Literal["dark", "light"]


# ---------- 组织 / 用户 ----------
class UserUpdateReq(BaseModel):
    dept: str | None = None
    email: str | None = None
    name: str | None = None
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
    labels: list[str] = []  # 预定义标签（/api/v1/tags 登记后才可绑定）


class TaskActionReq(BaseModel):
    action: str  # submit | return | transfer | claim | request_info | pause | cancel
    node_id: str | None = None
    form_values: dict | None = None
    to_node_id: str | None = None
    to_user_id: str | None = None
    reason: str | None = None
    # 提交时验收清单勾选快照 {key: {text, checked}}（有验收标准的节点强制全部勾选）
    acceptance_checks: dict = {}


class TaskAdoptRunReq(BaseModel):
    """采纳指定 Expert Run 的产出回填节点表单。
    normalize=true 时先做 AI 二次格式修正（失败自动降级原解析）；
    values 非空时为用户在抽屉中人工修改后的产出，直接覆盖 parsed 快照（跳过 AI 格式修正）。"""
    run_id: str = Field(min_length=1)
    normalize: bool = False
    values: dict | None = None
    approve_quality_override: bool = False


class TaskAiFillReq(BaseModel):
    """Expert 协助填充/重新执行：context 为用户补充的执行上下文（可选，≤2000 字）。"""
    context: str = Field(default="", max_length=2000)


class TaskSplitChild(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    note: str = ""
    assignee: str = ""
    due_hours: int = 48


class TaskSplitReq(BaseModel):
    children: list[TaskSplitChild] = Field(min_length=1, max_length=10)
    # 拆分时父任务尚未走 submit 动作，前端须显式带回当前编辑中的表单内容。
    form_values: dict | None = None


# ---------- Expert Runtime ----------
class ModelLimitsReq(BaseModel):
    max_context_tokens: int | None = Field(default=None, ge=8000, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=1_000_000)


class ProviderReq(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = ""
    models: list[str] = []
    model_limits: dict[str, ModelLimitsReq] = Field(default_factory=dict)
    max_context_tokens: int = Field(default=32_000, ge=8_000, le=10_000_000)  # 模型最大上下文窗口（token），压缩预算取 80%


class ProviderTestReq(BaseModel):
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=128)


class ExpertCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,95}$")
    description: str = ""
    system_prompt: str = ""
    provider_model_id: str = ""
    model: str = ""
    skills: list[str] = []
    knowledge_base_ids: list[str] = []
    tool_policies: dict[str, str] = {}


class ExpertVersionReq(BaseModel):
    description: str = ""
    system_prompt: str = ""
    provider_model_id: str = ""
    model: str = ""
    skills: list[str] = []
    knowledge_base_ids: list[str] = []
    tool_policies: dict[str, str] = {}


class DeploymentReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    environment: str = Field(pattern=r"^(test|prod)$")
    alias: str = ""


class ExpertRunReq(BaseModel):
    deployment_id: str = ""
    prompt: str = Field(min_length=1)
    write_intent: bool = False


class McpToolReq(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    input_schema: dict = {}
    risk: str = "read"
    approval: str = "none"


class McpServerReq(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    direction: str = "outbound"
    transport: str = "streamable-http"
    endpoint: str = ""
    auth_type: str = "none"
    credentials: str = ""
    tools: list[McpToolReq] = []


class McpToolUpdateReq(BaseModel):
    status: str | None = None
    enabled: bool | None = None
    risk: str | None = None
    approval: str | None = None


class ApprovalDecisionReq(BaseModel):
    note: str = ""


class ExpertChatSessionReq(BaseModel):
    deployment_id: str = ""
    provider_model_id: str = ""
    version_id: str = ""  # 绑定指定 Expert Version（编辑器草稿测试会话），deployment 存在时忽略
    project_name: str = ""  # 绑定项目（存名称）：注入项目绑定仓库的代码上下文
    title: str = "新会话"


class ExpertChatSessionRenameReq(BaseModel):
    """会话更新：title 重命名；expert_id/provider_model_id/project_name 变更绑定（传入则更新，空串清除绑定）。"""
    title: str | None = Field(default=None, min_length=1, max_length=160)
    expert_id: str = ""
    provider_model_id: str = ""
    project_name: str = ""


class ExpertChatMessageReq(BaseModel):
    content: str = Field(min_length=1)
    write_intent: bool = False
    # 文件是显式副作用：默认只在会话中回答，只有用户在界面勾选后才落库为可下载文档。
    create_file: bool = False
    quality_mode: Literal["fast", "balanced", "accurate"] = "balanced"


# ---------- 代码仓库（连接 / 绑定） ----------
class RepoConnectionCreateReq(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: str = Field(pattern=r"^(github|gitlab)$")
    base_url: str = ""  # 自建 GitLab 必填（如 https://gitlab.example.com）
    token: str = Field(min_length=8, max_length=512)


class RepoConnectionUpdateReq(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str = ""
    token: str | None = Field(default=None, min_length=8, max_length=512)


class RepoBindingCreateReq(BaseModel):
    connection_id: str
    provider_repo_id: str = ""  # 数字 id 优先；为空时用 full_name 定位
    full_name: str = ""
    role: str = Field(default="main", pattern=r"^(main|docs|service|lib)$")
    default_branch: str = ""


class RepoBindingUpdateReq(BaseModel):
    role: str | None = Field(default=None, pattern=r"^(main|docs|service|lib)$")
    default_branch: str | None = None
