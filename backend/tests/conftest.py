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


@pytest.fixture
async def seeded():
    """任务 + 工作项 + 6 个附件（含含毒/已删除/无对象）的 DB 场景，供附件证据测试共用。"""
    from sqlalchemy import select

    from flowhub_api.db.session import SessionFactory
    from flowhub_api.models import User
    from flowhub_api.models.support import DocItem
    from flowhub_api.models.workflow import TaskItem, WorkItem

    async with SessionFactory() as session:
        wi = WorkItem(id="WI-EVID-001", type="issue", title="备件库存看板", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-EVID-001", wi_id=wi.id, title="分析备件库存", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        docs = [
            DocItem(id="evd1", name="Q2服务复盘.pdf", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t1", wi=wi.id, object_name="d1/Q2.pdf"),
            DocItem(id="evd2", name="备件清单.xlsx", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t2", wi=wi.id, object_name="d2/list.xlsx"),
            DocItem(id="evd3", name="无关文档.txt", project="售后", scan="已扫描", uploader="李四",
                    size="1KB", time="t3", wi=wi.id, object_name="d3/x.txt"),
            DocItem(id="evd4", name="病毒文件.zip", project="售后", scan="含毒", uploader="王五",
                    size="1KB", time="t4", wi=wi.id, object_name="d4/v.zip"),
            DocItem(id="evd5", name="已删除.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t5", wi=wi.id, object_name="d5/d.txt", deleted=True),
            DocItem(id="evd6", name="无对象.txt", project="售后", scan="已扫描", uploader="张三",
                    size="1KB", time="t6", wi=wi.id, object_name=None),
        ]
        session.add(wi); session.add(task); session.add_all(docs)
        await session.commit()
        admin = (await session.execute(select(User).where(User.account == "liting"))).scalars().first()
        yield session, task, admin, docs
        # 清理固定 ID 行，避免后续用例相同主键冲突（admin 为 seed 用户，不删）
        for d in docs:
            await session.delete(d)
        await session.delete(task)
        await session.delete(wi)
        await session.commit()
