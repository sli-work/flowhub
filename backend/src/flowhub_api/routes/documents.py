"""文档路由（docs/06 §一）：上传校验链 / 列表 / 短时链接 / 删除恢复 / Axure zip 预览。"""
import mimetypes
import os
import shutil
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.clients.minio import get_minio
from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import DocItem, User
from flowhub_api.services.audit import AuditService
from flowhub_api.services.document_access import create_document_content_token, document_content_link, valid_document_content_token

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

ALLOWED_EXT = {".pdf", ".md", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".fig", ".zip", ".png", ".jpg", ".yaml", ".txt"}
ALLOWED_MIME = {"application/pdf", "text/markdown", "text/plain", "application/zip",
                "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "image/png", "image/jpeg", "application/octet-stream"}


def _brief(d: DocItem) -> dict:
    return {
        "id": d.id, "name": d.name, "project": d.project, "version": d.version,
        "level": d.level, "scan": d.scan, "uploader": d.uploader, "size": d.size,
        "time": d.time, "kind": d.kind, "wi": d.wi,
        # 扩展名（前端类型图标/预览能力判断用）
        "ext": d.name.rsplit(".", 1)[-1].lower() if "." in d.name else "",
    }


@router.get("")
async def list_documents(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    name: str = "", project: str = "", level: str = "", scan: str = "", wi: str = "",
    page: int = Query(1, ge=1), page_size: int = Query(5, ge=1, le=100),
):
    stmt = select(DocItem).where(DocItem.deleted == False)  # noqa: E712
    if name:
        stmt = stmt.where(DocItem.name.contains(name))
    if project:
        stmt = stmt.where(DocItem.project.contains(project))
    if level:
        stmt = stmt.where(DocItem.level == level)
    if scan:
        stmt = stmt.where(DocItem.scan == scan)
    if wi:
        stmt = stmt.where(DocItem.wi == wi)
    total = len((await session.execute(stmt)).scalars().all())
    # 固定排序保证分页稳定（观察项 E 修复：原实现无 order_by，分页结果顺序不稳定）
    rows = (await session.execute(
        stmt.order_by(DocItem.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return ok({"items": [_brief(d) for d in rows], "total": total, "page": page, "page_size": page_size})


@router.post("/upload")
async def upload_document(
    file: Annotated[UploadFile, File(...)],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    # 明确声明为表单字段：前端 FormData 传入的 wi/kind/project 此前被当作 query 参数忽略，
    # 导致任务处理中上传的文档无法关联工作项
    project: str = Form(""),
    kind: str = Form("文档"),
    wi: str = Form(""),
):
    auth = build_authorizer(user)
    auth.require("document:upload")
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    # 校验链：扩展名 → MIME（docs/06 §1.1）
    if ext not in ALLOWED_EXT:
        hint = "（Axure 请上传「发布 → 生成 HTML 文件」导出的 zip 包）" if ext == ".rp" else ""
        raise BizError(BizCode.VALIDATION, f"不允许的扩展名：{ext}{hint}")
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if mime and mime not in ALLOWED_MIME:
        raise BizError(BizCode.VALIDATION, f"不允许的 MIME：{mime}")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise BizError(BizCode.VALIDATION, "文件过大（疑似压缩炸弹，上限 50MB）")

    doc = DocItem(
        id=f"d{uuid4().hex[:8]}", name=file.filename or "未命名", project=project or "未归档",
        version="v1", level="L2", scan="已扫描", uploader=user.name,
        size=f"{len(data) / 1024 / 1024:.1f}MB" if len(data) > 1024 * 1024 else f"{len(data) // 1024}KB",
        time="刚刚", kind=kind, wi=wi or None,
    )
    # MinIO 存储（未配置时跳过，仅入库元数据）
    minio = get_minio()
    if minio:
        bucket = get_settings().minio_bucket
        doc.object_name = f"{doc.id}/{file.filename}"
        minio.put_object(bucket, doc.object_name, __import__("io").BytesIO(data), len(data))
    session.add(doc)
    await AuditService(session).record(
        actor=user.name, action="document:upload", target=f"{doc.name} · {doc.project}", result="success",
    )
    await session.commit()
    return ok({"doc": _brief(doc)}, "上传成功：扩展名/MIME/大小校验通过")


@router.post("/{doc_id}/link")
async def create_link(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("document:download")
    doc = await session.get(DocItem, doc_id)
    if doc is None or doc.deleted:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    if doc.scan == "含毒":
        raise BizError(BizCode.FORBIDDEN, "文档含毒，已禁止访问")
    await AuditService(session).record(
        actor=user.name, action="document:download", target=f"{doc.name} · 短时链接", result="success",
    )
    await session.commit()
    token = create_document_content_token(doc_id)
    resp = JSONResponse(content=ok(
        {"link": f"/api/v1/documents/{doc_id}/content?token={token}", "expire": "5 分钟"},
        "权限代理校验通过，短时链接已生成",
    ))
    # Axure 页面内 resources/... 相对引用不携带 query token → 同源 iframe 靠 cookie 带同一令牌
    resp.set_cookie(
        f"fh_doc_preview_{doc_id}", token, max_age=300,
        httponly=True, samesite="lax", path=f"/api/v1/documents/{doc_id}/preview",
    )
    return resp


@router.get("/{doc_id}/content")
async def read_document_content(
    doc_id: str,
    token: str = "",
    session: AsyncSession = Depends(get_db),
):
    """以短时令牌读取单个文档对象，不暴露对象存储凭证。"""
    if not valid_document_content_token(token, doc_id):
        raise BizError(BizCode.UNAUTH, "文档链接无效或已过期", http_status=401)
    doc = await session.get(DocItem, doc_id)
    if doc is None or doc.deleted:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    if doc.scan == "含毒":
        raise BizError(BizCode.FORBIDDEN, "文档含毒，已禁止访问")
    if not doc.object_name:
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用")
    minio = get_minio()
    if minio is None:
        raise BizError(BizCode.NOT_FOUND, "文档存储不可用")
    try:
        obj = minio.get_object(get_settings().minio_bucket, doc.object_name)
        data = obj.read()
        obj.close()
        obj.release_conn()
    except Exception as exc:  # noqa: BLE001
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用") from exc
    media_type = mimetypes.guess_type(doc.name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.name)}"}
    return StreamingResponse(BytesIO(data), media_type=media_type, headers=headers)


@router.get("/{doc_id}/download")
async def download_document(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """Download a document through the authenticated API boundary.

    The browser cannot attach the Bearer token to a plain anchor reliably, so
    the frontend fetches this endpoint and saves the returned Blob.
    """
    build_authorizer(user).require("document:download")
    doc = await session.get(DocItem, doc_id)
    if doc is None or doc.deleted:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    if doc.scan == "含毒":
        raise BizError(BizCode.FORBIDDEN, "文档含毒，已禁止访问")
    if not doc.object_name:
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用")
    minio = get_minio()
    if minio is None:
        raise BizError(BizCode.NOT_FOUND, "文档存储不可用")
    try:
        obj = minio.get_object(get_settings().minio_bucket, doc.object_name)
        data = obj.read()
        obj.close()
        obj.release_conn()
    except Exception as exc:  # noqa: BLE001
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用") from exc
    await AuditService(session).record(actor=user.name, action="document:download", target=doc.name, result="success")
    await session.commit()
    media_type = mimetypes.guess_type(doc.name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.name)}"}
    return StreamingResponse(BytesIO(data), media_type=media_type, headers=headers)


@router.get("/{doc_id}/preview/{entry_path:path}")
async def preview_document_entry(
    doc_id: str,
    entry_path: str,
    request: Request,
    token: str = "",
    session: AsyncSession = Depends(get_db),
):
    """Axure 导出 HTML 包预览：zip 解压缓存后按路径提供静态文件（iframe 无法带 Bearer，走短时 token）。

    index.html 由前端拼 query token；页面内相对引用的子资源（JS/CSS/图片）不带 token，
    回退读 /link 下发的同源预览 cookie（fh_doc_preview_{doc_id}）。"""
    if not token:
        token = request.cookies.get(f"fh_doc_preview_{doc_id}") or ""
    if not valid_document_content_token(token, doc_id):
        raise BizError(BizCode.UNAUTH, "文档链接无效或已过期", http_status=401)
    doc = await session.get(DocItem, doc_id)
    if doc is None or doc.deleted:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    if doc.scan == "含毒":
        raise BizError(BizCode.FORBIDDEN, "文档含毒，已禁止访问")
    if not doc.name.lower().endswith(".zip"):
        raise BizError(BizCode.VALIDATION, "仅支持 zip 包预览（Axure 请上传导出的 HTML zip）")
    # 缓存目录带版本号：解压逻辑变更（编码修复等）后自动重建旧缓存
    cache_dir = Path(tempfile.gettempdir()) / "flowhub-axure-preview" / f"{doc_id}_v2"
    index = cache_dir / "index.html"
    if not index.is_file():
        data = await _load_document_bytes(doc)
        _extract_zip_checked(data, cache_dir)
        if not index.is_file():
            shutil.rmtree(cache_dir, ignore_errors=True)
            raise BizError(BizCode.VALIDATION, "包内缺少 index.html，仅支持 Axure 导出的 HTML 包预览")
    target = (cache_dir / entry_path).resolve()
    if not str(target).startswith(str(cache_dir.resolve()) + os.sep):
        raise BizError(BizCode.NOT_FOUND, "路径非法")
    if not target.is_file():
        raise BizError(BizCode.NOT_FOUND, "文件不存在")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, headers={"Cache-Control": "no-store"})


async def _load_document_bytes(doc: DocItem) -> bytes:
    if not doc.object_name:
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用")
    minio = get_minio()
    if minio is None:
        raise BizError(BizCode.NOT_FOUND, "文档存储不可用")
    try:
        obj = minio.get_object(get_settings().minio_bucket, doc.object_name)
        data = obj.read()
        obj.close()
        obj.release_conn()
    except Exception as exc:  # noqa: BLE001
        raise BizError(BizCode.NOT_FOUND, "文档内容不可用") from exc
    return data


def _zip_entry_name(info: zipfile.ZipInfo) -> str:
    """zip 条目名编码修复：未设 UTF-8 标志（0x800）的条目，zipfile 默认按 CP437 解码会得到乱码名，
    导致 Axure 播放器按原始中文名请求 404。还原顺序：UTF-8 → GBK（Windows 中文导出），均失败回退原名。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.filename


def _extract_zip_checked(data: bytes, cache_dir: Path) -> None:
    """解压前校验防压缩炸弹：解压总量 ≤ 200MB、条目 ≤ 2000、防路径穿越。
    兼容 macOS「压缩文件夹」产物：忽略 __MACOSX / .DS_Store；若所有条目都包在
    同一顶层目录下且该目录含 index.html，则剥离该外层目录（Axure 包根需有 index.html）。"""
    MAX_TOTAL = 200 * 1024 * 1024
    MAX_ENTRIES = 2000
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            entries: list[tuple[str, zipfile.ZipInfo]] = []
            for info in zf.infolist():
                if "__MACOSX" in info.filename.split("/") or info.filename.split("/")[-1] == ".DS_Store":
                    continue
                entries.append((_zip_entry_name(info), info))
            if len(entries) > MAX_ENTRIES:
                raise BizError(BizCode.VALIDATION, "压缩包条目过多，拒绝解压")
            total = 0
            for _, info in entries:
                total += info.file_size
                if total > MAX_TOTAL:
                    raise BizError(BizCode.VALIDATION, "解压后体积超限（上限 200MB），疑似压缩炸弹")
            names = [name for name, _ in entries]
            prefix = ""
            if names:
                seg = names[0].split("/", 1)[0]
                if seg and f"{seg}/index.html" in names and all(
                    n == seg or n.startswith(seg + "/") for n in names
                ):
                    prefix = seg + "/"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_root = str(cache_dir.resolve()) + os.sep
            for name, info in entries:
                if prefix and name.startswith(prefix):
                    name = name[len(prefix):]
                if not name:
                    continue
                target = (cache_dir / name).resolve()
                if not str(target).startswith(cache_root):
                    raise BizError(BizCode.VALIDATION, "压缩包包含非法路径，拒绝解压")
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise BizError(BizCode.VALIDATION, "压缩包损坏或格式不支持") from exc


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("document:delete")
    doc = await session.get(DocItem, doc_id)
    if doc is None:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    doc.deleted = True
    await AuditService(session).record(
        actor=user.name, action="document:delete", target=doc.name, result="success",
    )
    await session.commit()
    return ok(message="文档已删除（可恢复）")


@router.post("/{doc_id}/restore")
async def restore_document(
    doc_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("document:restore")
    doc = await session.get(DocItem, doc_id)
    if doc is None:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    doc.deleted = False
    await AuditService(session).record(
        actor=user.name, action="document:restore", target=doc.name, result="success",
    )
    await session.commit()
    return ok(message="文档已恢复")
