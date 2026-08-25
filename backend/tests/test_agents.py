"""Agent 管理测试：列表 / 能力边界 / 注册（密钥一次性）/ 绑定 / 状态 / 调用分流 / access keys。"""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from conftest import auth_headers
from flowhub_api.db.session import SessionFactory
from flowhub_api.models import DocItem, TaskAppend, TaskItem, User, WorkItem
from flowhub_api.services.agent_context import build_task_context


def _uniq(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def _created_agent(client: TestClient, org_headers: dict) -> tuple[str, str]:
    """session 级：org_admin 注册一个 Agent，返回 (agent_id, secret)。"""
    r = client.post("/api/v1/agents/register", headers=org_headers, json={
        "name": f"测试Agent-{uuid.uuid4().hex[:4]}", "desc": "pytest agent",
        "scope": "流程节点", "capabilities": ["read_context", "generate_content"],
    })
    assert r.status_code == 200
    data = r.json()["data"]
    return data["agent"]["id"], data["secret"]


class TestAgentList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/agents")
        assert r.status_code == 401

    def test_list_with_kpi(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/agents", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "items" in data and "kpi" in data
        assert data["kpi"]["total"] >= 1


class TestCapabilities:
    def test_capabilities(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/agents/capabilities", headers=leader_headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        names = {c["name"] for c in items}
        assert {"read_context", "generate_content", "submit_task"} <= names


class TestAgentRegister:
    def test_register_requires_perm(self, client: TestClient, leader_headers: dict):
        """leader 无 agent:register → 403（authorizer.require 显式 403，符合契约）。"""
        r = client.post("/api/v1/agents/register", headers=leader_headers,
                        json={"name": "x", "desc": "x"})
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_register_returns_secret_once(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": f"一次性密钥-{uuid.uuid4().hex[:4]}", "desc": "x",
        })
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["secret"].startswith("sk_")
        assert data["agent"]["status"] == "pending"

    def test_register_api_engine_requires_key(self, client: TestClient, org_headers: dict):
        """api 引擎必须提供 api_key + base_url。"""
        r = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "api-agent", "desc": "x", "engine": "api", "model": "gpt-4o",
        })
        assert r.status_code == 400
        assert "API Key 必填" in r.json()["message"]

    def test_register_with_invalid_tool(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "tool-agent", "desc": "x", "tool_id": "tool_nope",
        })
        assert r.status_code == 404

    def test_register_resolves_configured_provider_and_model(self, client: TestClient, org_headers: dict):
        provider = _uniq("provider")
        create = client.post("/api/v1/agents/tools", headers=org_headers, json={
            "name": f"{provider}-api", "engine": "api", "provider": provider,
            "models": ["chat", "reasoner"], "base_url": "https://example.test/v1", "api_key": "sk-test",
        })
        assert create.status_code == 200, create.text

        registered = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "provider-bound-agent", "provider": provider, "model": "reasoner",
        })
        assert registered.status_code == 200, registered.text
        agent = registered.json()["data"]["agent"]
        assert agent["provider"] == provider
        assert agent["model"] == "reasoner"

    def test_update_agent_changes_provider_model_and_prompt(self, client: TestClient, org_headers: dict):
        provider = _uniq("provider")
        created_provider = client.post("/api/v1/agents/tools", headers=org_headers, json={
            "name": f"{provider}-api", "engine": "api", "provider": provider,
            "models": ["chat"], "base_url": "https://example.test/v1", "api_key": "sk-test",
        })
        assert created_provider.status_code == 200, created_provider.text
        created_agent = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "editable-agent", "provider": provider, "model": "chat", "system_prompt": "old prompt",
        })
        assert created_agent.status_code == 200, created_agent.text
        agent_id = created_agent.json()["data"]["agent"]["id"]

        updated = client.put(f"/api/v1/agents/{agent_id}", headers=org_headers, json={
            "name": "edited-agent", "provider": provider, "model": "chat", "system_prompt": "new prompt",
        })
        assert updated.status_code == 200, updated.text
        agent = updated.json()["data"]["agent"]
        assert agent["name"] == "edited-agent"
        assert agent["provider"] == provider
        assert agent["model"] == "chat"
        assert agent["systemPrompt"] == "new prompt"

    def test_agent_detail_returns_full_system_prompt(self, client: TestClient, org_headers: dict):
        prompt = "x" * 160
        created = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "detail-agent", "system_prompt": prompt,
        })
        assert created.status_code == 200, created.text
        agent_id = created.json()["data"]["agent"]["id"]

        detail = client.get(f"/api/v1/agents/{agent_id}", headers=org_headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["agent"]["systemPrompt"] == prompt


class TestAgentLifecycle:
    def test_bind(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        agent_id, _ = _created_agent
        r = client.post(f"/api/v1/agents/{agent_id}/bind", headers=org_headers, json={
            "scope": "需求节点", "bindings": "tpl-req:n5", "capabilities": {"read_context": "direct"},
        })
        assert r.status_code == 200
        assert r.json()["data"]["agent"]["bindings"] == "tpl-req:n5"

    def test_bind_unknown_capability(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        agent_id, _ = _created_agent
        r = client.post(f"/api/v1/agents/{agent_id}/bind", headers=org_headers, json={
            "capabilities": {"fly_cap": "direct"},
        })
        assert r.status_code == 400

    def test_status_suspend_activate_revoke(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        agent_id, _ = _created_agent
        r1 = client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "activate"})
        assert r1.status_code == 200 and r1.json()["data"]["agent"]["status"] == "active"
        r2 = client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "suspend"})
        assert r2.status_code == 200 and r2.json()["data"]["agent"]["status"] == "suspended"
        r3 = client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "revoke"})
        assert r3.status_code == 200 and r3.json()["data"]["agent"]["status"] == "revoked"

    def test_status_unknown_action(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        agent_id, _ = _created_agent
        r = client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "explode"})
        assert r.status_code == 400

    def test_delete_requires_revoked_agent(self, client: TestClient, org_headers: dict):
        created = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": f"delete-agent-{uuid.uuid4().hex[:4]}",
        })
        assert created.status_code == 200, created.text
        agent_id = created.json()["data"]["agent"]["id"]
        rejected = client.delete(f"/api/v1/agents/{agent_id}", headers=org_headers)
        assert rejected.status_code == 400
        assert "吊销" in rejected.json()["message"]

        revoked = client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "revoke"})
        assert revoked.status_code == 200
        deleted = client.delete(f"/api/v1/agents/{agent_id}", headers=org_headers)
        assert deleted.status_code == 200
        assert deleted.json()["data"]["id"] == agent_id

        missing = client.get(f"/api/v1/agents/{agent_id}", headers=org_headers)
        assert missing.status_code == 404


class TestAgentInvoke:
    def test_invoke_inactive_agent_forbidden(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        """pending 状态 Agent 调用 → 40301 拒绝（HTTP 状态受 BUG-A 影响为 400）。"""
        agent_id, _ = _created_agent
        r = client.post(f"/api/v1/agents/{agent_id}/invoke", headers=org_headers, json={
            "capability": "read_context", "prompt": "hello",
        })
        assert r.json()["code"] == 40301
        assert r.status_code == 403  # 契约 403

    def test_invoke_forbidden_capability(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        """submit_task 默认 forbid → 拒绝（HTTP 状态受 BUG-A 影响为 400，契约 403）。"""
        agent_id, _ = _created_agent
        client.post(f"/api/v1/agents/{agent_id}/status", headers=org_headers, json={"action": "activate"})
        r = client.post(f"/api/v1/agents/{agent_id}/invoke", headers=org_headers, json={
            "capability": "submit_task", "action": "提交任务", "prompt": "x",
        })
        assert r.json()["code"] in (40301, 40302)
        assert r.status_code == 403

    def test_invoke_unknown_capability(self, client: TestClient, org_headers: dict, _created_agent: tuple):
        """未知能力 → 能力边界 forbid → 403（修复后语义：能力边界外不可触碰）。"""
        agent_id, _ = _created_agent
        r = client.post(f"/api/v1/agents/{agent_id}/invoke", headers=org_headers, json={
            "capability": "no_such_cap", "prompt": "x",
        })
        assert r.status_code == 403
        assert r.json()["code"] == 40301


class TestProviderConfiguration:
    def test_opencode_provider_list_includes_runtime_models(self, client: TestClient, org_headers: dict, monkeypatch: pytest.MonkeyPatch):
        from flowhub_api.services import opencode_config

        monkeypatch.setattr(opencode_config, "list_providers", lambda: [{
            "provider": "deepseek", "type": "api", "hasKey": True, "baseUrl": "", "models": [],
        }])
        monkeypatch.setattr(opencode_config, "list_models", lambda: [
            {"provider": "deepseek", "model": "deepseek-chat"},
        ])

        listed = client.get("/api/v1/agents/tools/opencode/providers", headers=org_headers)
        assert listed.status_code == 200
        assert listed.json()["data"]["items"][0]["models"] == ["deepseek-chat"]

    def test_provider_list_returns_base_url_and_models(self, client: TestClient, org_headers: dict):
        provider = _uniq("provider")
        created = client.post("/api/v1/agents/tools", headers=org_headers, json={
            "name": f"{provider}-api", "engine": "api", "provider": provider,
            "models": ["chat", "reasoner"], "base_url": "https://example.test/v1", "api_key": "sk-test",
        })
        assert created.status_code == 200, created.text

        listed = client.get("/api/v1/agents/tools", headers=org_headers)
        assert listed.status_code == 200
        item = next(item for item in listed.json()["data"]["items"] if item["provider"] == provider)
        assert item["baseUrl"] == "https://example.test/v1"
        assert item["models"] == ["chat", "reasoner"]

    def test_agent_type_persists_default_provider_and_model(self, client: TestClient, org_headers: dict):
        provider = _uniq("provider")
        create_provider = client.post("/api/v1/agents/tools", headers=org_headers, json={
            "name": f"{provider}-api", "engine": "api", "provider": provider,
            "models": ["chat"], "base_url": "https://example.test/v1", "api_key": "sk-test",
        })
        assert create_provider.status_code == 200, create_provider.text

        created = client.post("/api/v1/agents/types", headers=org_headers, json={
            "code": _uniq("type"), "label": "Provider default", "provider": provider, "model": "chat",
        })
        assert created.status_code == 200, created.text
        assert created.json()["data"]["type"]["provider"] == provider
        assert created.json()["data"]["type"]["model"] == "chat"

    def test_agent_type_default_selects_provider_when_registering_agent(self, client: TestClient, org_headers: dict):
        provider = _uniq("provider")
        assert client.post("/api/v1/agents/tools", headers=org_headers, json={
            "name": f"{provider}-api", "engine": "api", "provider": provider,
            "models": ["chat"], "base_url": "https://example.test/v1", "api_key": "sk-test",
        }).status_code == 200
        type_code = _uniq("type")
        assert client.post("/api/v1/agents/types", headers=org_headers, json={
            "code": type_code, "label": "Provider default", "provider": provider, "model": "chat",
        }).status_code == 200

        registered = client.post("/api/v1/agents/register", headers=org_headers, json={
            "name": "type-default-agent", "agent_type": type_code,
        })
        assert registered.status_code == 200, registered.text
        agent = registered.json()["data"]["agent"]
        assert agent["provider"] == provider
        assert agent["model"] == "chat"


class TestAgentInheritedContext:
    def test_agent_only_node_receives_completed_forms_appends_and_attachments(self, client: TestClient):
        """自动节点以其系统处理人身份读取与任务页一致的继承上下文。"""
        suffix = uuid.uuid4().hex[:8]

        async def exercise() -> str:
            async with SessionFactory() as session:
                wi = WorkItem(
                    id=f"WI-CONTEXT-{suffix}", type="issue", title="agent context", project="测试项目",
                    creator="张伟", start_values={"起始说明": "来自起始节点", "附件": [{"id": f"d-start-{suffix}", "name": "start.md"}]},
                )
                completed = TaskItem(
                    id=f"T-CONTEXT-1-{suffix}", wi_id=wi.id, title="前序", project=wi.project,
                    node="分析", type="issue", assignee="张伟", status="completed",
                    form_values={"分析结论": "前序已完成", "附件": [{"id": f"d-prev-{suffix}", "name": "previous.txt"}]},
                )
                unfinished = TaskItem(
                    id=f"T-CONTEXT-2-{suffix}", wi_id=wi.id, title="未完成", project=wi.project,
                    node="并行节点", type="issue", assignee="张伟", status="in_progress", form_values={"不应出现": "未完成"},
                )
                current = TaskItem(
                    id=f"T-CONTEXT-3-{suffix}", wi_id=wi.id, title="自动节点", project=wi.project,
                    node="Agent 自动", type="issue", assignee="待分配", status="pending_confirmation",
                )
                docs = [
                    DocItem(id=f"d-start-{suffix}", name="start.md", project=wi.project, uploader="张伟", wi=wi.id),
                    DocItem(id=f"d-prev-{suffix}", name="previous.txt", project=wi.project, uploader="张伟", wi=wi.id),
                ]
                append = TaskAppend(
                    id=f"TA-CONTEXT-{suffix}", task_id=completed.id, node_id="n1", wi_id=wi.id,
                    appender="张伟", values={"补充说明": "前序补充", "附件": [{"id": f"d-append-{suffix}", "name": "append.pdf"}]},
                )
                docs.append(DocItem(id=f"d-append-{suffix}", name="append.pdf", project=wi.project, uploader="张伟", wi=wi.id))
                session.add_all([wi, completed, unfinished, current, *docs])
                await session.flush()
                session.add(append)
                await session.commit()
                system_user = User(id="system", name="待分配", account=f"system-{suffix}", roles=[])
                return await build_task_context(session, system_user, current, include_docs=True)

        context = asyncio.run(exercise())
        assert "来自起始节点" in context
        assert "前序已完成" in context
        assert "前序补充" in context
        assert "未完成" not in context
        assert "start.md" in context and "previous.txt" in context and "append.pdf" in context
        assert "/api/v1/documents/d-prev-" in context


def _create_key(client: TestClient, headers: dict, name: str = "ci-key") -> tuple[str, str]:
    """创建 access key，返回 (id, 明文)。"""
    r = client.post("/api/v1/agents/access-keys", headers=headers, json={"name": name})
    assert r.status_code == 200
    data = r.json()["data"]["key"]
    assert data["key"].startswith("sk_")
    return data["id"], data["key"]


class TestAccessKeys:
    def test_create_key_returns_plaintext_once(self, client: TestClient, leader_headers: dict):
        kid, raw = _create_key(client, leader_headers)
        assert kid and raw.startswith("sk_")

    def test_list_keys(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/agents/access-keys", headers=leader_headers)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        # 不泄露明文/哈希
        for k in items:
            assert "key" not in k and "hash" not in str(k)

    def test_revoke_key(self, client: TestClient, leader_headers: dict):
        kid, _ = _create_key(client, leader_headers)
        r = client.delete(f"/api/v1/agents/access-keys/{kid}", headers=leader_headers)
        assert r.status_code == 200
        assert r.json()["data"]["key"]["status"] == "revoked"

    def test_cannot_revoke_others_key(self, client: TestClient, leader_headers: dict, dev_headers: dict):
        kid, _ = _create_key(client, leader_headers)
        r = client.delete(f"/api/v1/agents/access-keys/{kid}", headers=dev_headers)
        assert r.status_code == 404  # 他人 key 不可见（防越权）
