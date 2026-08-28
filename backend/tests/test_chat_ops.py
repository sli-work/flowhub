"""aichat 会话常规操作（自动压缩 / 清空 / 删除）测试。"""
import uuid

from fastapi.testclient import TestClient

from flowhub_api.services.expert_runtime import FULL_ROUNDS, build_session_history, context_budget_bytes, session_history_preview


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _mk_session(client: TestClient, headers: dict) -> str:
    r = client.post("/api/v1/expert-chat/sessions", headers=headers, json={"title": _uniq("会话")})
    assert r.status_code == 200, r.text
    return r.json()["data"]["session"]["id"]


def test_short_history_no_compact():
    rows = [(i, ("user" if i % 2 == 0 else "assistant"), f"第{i}条内容") for i in range(1, 7)]
    text, summary, marker, compacted = build_session_history(rows, budget_bytes=context_budget_bytes())
    assert compacted is False and marker == 0 and summary == ""
    assert "第1条内容" in text and "第6条内容" in text


def test_budget_derived_from_max_context_tokens():
    # 默认 100 万窗口 × 80% × 3 字节/token
    assert context_budget_bytes() == int(1_000_000 * 0.8 * 3)
    assert context_budget_bytes(200_000) == int(200_000 * 0.8 * 3)


def test_long_history_compacts_once_and_advances_marker():
    # 小预算显式触发压缩（模拟超预算场景）
    small_budget = 100_000  # 100KB
    rows = []
    seq = 1
    for i in range(30):
        rows.append((seq, "user", f"第{i}轮问题" + "长" * 7000)); seq += 1
        rows.append((seq, "assistant", f"第{i}轮回答" + "答" * 7000)); seq += 1
    total_bytes = sum(len(c.encode("utf-8")) for _, _, c in rows)
    assert total_bytes > small_budget
    text1, summary1, marker1, compacted1 = build_session_history(rows, budget_bytes=small_budget)
    assert compacted1 is True and marker1 > 0 and summary1
    assert "早期对话" in text1 and "近期对话" in text1
    last_q = rows[-2][2]
    assert last_q[:40] in text1  # 最近一轮保留全文
    # 增量语义：同一批消息再次调用（带已固化摘要与标记）→ 早期不再重复压缩
    text2, summary2, marker2, compacted2 = build_session_history(rows, summary=summary1, marker=marker1, budget_bytes=small_budget)
    assert summary2 == summary1 and marker2 == marker1
    assert compacted2 is True and "早期对话" in text2


def test_preview_does_not_advance_marker():
    # 预览（主请求用）：返回未压缩上下文 + needs_compact 标记，不推进 marker
    small_budget = 100_000
    rows = [(i, "user" if i % 2 else "assistant", "长" * 7000) for i in range(1, 41)]
    text, needs = session_history_preview(rows, summary="", marker=0, budget_bytes=small_budget)
    assert needs is True
    assert "早期对话" not in text  # 未压缩：无摘要标记


def test_new_messages_append_after_marker():
    # 首次压缩后，新增小消息不超预算 → 只追加在标记之后，不触碰已固化摘要
    budget = 300_000  # 300KB：首次 30 轮触发压缩后 keep(12×15KB)≈180KB + 摘要 ≈ 仍低于预算
    rows1 = []
    seq = 1
    for i in range(30):
        rows1.append((seq, "user", f"第{i}轮问题" + "长" * 5000)); seq += 1
        rows1.append((seq, "assistant", f"第{i}轮回答" + "答" * 5000)); seq += 1
    _, summary1, marker1, compacted1 = build_session_history(rows1, budget_bytes=budget)
    assert compacted1 is True and marker1 > 0
    new_rows = rows1 + [(100, "user", "新增问题"), (101, "assistant", "新增回答")]
    text, summary2, marker2, compacted2 = build_session_history(new_rows, summary=summary1, marker=marker1, budget_bytes=budget)
    assert summary2 == summary1 and marker2 == marker1  # 未超预算：摘要与标记不变
    assert "新增问题" in text and "新增回答" in text      # 新消息原样追加


def test_history_respects_byte_threshold():
    rows = [(1, "user", "短" * 200), (2, "assistant", "答" * 200)]
    total = sum(len(c.encode("utf-8")) for _, _, c in rows)
    assert total < context_budget_bytes()
    text, summary, marker, compacted = build_session_history(rows)
    assert compacted is False and marker == 0


def test_clear_and_delete_session(client: TestClient, org_headers: dict):
    sid = _mk_session(client, org_headers)
    # 发两条消息
    for msg in ("第一问", "第二问"):
        r = client.post(f"/api/v1/expert-chat/sessions/{sid}/messages", headers=org_headers, json={"content": msg})
        assert r.status_code == 200, r.text
    lst = client.get(f"/api/v1/expert-chat/sessions/{sid}/messages", headers=org_headers).json()["data"]["items"]
    assert len(lst) >= 4  # 2 问 2 答
    # 清空消息：会话保留，消息清空
    c = client.delete(f"/api/v1/expert-chat/sessions/{sid}/messages", headers=org_headers)
    assert c.status_code == 200
    lst2 = client.get(f"/api/v1/expert-chat/sessions/{sid}/messages", headers=org_headers).json()["data"]["items"]
    assert lst2 == []
    sessions = client.get("/api/v1/expert-chat/sessions", headers=org_headers).json()["data"]["items"]
    assert any(s["id"] == sid for s in sessions)  # 会话仍在
    # 删除会话
    d = client.delete(f"/api/v1/expert-chat/sessions/{sid}", headers=org_headers)
    assert d.status_code == 200
    sessions2 = client.get("/api/v1/expert-chat/sessions", headers=org_headers).json()["data"]["items"]
    assert all(s["id"] != sid for s in sessions2)


def test_clear_forbidden_for_other_owner(client: TestClient, org_headers: dict, dev_headers: dict):
    sid = _mk_session(client, org_headers)
    r = client.delete(f"/api/v1/expert-chat/sessions/{sid}/messages", headers=dev_headers)
    assert r.status_code == 404  # 非属主 → 会话不存在
