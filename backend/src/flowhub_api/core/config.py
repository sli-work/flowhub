"""应用配置：全部来自环境变量 / .env（pydantic-settings）。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    debug: bool = True
    secret_key: str = "change-me"
    access_token_expire_minutes: int = 720
    cors_origins: str = "http://localhost:5173,http://localhost:5273"
    public_base_url: str = ""  # 企业微信/钉钉 OAuth 回调所使用的 HTTPS 公网地址

    # PostgreSQL
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "flowhub"
    postgres_password: str = "flowhub"
    postgres_db: str = "flowhub"
    database_url: str = ""

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_url: str = ""

    # MinIO
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "flowhub"
    minio_secret_key: str = "flowhub-minio"
    minio_bucket: str = "flowhub-docs"
    minio_secure: bool = False

    # 管理员引导
    bootstrap_admin_account: str = "admin"
    bootstrap_admin_password: str = "Admin@123456"
    bootstrap_admin_name: str = "系统管理员"

    # Provider API Key 加密（Fernet key 派生；留空回退 secret_key）
    agent_key_encrypt_secret: str = ""

    # Graphify：绑定仓库的本地 AST 代码图谱（仅分析镜像工作树，不访问外网）
    graphify_enabled: bool = True
    graphify_command: str = "graphify"
    graphify_timeout_seconds: int = 120
    graphify_max_workers: int = 2
    graphify_max_prompt_chars: int = 6000

    # ---------- 通知渠道（docs/06）：钉钉 / 企微机器人 webhook + 邮件 SMTP ----------
    # 留空 = 该渠道未启用（通知中心渠道健康检查会显示"未配置"）
    dingtalk_webhook: str = ""      # 钉钉群机器人 webhook 地址
    dingtalk_secret: str = ""       # 钉钉机器人加签密钥（可选）
    dingtalk_agent_id: str = ""     # 钉钉内部应用 AgentId（点对点工作通知）
    wecom_webhook: str = ""         # 企业微信群机器人 webhook 地址
    wecom_agent_id: str = ""        # 企业微信自建应用 AgentId（点对点应用消息）
    wecom_app_secret: str = ""      # 企业微信自建应用 Secret（不要复用通讯录 Secret）
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""             # 发件人，如 FlowHub <no-reply@example.com>

    # ---------- 组织同步（docs/02 §1.3）：钉钉 / 企微组织 API 凭证 ----------
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    wecom_corp_id: str = ""
    wecom_corp_secret: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_dsn(self) -> str:
        if self.redis_url:
            return self.redis_url
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
