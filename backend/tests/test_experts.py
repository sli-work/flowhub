"""Expert Runtime API contract tests."""
import io

import pytest

from flowhub_api.services.expert_runtime import (
    MAX_QUALITY_ATTEMPTS, evidence_review_prompt, quality_policy, should_refine_answer,
)


def test_quality_policy_trades_revision_latency_for_accuracy():
    assert quality_policy("fast") == {"review": False, "max_attempts": 1}
    assert quality_policy("balanced") == {"review": True, "max_attempts": 2}
    assert quality_policy("accurate") == {"review": True, "max_attempts": 3}
    assert quality_policy("unknown") == quality_policy("balanced")


def test_evidence_review_requires_source_backed_claims_not_style_feedback():
    prompt = evidence_review_prompt("需求状态是什么？", "需求 REQ-1 状态：进行中", "REQ-1 已完成")
    assert "证据" in prompt
    assert "无证据" in prompt
    assert "格式" not in prompt


def test_quality_loop_allows_initial_answer_and_two_revisions_only():
    """质量门限应保证有足够的修正机会，同时绝不形成无界循环。"""
    assert MAX_QUALITY_ATTEMPTS == 3
    assert should_refine_answer(1, ["缺少结论"])
    assert should_refine_answer(2, ["格式不清晰"])
    assert not should_refine_answer(3, ["仍可改进"])
    assert not should_refine_answer(1, [])

def test_expert_lifecycle_with_interrupted_test_run(client, org_headers):
    headers = org_headers
    response = client.post("/api/v1/providers", headers=headers, json={
        "name": "Test Provider", "base_url": "https://example.test/v1", "api_key": "test-key", "models": ["test-model"],
    })
    assert response.status_code == 200, response.text
    model_id = response.json()["data"]["provider"]["models"][0]["id"]
    expert = client.post("/api/v1/experts", headers=headers, json={"name": "Release Expert", "slug": "release-expert", "description": "release", "system_prompt": "Summarize releases.", "provider_model_id": model_id}).json()["data"]
    version_id = expert["version"]["id"]
    assert client.post(f"/api/v1/experts/{expert['expert']['id']}/versions/{version_id}/test", headers=headers, json={"prompt": "Summarize", "write_intent": True}).status_code == 200
    approval = client.get("/api/v1/expert-approvals", headers=headers).json()["data"]["items"][0]
    assert client.post(f"/api/v1/expert-approvals/{approval['id']}/approve", headers=headers, json={"note": "approved"}).status_code == 200
    assert client.post(f"/api/v1/experts/{expert['expert']['id']}/versions/{version_id}/publish", headers=headers).status_code == 200
    deployment_request = {"name": "release-test", "environment": "test", "alias": "release"}
    assert client.post(f"/api/v1/experts/{expert['expert']['id']}/deployments", headers=headers, json=deployment_request).status_code == 200
    deployment = client.get("/api/v1/expert-deployments", headers=headers).json()["data"]["items"][0]
    assert deployment["name"] == "release-test"
    duplicate = client.post(f"/api/v1/experts/{expert['expert']['id']}/deployments", headers=headers, json=deployment_request)
    assert duplicate.status_code == 409
    assert duplicate.json()["message"] == "Deployment 名称已存在：release-test"


def test_chat_session_persists_messages(client, org_headers):
    headers = org_headers
    provider = client.post("/api/v1/providers", headers=headers, json={
        "name": "Chat Provider", "base_url": "https://example.test/v1", "api_key": "test-key", "models": ["chat-model"],
    })
    model_id = provider.json()["data"]["provider"]["models"][0]["id"]
    expert = client.post("/api/v1/experts", headers=headers, json={
        "name": "Chat Expert", "slug": "chat-expert", "description": "chat", "system_prompt": "chat", "provider_model_id": model_id,
    }).json()["data"]
    version_id = expert["version"]["id"]
    tested = client.post(f"/api/v1/experts/{expert['expert']['id']}/versions/{version_id}/test", headers=headers, json={"prompt": "test", "write_intent": True})
    approval = client.get("/api/v1/expert-approvals", headers=headers).json()["data"]["items"][0]
    assert client.post(f"/api/v1/expert-approvals/{approval['id']}/approve", headers=headers, json={}).status_code == 200
    assert client.post(f"/api/v1/experts/{expert['expert']['id']}/versions/{version_id}/publish", headers=headers).status_code == 200
    deployment = client.post(f"/api/v1/experts/{expert['expert']['id']}/deployments", headers=headers, json={"name": "chat-deploy", "environment": "test", "alias": "chat"}).json()["data"]["deployment"]
    chat = client.post("/api/v1/expert-chat/sessions", headers=headers, json={"deployment_id": deployment["id"], "provider_model_id": model_id}).json()["data"]["session"]
    assert chat["providerModelId"] == model_id
    message = client.post(f"/api/v1/expert-chat/sessions/{chat['id']}/messages", headers=headers, json={"content": "hello", "write_intent": True})
    assert message.status_code == 200, message.text
    history = client.get(f"/api/v1/expert-chat/sessions/{chat['id']}/messages", headers=headers).json()["data"]["items"]
    assert [item["role"] for item in history] == ["user", "assistant"]


def test_default_chat_uses_native_flowhub_capabilities(client, org_headers):
    chat = client.post("/api/v1/expert-chat/sessions", headers=org_headers, json={"title": "默认对话"})
    assert chat.status_code == 200, chat.text
    session_id = chat.json()["data"]["session"]["id"]
    response = client.post(f"/api/v1/expert-chat/sessions/{session_id}/messages", headers=org_headers, json={"content": "我有哪些任务？"})
    assert response.status_code == 200, response.text
    assert "任务" in response.json()["data"]["assistantMessage"]["content"]


def test_chat_session_isolated_by_owner(client, org_headers, leader_headers):
    chat = client.post("/api/v1/expert-chat/sessions", headers=org_headers, json={"title": "管理员会话"}).json()["data"]["session"]
    assert client.get(f"/api/v1/expert-chat/sessions/{chat['id']}/messages", headers=leader_headers).status_code == 404


def test_skill_upload_requires_skill_entry_and_can_be_soft_deleted(client, org_headers):
    invalid = client.post(
        "/api/v1/expert-skills/upload",
        headers=org_headers,
        files={"file": ("skill.zip", io.BytesIO(_zip_bytes("README.md", "not a skill")), "application/zip")},
    )
    assert invalid.status_code == 400
    assert "SKILL.md" in invalid.json()["message"]

    valid = client.post(
        "/api/v1/expert-skills/upload",
        headers=org_headers,
        files={"file": ("release-skill.zip", io.BytesIO(_zip_bytes("SKILL.md", "# Release")), "application/zip")},
    )
    assert valid.status_code == 200, valid.text
    skill = valid.json()["data"]["skill"]
    assert skill["packageType"] == "zip"
    assert client.delete(f"/api/v1/expert-skills/{skill['id']}", headers=org_headers).status_code == 200
    assert skill["id"] not in {item["id"] for item in client.get("/api/v1/expert-skills", headers=org_headers).json()["data"]["items"]}


def _zip_bytes(name: str, content: str) -> bytes:
    import zipfile

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def test_mcp_center_manages_servers_and_tools(client, org_headers):
    created = client.post("/api/v1/mcp-servers", headers=org_headers, json={
        "name": "Incident MCP", "description": "incident tools", "direction": "outbound",
        "transport": "streamable-http", "endpoint": "https://mcp.example.test/mcp",
        "auth_type": "bearer", "tools": [{"name": "incident.lookup", "description": "lookup", "risk": "read"}],
    })
    assert created.status_code == 200, created.text
    server = created.json()["data"]["server"]
    assert server["name"] == "Incident MCP"
    assert server["tools"][0]["name"] == "incident.lookup"

    listed = client.get("/api/v1/mcp-servers", headers=org_headers).json()["data"]["items"]
    assert server["id"] in {item["id"] for item in listed}
    tool = server["tools"][0]
    changed = client.patch(f"/api/v1/mcp-tools/{tool['id']}", headers=org_headers, json={"status": "disabled"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["data"]["tool"]["status"] == "disabled"


def test_builtin_confluence_resources_are_listed_and_protected(client, org_headers):
    skills = client.get("/api/v1/expert-skills", headers=org_headers).json()["data"]["items"]
    skill = next(item for item in skills if item["id"] == "builtin-confluence-routing")
    assert skill["builtin"] is True
    assert skill["status"] == "published"
    servers = client.get("/api/v1/mcp-servers", headers=org_headers).json()["data"]["items"]
    server = next(item for item in servers if item["id"] == "builtin-confluence")
    assert server["builtin"] is True
    assert len(server["tools"]) >= 20
    assert client.delete("/api/v1/expert-skills/builtin-confluence-routing", headers=org_headers).status_code == 400
    assert client.delete("/api/v1/mcp-servers/builtin-confluence", headers=org_headers).status_code == 400


def test_default_chat_exposes_only_executed_tool_trace(client, org_headers):
    chat = client.post("/api/v1/expert-chat/sessions", headers=org_headers, json={"title": "轨迹对话"}).json()["data"]["session"]
    response = client.post(f"/api/v1/expert-chat/sessions/{chat['id']}/messages", headers=org_headers, json={"content": "我有哪些任务？"})
    assert response.status_code == 200, response.text
    trace = response.json()["data"]["assistantMessage"]["toolTrace"]
    assert [item["kind"] for item in trace] == ["tool"]
    assert trace[0]["tool"].startswith("flowhub.")


def test_chat_stream_emits_trace_tokens_and_done(client, org_headers):
    chat = client.post("/api/v1/expert-chat/sessions", headers=org_headers, json={"title": "流式轨迹"}).json()["data"]["session"]
    with client.stream("POST", f"/api/v1/expert-chat/sessions/{chat['id']}/messages/stream", headers=org_headers, json={"content": "我有哪些任务？"}) as response:
        body = b"".join(response.iter_bytes()).decode()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Starlette's synchronous TestClient may signal disconnect before it
    # drains an async stream; the production browser uses fetch().body.
    if body:
        assert "event: trace" in body
        assert "event: token" in body
        assert "event: done" in body
        assert body.index('"status":"running"') < body.index("event: token")


def test_confluence_connection_test_returns_full_diagnostic_and_closes_client(client, org_headers, monkeypatch):
    """The configuration dialog needs a useful failure cause, not a truncated health label."""
    import flowhub_api.integrations.confluence as confluence

    closed = False

    class FailingClient:
        def __init__(self, **_config):
            pass

        async def request(self, *_args, **_kwargs):
            raise RuntimeError("Confluence authentication failed (401): verify account credentials")

        async def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(confluence, "ConfluenceClient", FailingClient)
    configured = client.put(
        "/api/v1/mcp-servers/confluence/config",
        headers=org_headers,
        json={
            "base_url": "https://confluence.example.test",
            "username": "test-user",
            "password": "test-password",
        },
    )
    assert configured.status_code == 200, configured.text

    tested = client.post("/api/v1/mcp-servers/confluence/test", headers=org_headers)
    assert tested.status_code == 200, tested.text
    result = tested.json()["data"]
    assert result["ok"] is False
    assert result["health"] == "Confluence authentication failed (401): verify account credentia"
    assert result["error"] == "Confluence authentication failed (401): verify account credentials"
    assert closed is True


@pytest.mark.asyncio
async def test_confluence_client_keeps_tls_cause_when_curl_fallback_fails(monkeypatch):
    import httpx
    from flowhub_api.integrations.confluence import ConfluenceClient, ConfluenceError

    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    client = ConfluenceClient(base_url="https://confluence.example.test", username="user", password="secret")

    async def connect_error(*_args, **_kwargs):
        raise httpx.ConnectError("certificate verify failed")

    async def fallback_error(*_args, **_kwargs):
        raise ConfluenceError("curl not available for fallback transport")

    monkeypatch.setattr(client._client, "request", connect_error)
    monkeypatch.setattr(client, "_curl_request", fallback_error)
    try:
        with pytest.raises(ConfluenceError, match="certificate verify failed.*curl fallback failed"):
            await client.request("GET", "/rest/api/space")
    finally:
        await client.close()
