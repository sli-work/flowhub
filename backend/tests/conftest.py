"""pytest 全局 fixture：独立测试库（flowhub_test）+ TestClient（触发 lifespan 建表/seed）。

设计要点：
- 环境变量必须在导入 flowhub_api 之前设置（core.config.get_settings 有 lru_cache）。
- 每次会话开始前 DROP SCHEMA 重置测试库，保证用例可重复运行、互不污染。
- TestClient 的 with 语法触发 FastAPI lifespan：create_all + migrate + seed（FLOWHUB_SEED_DEMO=1 灌演示数据）。
"""
from __future__ import annotations

import os

_TEST_DB_HOST = os.environ.get("FLOWHUB_TEST_DB_HOST", "192.168.21.4")
os.environ.setdefault("DATABASE_URL", f"postgresql+psycopg://flowhub:flowhub123@{_TEST_DB_HOST}:5432/flowhub_test")
os.environ.setdefault("FLOWHUB_SEED_DEMO", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-please-change")
os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

import psycopg  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from flowhub_api.main import app  # noqa: E402

# 演示 seed 账号（seed/data.py SEED_USERS）：密码统一 Demo@1234
# bootstrap admin：admin / Admin@123456
DEMO_PASSWORD = "Demo@1234"
ADMIN_PASSWORD = "Admin@123456"


def _reset_test_db() -> None:
    """重建测试库 schema（会话开始时调用一次）。"""
    conn = psycopg.connect(f"postgresql://flowhub:flowhub123@{_TEST_DB_HOST}:5432/flowhub_test", autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def _db_reset() -> None:
    """每个 pytest 会话开始前重置测试库（保证 seed 幂等、用例可重复）。"""
    _reset_test_db()
    yield


@pytest.fixture(scope="session")
def client() -> TestClient:
    """TestClient：with 触发 lifespan（建表 + migrate + seed 演示数据）。"""
    with TestClient(app) as c:
        yield c


def login(client: TestClient, account: str, password: str = DEMO_PASSWORD) -> str:
    """登录并返回 token（断言失败时给出响应体便于定位）。"""
    resp = client.post("/api/v1/auth/login", json={"account": account, "password": password})
    assert resp.status_code == 200, f"login({account}) failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- 常用角色 token（session 级，一次登录全局复用） ----------
@pytest.fixture(scope="session")
def admin_token(client: TestClient) -> str:
    return login(client, "admin", ADMIN_PASSWORD)


@pytest.fixture(scope="session")
def org_admin_token(client: TestClient) -> str:
    """李婷：organization_admin + project_admin（可管组织/角色/项目/模板/Agent）。"""
    return login(client, "liting")


@pytest.fixture(scope="session")
def leader_token(client: TestClient) -> str:
    """张伟：after_sales + leader（可建项目、看板、审计，但无用户/角色管理权）。"""
    return login(client, "zhang.wei")


@pytest.fixture(scope="session")
def dev_token(client: TestClient) -> str:
    """孙琳：developer + qa 技能（基础执行角色，无管理权）。"""
    return login(client, "sunlin")


@pytest.fixture(scope="session")
def pm_token(client: TestClient) -> str:
    """吴凡：product_manager。"""
    return login(client, "wufan")


@pytest.fixture(scope="session")
def admin_headers(admin_token: str) -> dict[str, str]:
    return auth_headers(admin_token)


@pytest.fixture(scope="session")
def org_headers(org_admin_token: str) -> dict[str, str]:
    return auth_headers(org_admin_token)


@pytest.fixture(scope="session")
def leader_headers(leader_token: str) -> dict[str, str]:
    return auth_headers(leader_token)


@pytest.fixture(scope="session")
def dev_headers(dev_token: str) -> dict[str, str]:
    return auth_headers(dev_token)
