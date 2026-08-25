"""流程模板与画布测试：模板池 / 版本 / 画布读写 / 发布校验。"""
import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers

REQ_V3_CANVAS = {
    "nodes": [
        {"id": "n1", "label": "开始", "type": "start"},
        {"id": "n2", "label": "任务", "type": "task"},
        {"id": "n3", "label": "结束", "type": "end"},
    ],
    "edges": [["n1", "n2"], ["n2", "n3"]],
    "fallbacks": [],
}


class TestTemplatePool:
    def test_pool_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/templates/pool")
        assert r.status_code == 401

    def test_pool(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/templates/pool", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        ids = {t["id"] for t in data["items"]}
        assert {"tpl-req", "tpl-issue"} <= ids
        req = next(t for t in data["items"] if t["id"] == "tpl-req")
        assert "v4" in req["versions"]
        assert len(req["nodes"]) == 10


class TestTemplateVersions:
    def test_versions(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/templates/tpl-req/versions", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        versions = {v["version"] for v in data["items"]}
        assert versions == {"v1", "v2", "v3", "v4"}
        # 状态补齐：v4 应为 draft、v3 应为 published
        status_map = {v["version"]: v["status"] for v in data["items"]}
        assert status_map["v4"] == "draft"

    def test_versions_not_found(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/templates/tpl-ghost/versions", headers=leader_headers)
        assert r.status_code == 404

    def test_create_version(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/templates/tpl-issue/versions", headers=org_headers)
        assert r.status_code == 200
        assert r.json()["data"]["version"] == "v3"  # tpl-issue 现有 v1/v2 → 新 v3
        # 连续创建应递增到 v4（versions 数组与版本表同步，不产生孤儿/重复）
        r2 = client.post("/api/v1/templates/tpl-issue/versions", headers=org_headers)
        assert r2.status_code == 200
        assert r2.json()["data"]["version"] == "v4"

    def test_create_version_no_perm(self, client: TestClient, dev_headers: dict):
        r = client.post("/api/v1/templates/tpl-issue/versions", headers=dev_headers)
        assert r.status_code == 403


class TestCanvas:
    def test_get_default_canvas_req_v3(self, client: TestClient, leader_headers: dict):
        """需求流程 v3 默认画布：10 节点 10 边（自动生成）。"""
        r = client.get("/api/v1/templates/tpl-req/versions/v3/canvas", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["nodes"]) == 10
        assert len(data["edges"]) == 10
        assert len(data["fallbacks"]) >= 4

    def test_get_default_canvas_issue_v1(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/templates/tpl-issue/versions/v1/canvas", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["nodes"]) == 8

    def test_get_empty_canvas(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/templates/tpl-req/versions/v1/canvas", headers=leader_headers)
        assert r.status_code == 200
        assert r.json()["data"] == {"nodes": [], "edges": [], "fallbacks": []}

    def test_save_draft_canvas(self, client: TestClient, org_headers: dict):
        """先补齐版本记录，再保存 v4（draft 可写）。"""
        client.get("/api/v1/templates/tpl-req/versions", headers=org_headers)
        payload = {
            "nodes": [{"id": "x1", "label": "A", "type": "start"}, {"id": "x2", "label": "B", "type": "end"}],
            "edges": [["x1", "x2"]], "fallbacks": [],
        }
        r = client.put("/api/v1/templates/tpl-req/versions/v4/canvas", headers=org_headers, json=payload)
        assert r.status_code == 200
        got = client.get("/api/v1/templates/tpl-req/versions/v4/canvas", headers=org_headers).json()["data"]
        assert got["nodes"] == payload["nodes"]

    def test_save_published_canvas_forbidden(self, client: TestClient, org_headers: dict):
        """v3 为 published → 保存应 403。"""
        r = client.put("/api/v1/templates/tpl-req/versions/v3/canvas", headers=org_headers,
                       json={"nodes": [], "edges": [], "fallbacks": []})
        assert r.status_code == 403

    def test_save_canvas_no_perm(self, client: TestClient, dev_headers: dict):
        r = client.put("/api/v1/templates/tpl-req/versions/v4/canvas", headers=dev_headers,
                       json={"nodes": [], "edges": [], "fallbacks": []})
        assert r.status_code == 403


class TestCanvasValidate:
    def test_validate_valid_canvas(self, client: TestClient):
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate",
                        json=REQ_V3_CANVAS)
        assert r.status_code == 200
        assert r.json()["data"]["ok"] is True

    def test_validate_two_starts(self, client: TestClient):
        payload = {
            "nodes": [
                {"id": "a", "type": "start"}, {"id": "b", "type": "start"},
                {"id": "c", "type": "end"},
            ],
            "edges": [["a", "c"], ["b", "c"]], "fallbacks": [],
        }
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate", json=payload)
        data = r.json()["data"]
        assert data["ok"] is False
        assert any("开始节点必须为 1 个" in e["message"] for e in data["errors"])

    def test_validate_zero_end(self, client: TestClient):
        payload = {"nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "task"}],
                   "edges": [["a", "b"]], "fallbacks": []}
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate", json=payload)
        data = r.json()["data"]
        assert data["ok"] is False
        assert any("结束节点必须为 1 个" in e["message"] for e in data["errors"])

    def test_validate_dangling_nodes(self, client: TestClient):
        payload = {
            "nodes": [
                {"id": "a", "type": "start"}, {"id": "b", "type": "task"},
                {"id": "c", "type": "task"}, {"id": "d", "type": "end"},
            ],
            "edges": [["a", "b"]],  # c 悬空（无入边无出边）
            "fallbacks": [],
        }
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate", json=payload)
        data = r.json()["data"]
        assert data["ok"] is False
        assert any("悬空节点" in e["message"] for e in data["errors"])

    def test_validate_self_fallback(self, client: TestClient):
        payload = {**REQ_V3_CANVAS, "fallbacks": [["n2", "n2"]]}
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate", json=payload)
        data = r.json()["data"]
        assert data["ok"] is False
        assert any("回退不能指向自身" in e["message"] for e in data["errors"])

    def test_validate_agent_auto_without_binding(self, client: TestClient):
        """handler=Agent 自动 但未绑定 Agent → 校验失败。"""
        payload = {
            "nodes": [
                {"id": "a", "type": "start"}, {"id": "b", "type": "task", "cfg": {"handler": "Agent 自动"}},
                {"id": "c", "type": "end"},
            ],
            "edges": [["a", "b"], ["b", "c"]], "fallbacks": [],
        }
        r = client.post("/api/v1/templates/tpl-req/versions/v3/canvas/validate", json=payload)
        data = r.json()["data"]
        assert data["ok"] is False
        assert any("必须绑定 Agent" in e["message"] for e in data["errors"])


class TestPublish:
    def _publish(self, client: TestClient, headers: dict, version: str, template_id: str = "tpl-issue") -> "tuple[int, dict]":
        r = client.post(f"/api/v1/templates/{template_id}/versions/{version}/publish", headers=headers)
        return r.status_code, r.json()

    def test_publish_valid_version(self, client: TestClient, org_headers: dict):
        """tpl-issue v1 有默认画布（8 节点合法拓扑）→ 发布成功。"""
        # 先生成默认画布
        client.get("/api/v1/templates/tpl-issue/versions/v1/canvas", headers=org_headers)
        code, body = self._publish(client, org_headers, "v1")
        assert code == 200
        assert body["data"]["status"] == "published"
        # 版本列表确认状态
        versions = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        v1 = next(v for v in versions if v["version"] == "v1")
        assert v1["status"] == "published"

    def test_publish_duplicate(self, client: TestClient, org_headers: dict):
        """重复发布已发布版本 → 40902。"""
        client.get("/api/v1/templates/tpl-issue/versions/v1/canvas", headers=org_headers)
        self._publish(client, org_headers, "v1")
        code, body = self._publish(client, org_headers, "v1")
        assert code == 409
        assert body["code"] == 40902

    def test_publish_empty_canvas_blocked(self, client: TestClient, org_headers: dict):
        """新草稿版本画布为空 → 发布被阻断（422）。"""
        client.post("/api/v1/templates/tpl-req/versions", headers=org_headers)  # 生成 v5 草稿（画布为空）
        code, body = self._publish(client, org_headers, "v5", template_id="tpl-req")
        assert code == 422
        assert "画布为空" in body["message"]

    def test_publish_invalid_canvas_blocked(self, client: TestClient, org_headers: dict):
        """保存了非法画布（缺结束节点）→ 发布被阻断并返回校验问题。
        用独立模板保存坏画布，避免污染共享模板的节点定义（保存画布会同步模板节点）。"""
        tid = client.post("/api/v1/templates", headers=org_headers,
                          json={"name": f"坏画布-{__import__('uuid').uuid4().hex[:4]}", "type": "requirement"}
                          ).json()["data"]["template"]["id"]
        bad = {"nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "task"}],
               "edges": [["a", "b"]], "fallbacks": []}
        client.put(f"/api/v1/templates/{tid}/versions/v1/canvas", headers=org_headers, json=bad)
        code, body = self._publish(client, org_headers, "v1", template_id=tid)
        assert code == 422
        assert "阻断" in body["message"]

    def test_publish_no_perm(self, client: TestClient, dev_headers: dict):
        """developer 无 workflow_template:publish → 403。"""
        client.get("/api/v1/templates/tpl-issue/versions/v1/canvas", headers=dev_headers)
        code, body = self._publish(client, dev_headers, "v1")
        assert code == 403
        assert body["code"] == 40302

    def test_publish_not_found(self, client: TestClient, org_headers: dict):
        code, body = self._publish(client, org_headers, "v99")
        assert code == 404


class TestSaveAndPublish:
    """发布（自动创建新版本）：每次发布都创建新版本号 → 保存画布 → 校验 → 自动发布。"""

    VALID = {
        "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "task"}, {"id": "c", "type": "end"}],
        "edges": [["a", "b"], ["b", "c"]], "fallbacks": [],
    }
    INVALID = {
        "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "task"}],
        "edges": [["a", "b"]], "fallbacks": [],  # 缺结束节点
    }

    def _save(self, client: TestClient, headers: dict, payload: dict, template_id: str = "tpl-issue") -> "tuple[int, dict]":
        r = client.post(f"/api/v1/templates/{template_id}/versions/save-and-publish", headers=headers, json=payload)
        return r.status_code, r.json()

    def test_save_publish_publishes_new_version(self, client: TestClient, org_headers: dict):
        """合法画布：发布创建新版本并进入 published。"""
        code, body = self._save(client, org_headers, self.VALID)
        assert code == 200
        ver = body["data"]["version"]
        assert body["data"]["status"] == "published"
        vers = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        v = next(x for x in vers if x["version"] == ver)
        assert v["status"] == "published"

    def test_save_publish_always_creates_new_version(self, client: TestClient, org_headers: dict):
        """连续发布两次 → 每次都是新版本号（发布永远自动创建新版，不顶替既有版本）。"""
        v1 = self._save(client, org_headers, self.VALID)[1]["data"]["version"]
        v2 = self._save(client, org_headers, self.VALID)[1]["data"]["version"]
        assert int(v2[1:]) > int(v1[1:])
        # v1 仍保持 published（未被顶替/覆盖）
        vers = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        v1_item = next(x for x in vers if x["version"] == v1)
        assert v1_item["status"] == "published"

    def test_save_publish_invalid_keeps_new_draft(self, client: TestClient, org_headers: dict):
        """非法画布：422 阻断，新版本保留为草稿；修复后再次发布创建下一个新版本。"""
        code, body = self._save(client, org_headers, self.INVALID)
        assert code == 422
        assert "发布校验未通过" in body["message"]
        # 新版本保留为草稿
        vers = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        latest = vers[0]["version"]
        assert vers[0]["status"] == "draft"
        # 修复后再次发布 → 创建下一个新版本（不再是同一版本）
        code2, body2 = self._save(client, org_headers, self.VALID)
        assert code2 == 200
        assert int(body2["data"]["version"][1:]) > int(latest[1:])
        assert body2["data"]["status"] == "published"

    def test_save_publish_no_perm(self, client: TestClient, dev_headers: dict):
        """developer 无 workflow_template:publish → 403。"""
        code, body = self._save(client, dev_headers, self.VALID)
        assert code == 403
        assert body["code"] == 40302


class TestSaveDraft:
    """保存草稿（不发布）：复用最新草稿或自动创建新版本草稿。"""

    VALID = {
        "nodes": [{"id": "a", "type": "start"}, {"id": "b", "type": "task"}, {"id": "c", "type": "end"}],
        "edges": [["a", "b"], ["b", "c"]], "fallbacks": [],
    }

    def _save(self, client: TestClient, headers: dict, payload: dict, template_id: str = "tpl-issue") -> "tuple[int, dict]":
        r = client.post(f"/api/v1/templates/{template_id}/versions/save-draft", headers=headers, json=payload)
        return r.status_code, r.json()

    def test_save_draft_creates_new_draft(self, client: TestClient, org_headers: dict):
        """最新版本无草稿（已发布）→ 自动创建新版本草稿，不发布。"""
        code, body = self._save(client, org_headers, self.VALID)
        assert code == 200
        ver = body["data"]["version"]
        assert body["data"]["status"] == "draft"
        assert body["data"]["created"] is True
        # 版本保留为草稿（未被发布）
        vers = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        v = next(x for x in vers if x["version"] == ver)
        assert v["status"] == "draft"

    def test_save_draft_reuses_latest_draft(self, client: TestClient, org_headers: dict):
        """已有草稿 → 复用同一版本（不产生新版本），内容覆盖。"""
        v1 = self._save(client, org_headers, self.VALID)[1]["data"]["version"]
        code2, body2 = self._save(client, org_headers, self.VALID)
        assert code2 == 200
        assert body2["data"]["version"] == v1
        assert body2["data"]["created"] is False
        assert body2["data"]["status"] == "draft"

    def test_save_draft_then_publish_creates_new_version(self, client: TestClient, org_headers: dict):
        """保存草稿 → 再发布：发布自动创建下一个新版本并转 published（草稿→发布闭环，草稿本身保留）。"""
        vd = self._save(client, org_headers, self.VALID)[1]["data"]["version"]
        r = client.post("/api/v1/templates/tpl-issue/versions/save-and-publish", headers=org_headers, json=self.VALID)
        assert r.status_code == 200
        vp = r.json()["data"]["version"]
        assert int(vp[1:]) > int(vd[1:])
        assert r.json()["data"]["status"] == "published"
        # 草稿版本仍保留为 draft（发布不顶替草稿）
        vers = client.get("/api/v1/templates/tpl-issue/versions", headers=org_headers).json()["data"]["items"]
        vd_item = next(x for x in vers if x["version"] == vd)
        assert vd_item["status"] == "draft"


class TestStartSchema:
    """模板最新版本的起始节点表单（新建工作项硬性要求）。"""

    def test_start_schema_returns_latest_published(self, client: TestClient, org_headers: dict):
        """返回模板最新已发布版本的起始表单（非空 FormField[]）。"""
        r = client.get("/api/v1/templates/tpl-req/start-schema", headers=org_headers)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["templateId"] == "tpl-req"
        assert d["version"] is not None
        assert d["schema"] and len(d["schema"]) > 0
        # 表单字段结构：key/label/type/required
        first = d["schema"][0]
        assert "key" in first and "label" in first and "required" in first

    def test_start_schema_not_found(self, client: TestClient, org_headers: dict):
        r = client.get("/api/v1/templates/tpl-ghost/start-schema", headers=org_headers)
        assert r.status_code == 404


class TestCreateTemplate:
    """新建流程模板：创建全局模板 + 初始 v1 草稿。"""

    def test_create_template(self, client: TestClient, org_headers: dict):
        """创建模板 → 返回新模板（tpl- 前缀、v1 草稿），模板池可见。"""
        name = f"变更流程-{__import__('uuid').uuid4().hex[:4]}"
        r = client.post("/api/v1/templates", headers=org_headers, json={"name": name, "type": "change"})
        assert r.status_code == 200
        t = r.json()["data"]["template"]
        assert t["id"].startswith("tpl-")
        assert t["name"] == name
        assert t["versions"] == ["v1"]
        # 模板池可见 + v1 草稿版本存在
        pool = client.get("/api/v1/templates/pool", headers=org_headers).json()["data"]["items"]
        assert any(x["id"] == t["id"] for x in pool)
        vers = client.get(f"/api/v1/templates/{t['id']}/versions", headers=org_headers).json()["data"]["items"]
        assert vers[0]["version"] == "v1" and vers[0]["status"] == "draft"

    def test_create_template_duplicate_name(self, client: TestClient, org_headers: dict):
        name = f"重复模板-{__import__('uuid').uuid4().hex[:4]}"
        assert client.post("/api/v1/templates", headers=org_headers, json={"name": name, "type": "requirement"}).status_code == 200
        r = client.post("/api/v1/templates", headers=org_headers, json={"name": name, "type": "issue"})
        assert r.status_code == 409
        assert r.json()["code"] == 40901

    def test_create_template_no_perm(self, client: TestClient, leader_headers: dict):
        """leader 无 workflow_template:create → 403。"""
        r = client.post("/api/v1/templates", headers=leader_headers, json={"name": "x", "type": "requirement"})
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_create_template_missing_name(self, client: TestClient, org_headers: dict):
        r = client.post("/api/v1/templates", headers=org_headers, json={"name": "  ", "type": "requirement"})
        assert r.status_code == 400


class TestRenameDeleteTemplate:
    """模板管理：默认画布 / 改名 / 删除（绑定/实例保护）。"""

    def _mk(self, client: TestClient, headers: dict) -> dict:
        name = f"操作模板-{__import__('uuid').uuid4().hex[:4]}"
        return client.post("/api/v1/templates", headers=headers, json={"name": name, "type": "requirement"}).json()["data"]["template"]

    def test_create_has_default_canvas(self, client: TestClient, org_headers: dict):
        """新建模板自带默认画布：开始 → 任务 → 结束（可发布闭环）。"""
        t = self._mk(client, org_headers)
        cv = client.get(f"/api/v1/templates/{t['id']}/versions/v1/canvas", headers=org_headers).json()["data"]
        types = [n["type"] for n in cv["nodes"]]
        assert "start" in types and "task" in types and "end" in types
        assert len(cv["edges"]) == 2

    def test_rename(self, client: TestClient, org_headers: dict):
        t = self._mk(client, org_headers)
        new = f"{t['name']}-改"
        r = client.patch(f"/api/v1/templates/{t['id']}", headers=org_headers, json={"name": new})
        assert r.status_code == 200
        pool = client.get("/api/v1/templates/pool", headers=org_headers).json()["data"]["items"]
        assert any(x["id"] == t["id"] and x["name"] == new for x in pool)

    def test_rename_duplicate(self, client: TestClient, org_headers: dict):
        a, b = self._mk(client, org_headers), self._mk(client, org_headers)
        r = client.patch(f"/api/v1/templates/{b['id']}", headers=org_headers, json={"name": a["name"]})
        assert r.status_code == 409

    def test_delete(self, client: TestClient, org_headers: dict):
        t = self._mk(client, org_headers)
        r = client.delete(f"/api/v1/templates/{t['id']}", headers=org_headers)
        assert r.status_code == 200
        pool = client.get("/api/v1/templates/pool", headers=org_headers).json()["data"]["items"]
        assert not any(x["id"] == t["id"] for x in pool)

    def test_delete_bound_rejected(self, client: TestClient, org_headers: dict):
        """已被项目绑定的模板删除被拒（409）。"""
        t = self._mk(client, org_headers)
        r = client.post("/api/v1/projects", headers=org_headers, json={
            "name": f"绑-{t['id'][-4:]}", "code": f"B{__import__('uuid').uuid4().hex[:4]}", "status": "active",
            "template_bindings": [{"template_id": t["id"], "version": "v1"}],
        })
        assert r.status_code == 200
        r = client.delete(f"/api/v1/templates/{t['id']}", headers=org_headers)
        assert r.status_code == 409


class TestStartSchemaFallback:
    """start-schema 最终兜底：模板无任何起始表单时返回默认标题字段。"""

    def test_start_schema_empty_template_gets_default_title(self, client: TestClient, org_headers: dict):
        """创建模板后（默认画布 start 有 title schema），画布 schema 清空 + 模板级也空 → 返回默认 title。"""
        name = f"兜底模板-{__import__('uuid').uuid4().hex[:4]}"
        tid = client.post("/api/v1/templates", headers=org_headers, json={"name": name, "type": "requirement"}).json()["data"]["template"]["id"]
        # 画布 start 节点 schema 置空（仍保留节点）→ 触发回退
        cv = client.get(f"/api/v1/templates/{tid}/versions/v1/canvas", headers=org_headers).json()["data"]
        for n in cv["nodes"]:
            if n.get("type") == "start":
                n["cfg"]["schema"] = []
        client.put(f"/api/v1/templates/{tid}/versions/v1/canvas", headers=org_headers, json={"nodes": cv["nodes"], "edges": cv["edges"], "fallbacks": cv["fallbacks"]})
        r = client.get(f"/api/v1/templates/{tid}/start-schema", headers=org_headers)
        d = r.json()["data"]
        assert d["fallback"] is True
        assert d["schema"] and len(d["schema"]) > 0
        assert d["schema"][0]["key"] == "title"
        assert d["schema"][0]["required"] is True


class TestStartSchemaTitleGuarantee:
    """start-schema 必须包含 title 字段（后端创建流程强制 title 必填）。"""

    def test_start_schema_missing_title_gets_prepended(self, client: TestClient, org_headers: dict):
        """模板 start 表单非空但缺 title → start-schema 自动补 title 到最前。"""
        name = f"缺标题模板-{__import__('uuid').uuid4().hex[:4]}"
        tid = client.post("/api/v1/templates", headers=org_headers, json={"name": name, "type": "requirement"}).json()["data"]["template"]["id"]
        cv = client.get(f"/api/v1/templates/{tid}/versions/v1/canvas", headers=org_headers).json()["data"]
        for n in cv["nodes"]:
            if n.get("type") == "start":
                n["cfg"]["schema"] = [{"key": "description", "label": "描述", "type": "textarea", "required": True}]
        client.post(f"/api/v1/templates/{tid}/versions/save-and-publish", headers=org_headers, json=cv)
        d = client.get(f"/api/v1/templates/{tid}/start-schema", headers=org_headers).json()["data"]
        keys = [f["key"] for f in d["schema"]]
        assert keys[0] == "title", f"title 应补到最前: {keys}"
        assert "description" in keys
