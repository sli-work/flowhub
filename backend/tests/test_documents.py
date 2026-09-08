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


class TestAxurePreview:
    """Axure zip 预览：外层目录剥离（macOS 压缩文件夹）/ __MACOSX 忽略 / 子资源 cookie 鉴权。"""

    @staticmethod
    def _make_axure_zip(wrap: bool) -> bytes:
        import io as _io
        import zipfile as _zf

        buf = _io.BytesIO()
        paths = (
            ["AxureDemo/index.html", "AxureDemo/resources/style.css", "AxureDemo/data.js", "__MACOSX/AxureDemo/._index.html"]
            if wrap else
            ["index.html", "resources/style.css", "data.js", "__MACOSX/._index.html"]
        )
        with _zf.ZipFile(buf, "w") as zf:
            for p in paths:
                zf.writestr(p, f"/* {p} */")
        return buf.getvalue()

    def _upload(self, client: TestClient, leader_headers: dict, data: bytes) -> str:
        r = client.post("/api/v1/documents/upload", headers=leader_headers,
                        files={"file": ("axure-demo2.zip", io.BytesIO(data), "application/zip")},
                        data={"project": "订单中心", "kind": "原型"})
        assert r.status_code == 200, r.text
        return r.json()["data"]["doc"]["id"]

    def test_extract_strips_wrapper_dir_and_macosx(self, tmp_path):
        from flowhub_api.routes.documents import _extract_zip_checked

        cache = tmp_path / "doc1"
        _extract_zip_checked(self._make_axure_zip(wrap=True), cache)
        assert (cache / "index.html").is_file(), "外层目录应被剥离，index.html 位于包根"
        assert (cache / "resources" / "style.css").is_file()
        assert not (cache / "AxureDemo").exists()
        assert not any("__MACOSX" in str(p) for p in cache.rglob("*")), "__MACOSX 应被忽略"

        cache2 = tmp_path / "doc2"
        _extract_zip_checked(self._make_axure_zip(wrap=False), cache2)
        assert (cache2 / "index.html").is_file(), "无外层目录时保持原样"

    def test_gbk_entry_name_decoded(self):
        """未设 UTF-8 标志的中文条目名（实际字节为 UTF-8 或 GBK）解压时应还原为中文名。"""
        from types import SimpleNamespace

        from flowhub_api.routes.documents import _zip_entry_name

        cn_name = "待处理告警-告警中心.html"
        utf8_mojibake = cn_name.encode("utf-8").decode("cp437")   # macOS 压缩未标志条目（本例 DRCC 包）
        gbk_mojibake = cn_name.encode("gbk").decode("cp437")      # Windows 中文导出
        assert _zip_entry_name(SimpleNamespace(filename=utf8_mojibake, flag_bits=0)) == cn_name
        assert _zip_entry_name(SimpleNamespace(filename=gbk_mojibake, flag_bits=0)) == cn_name
        assert _zip_entry_name(SimpleNamespace(filename=cn_name, flag_bits=0x800)) == cn_name
        assert _zip_entry_name(SimpleNamespace(filename="index.html", flag_bits=0)) == "index.html"

    def test_preview_index_and_subresource_auth(self, client: TestClient, leader_headers: dict):
        doc_id = self._upload(client, leader_headers, self._make_axure_zip(wrap=True))
        r = client.post(f"/api/v1/documents/{doc_id}/link", headers=leader_headers)
        assert r.status_code == 200, r.text
        token = r.json()["data"]["link"].split("token=", 1)[1]

        archive = client.get(f"/api/v1/documents/{doc_id}/archive?token={token}")
        assert archive.json()["data"]["preview_type"] == "axure"

        # index.html：query token 可访问
        idx = client.get(f"/api/v1/documents/{doc_id}/preview/index.html?token={token}")
        assert idx.status_code == 200, idx.text

        # 子资源：无 query token 且无 cookie → 401；带 /link 下发的预览 cookie → 200
        cookie_name = f"fh_doc_preview_{doc_id}"
        cookie_value = client.cookies.get(cookie_name)
        assert cookie_value, "/link 应下发预览 cookie"
        client.cookies.clear()
        bare = client.get(f"/api/v1/documents/{doc_id}/preview/resources/style.css")
        assert bare.status_code == 401
        sub = client.get(f"/api/v1/documents/{doc_id}/preview/resources/style.css",
                         cookies={cookie_name: cookie_value})
        assert sub.status_code == 200, sub.text

    def test_standard_zip_lists_entries_instead_of_requiring_axure_index(self, client: TestClient, leader_headers: dict):
        """普通 ZIP 不含 Axure index.html 时，仍可在文件浏览器中查看包内文件。"""
        buf = io.BytesIO()
        import zipfile as _zf

        with _zf.ZipFile(buf, "w") as archive:
            archive.writestr("交付说明.txt", "release notes")
            archive.writestr("assets/logo.png", b"png")
        doc_id = self._upload(client, leader_headers, buf.getvalue())
        link = client.post(f"/api/v1/documents/{doc_id}/link", headers=leader_headers)
        token = link.json()["data"]["link"].split("token=", 1)[1]

        response = client.get(f"/api/v1/documents/{doc_id}/archive?token={token}")

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["preview_type"] == "archive"
        assert [entry["path"] for entry in data["entries"]] == ["assets/logo.png", "交付说明.txt"]

class TestImageUpload:
    @staticmethod
    def _image_bytes(image_format: str) -> bytes:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (2, 2), "#2463eb").save(buf, format=image_format)
        return buf.getvalue()

    def test_image_upload_accepts_webp_and_rejects_non_image(self, client: TestClient, leader_headers: dict):
        uploaded = client.post(
            "/api/v1/documents/images/upload", headers=leader_headers,
            files={"file": ("现场图.webp", self._image_bytes("WEBP"), "image/webp")},
            data={"project": "订单中心", "kind": "节点表单图片"},
        )
        assert uploaded.status_code == 200, uploaded.text
        uploaded_id = uploaded.json()["data"]["doc"]["id"]
        assert uploaded.json()["data"]["doc"]["name"] == "现场图.webp"
        listed = client.get("/api/v1/documents", headers=leader_headers).json()["data"]["items"]
        assert uploaded_id not in {doc["id"] for doc in listed}

        rejected = client.post(
            "/api/v1/documents/images/upload", headers=leader_headers,
            files={"file": ("说明.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert rejected.status_code == 400
        assert "图片" in rejected.json()["message"]

    def test_image_upload_rejects_spoofed_image_content(self, client: TestClient, leader_headers: dict):
        rejected = client.post(
            "/api/v1/documents/images/upload", headers=leader_headers,
            files={"file": ("伪造.png", b"<script>alert('not-an-image')</script>", "image/png")},
        )
        assert rejected.status_code == 400
        assert "图片内容" in rejected.json()["message"]

    def test_image_upload_rejects_unknown_task_target(self, client: TestClient, leader_headers: dict):
        rejected = client.post(
            "/api/v1/documents/images/upload", headers=leader_headers,
            files={"file": ("现场图.png", self._image_bytes("PNG"), "image/png")},
            data={"project": "订单中心", "wi": "missing-task"},
        )
        assert rejected.status_code == 400
        assert "任务或项目不匹配" in rejected.json()["message"]

    def test_regular_upload_cannot_forge_image_form_kind(self, client: TestClient, leader_headers: dict):
        rejected = client.post(
            "/api/v1/documents/upload", headers=leader_headers,
            files={"file": ("伪造.png", b"not an image", "image/png")},
            data={"project": "订单中心", "kind": "节点表单图片"},
        )
        assert rejected.status_code == 400
        assert "专用图片上传接口" in rejected.json()["message"]
