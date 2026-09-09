"""reparse-attachments 接口：失败重试解析入口。"""
import pytest

from flowhub_api.db.session import SessionFactory
from flowhub_api.models.support import DocItem
from flowhub_api.models.workflow import TaskItem, WorkItem
from flowhub_api.services import attachment_evidence as svc


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema_and_seed(client):
    """触发 lifespan（建表 + seed demo 用户），供直接操作 SessionFactory 的用例使用。"""
    return client


@pytest.fixture
async def seeded_task_with_doc():
    async with SessionFactory() as session:
        wi = WorkItem(id="WI-REP-001", type="issue", title="备件盘点", project="售后",
                      assignee="张三", creator="张三")
        task = TaskItem(id="T-REP-001", wi_id=wi.id, title="分析备件盘点", project="售后",
                        node="分析", node_id="n1", type="issue", assignee="张三")
        doc = DocItem(id="d-rep", name="备件清单.txt", project="售后", scan="已扫描",
                      uploader="张三", size="1KB", time="t1", wi=wi.id, object_name="d-rep/x.txt")
        session.add(wi); session.add(task); session.add(doc)
        wi.start_values = {"attach": [{"id": doc.id, "name": doc.name}]}
        await session.commit()
        yield task.id, doc.id
        # 清理固定 ID 行，避免后续用例相同主键冲突
        await session.delete(doc)
        await session.delete(task)
        await session.delete(wi)
        await session.commit()


def test_reparse_attachments_indexes(client, org_headers, seeded_task_with_doc, monkeypatch):
    task_id, _doc_id = seeded_task_with_doc
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "备件缺货 120 单\n补货周期 7 天".encode())

    resp = client.post(f"/api/v1/tasks/{task_id}/reparse-attachments", headers=org_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["attachments"], "应返回附件解析摘要"
    assert data["attachments"][0]["status"] == "indexed"
    assert data["attachments"][0]["parser"] == "plain"
    assert any("备件缺货" in c["text"] for c in data["injected"])
    assert any(c["location"].startswith("p") for c in data["injected"])


def test_reparse_attachments_denied_for_outsider(client, leader_headers, seeded_task_with_doc, monkeypatch):
    task_id, _doc_id = seeded_task_with_doc
    monkeypatch.setattr(svc, "_load_bytes", lambda doc: "内容".encode())
    resp = client.post(f"/api/v1/tasks/{task_id}/reparse-attachments", headers=leader_headers)
    assert resp.status_code == 403, resp.text
