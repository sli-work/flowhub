"""文档中心测试：列表 / 上传校验链 / 短时链接 / 删除恢复 / 权限。"""
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import auth_headers


class TestDocumentList:
    def test_list_requires_auth(self, client: TestClient):
        r = client.get("/api/v1/documents")
        assert r.status_code == 401

    def test_list(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/documents", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["total"] >= 1
        # 前端默认每页 5 条
        assert len(data["items"]) <= 5

    def test_list_filter_project(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/documents", headers=leader_headers, params={"project": "订单"})
        data = r.json()["data"]
        assert all("订单" in d["project"] for d in data["items"])

    def test_list_pagination(self, client: TestClient, leader_headers: dict):
        r = client.get("/api/v1/documents", headers=leader_headers, params={"page": 2, "page_size": 5})
        assert r.status_code == 200


class TestUpload:
    def test_upload_success(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/documents/upload", headers=leader_headers,
                        files={"file": ("测试文档.md", "# 测试内容\nhello".encode("utf-8"), "text/markdown")},
                        data={"project": "订单中心", "kind": "需求文档"})
        assert r.status_code == 200
        doc = r.json()["data"]["doc"]
        assert doc["name"] == "测试文档.md"
        assert doc["uploader"] == "张伟"

    def test_upload_reject_extension(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/documents/upload", headers=leader_headers,
                        files={"file": ("evil.exe", io.BytesIO(b"MZ"), "application/octet-stream")})
        assert r.status_code == 400
        assert "不允许的扩展名" in r.json()["message"]

    def test_upload_reject_mime(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/documents/upload", headers=leader_headers,
                        files={"file": ("x.md", io.BytesIO(b"data"), "application/x-msdownload")})
        assert r.status_code == 400
        assert "不允许的 MIME" in r.json()["message"]

    def test_upload_reject_path_traversal_name(self, client: TestClient, leader_headers: dict):
        """路径穿越文件名应被拒绝（扩展名白名单外）。"""
        r = client.post("/api/v1/documents/upload", headers=leader_headers,
                        files={"file": ("../../etc/passwd", io.BytesIO(b"x"), "text/plain")})
        assert r.status_code == 400

    def test_upload_requires_auth(self, client: TestClient):
        r = client.post("/api/v1/documents/upload", files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
        assert r.status_code == 401


class TestLinkAndContent:
    def test_download_requires_bearer_auth(self, client: TestClient):
        r = client.get("/api/v1/documents/d1/download")
        assert r.status_code == 401

    def test_create_link(self, client: TestClient, leader_headers: dict):
        r = client.post("/api/v1/documents/d1/link", headers=leader_headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert "token=" in data["link"]
        assert data["expire"] == "5 分钟"

    def test_content_rejects_missing_or_tampered_token(self, client: TestClient, leader_headers: dict):
        link = client.post("/api/v1/documents/d1/link", headers=leader_headers).json()["data"]["link"]
        assert client.get("/api/v1/documents/d1/content").status_code == 401
        token = link.split("token=", 1)[1]
        assert client.get(f"/api/v1/documents/d1/content?token={token}x").status_code == 401

    def test_link_requires_perm(self, client: TestClient, dev_headers: dict):
        """developer 有 document:download → 应可生成链接（权限矩阵全员 true）。"""
        r = client.post("/api/v1/documents/d1/link", headers=dev_headers)
        assert r.status_code == 200


class TestDeleteRestore:
    def test_delete_requires_perm(self, client: TestClient, leader_headers: dict):
        """leader 无 document:delete → 403。"""
        r = client.delete("/api/v1/documents/d1", headers=leader_headers)
        assert r.status_code == 403
        assert r.json()["code"] == 40302

    def test_delete_and_restore_flow(self, client: TestClient, org_headers: dict):
        # 先上传一条再删（避免动 seed 数据）；用 name 过滤精确定位（列表默认 page_size=5 且无排序）
        name = f"del-{uuid.uuid4().hex[:6]}.md"
        up = client.post("/api/v1/documents/upload", headers=org_headers,
                         files={"file": (name, b"x", "text/markdown")})
        doc_id = up.json()["data"]["doc"]["id"]
        r = client.delete(f"/api/v1/documents/{doc_id}", headers=org_headers)
        assert r.status_code == 200
        lst = client.get("/api/v1/documents", headers=org_headers, params={"name": name[:-3]}).json()["data"]["items"]
        assert doc_id not in {d["id"] for d in lst}
        # 恢复
        r2 = client.post(f"/api/v1/documents/{doc_id}/restore", headers=org_headers)
        assert r2.status_code == 200
        lst2 = client.get("/api/v1/documents", headers=org_headers, params={"name": name[:-3]}).json()["data"]["items"]
        assert doc_id in {d["id"] for d in lst2}

    def test_delete_not_found(self, client: TestClient, org_headers: dict):
        r = client.delete("/api/v1/documents/ghost", headers=org_headers)
        assert r.status_code == 404
