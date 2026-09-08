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
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.clients.minio import get_minio
from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import DocItem, TaskItem, User
from flowhub_api.services.audit import AuditService
from flowhub_api.services.document_access import create_document_content_token, document_content_link, valid_document_content_token
from flowhub_api.services.workflow import WorkflowService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

ALLOWED_EXT = {".pdf", ".md", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".fig", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".yaml", ".txt"}
ALLOWED_MIME = {"application/pdf", "text/markdown", "text/plain", "application/zip",
                "application/x-zip-compressed",  # Windows 浏览器对 .zip 的常见 MIME
                "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "image/png", "image/jpeg", "image/webp", "application/octet-stream"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


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
    else:
        # 文档中心（不带 wi 的全量视图）不展示「节点表单附件」：它们是节点表单的
        # 采纳产物，只在对应工作项/节点文档里可见，避免 Expert 每次产出都涌入文档中心
        stmt = stmt.where(DocItem.kind.not_in(("节点表单附件", "节点表单图片")))
    total = len((await session.execute(stmt)).scalars().all())
    # 固定排序保证分页稳定（观察项 E 修复：原实现无 order_by，分页结果顺序不稳定）
    rows = (await session.execute(
        stmt.order_by(DocItem.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return ok({"items": [_brief(d) for d in rows], "total": total, "page": page, "page_size": page_size})


async def _save_uploaded_document(
    file: UploadFile, user: User, session: AsyncSession, *, project: str, kind: str, wi: str,
    image_only: bool = False,
) -> dict:
    """Validate and persist a document, with an optional strict image-only boundary."""
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
    if image_only:
        if ext not in IMAGE_EXT or mime not in IMAGE_MIME:
            raise BizError(BizCode.VALIDATION, "仅支持 PNG、JPEG、WebP 图片")
    else:
        if ext not in ALLOWED_EXT:
            hint = "（Axure 请上传「发布 → 生成 HTML 文件」导出的 zip 包）" if ext == ".rp" else ""
            raise BizError(BizCode.VALIDATION, f"不允许的扩展名：{ext}{hint}")
        if mime and mime not in ALLOWED_MIME:
            raise BizError(BizCode.VALIDATION, f"不允许的 MIME：{mime}")
    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise BizError(BizCode.VALIDATION, "文件过大（疑似压缩炸弹，上限 50MB）")
    if image_only:
        _validate_image_bytes(data, ext)

    doc = DocItem(
        id=f"d{uuid4().hex[:8]}", name=file.filename or "未命名", project=project or "未归档",
        version="v1", level="L2", scan="已扫描", uploader=user.name,
        size=f"{len(data) / 1024 / 1024:.1f}MB" if len(data) > 1024 * 1024 else f"{len(data) // 1024}KB",
        time="刚刚", kind=kind, wi=wi or None,
    )
    minio = get_minio()
    if minio:
        bucket = get_settings().minio_bucket
        doc.object_name = f"{doc.id}/{file.filename}"
        minio.put_object(bucket, doc.object_name, __import__("io").BytesIO(data), len(data))
    session.add(doc)
    await AuditService(session).record(actor=user.name, action="document:upload", target=f"{doc.name} · {doc.project}", result="success")
    await session.commit()
    return _brief(doc)


def _validate_image_bytes(data: bytes, ext: str) -> None:
    """Verify decoded image content, not merely untrusted filename/MIME metadata."""
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BizError(BizCode.VALIDATION, "图片内容无效或已损坏") from exc
    if image_format != IMAGE_FORMATS[ext]:
        raise BizError(BizCode.VALIDATION, "图片内容与文件类型不匹配")


async def _validate_image_upload_target(session: AsyncSession, user: User, project: str, wi: str) -> None:
    """Bind work-item-scoped uploads to a visible active task before saving bytes."""
    if not wi:
        return
    tasks = (await session.execute(select(TaskItem).where(
        TaskItem.wi_id == wi,
        TaskItem.status.not_in(("completed", "cancelled")),
    ))).scalars().all()
    if not tasks or any(task.project != project for task in tasks):
        raise BizError(BizCode.VALIDATION, "图片上传任务或项目不匹配")
    is_admin = any(role.id in {"system_admin", "organization_admin"} for role in user.roles)
    if not is_admin:
        recipient_ids = {
            candidate.id
            for task in tasks
            for candidate in await WorkflowService(session).resolve_task_recipients(task)
        }
        if user.id not in recipient_ids:
            raise BizError(BizCode.FORBIDDEN, "仅当前节点处理人可上传图片")


@router.post("/upload")
async def upload_document(
    file: Annotated[UploadFile, File(...)], user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)], project: str = Form(""), kind: str = Form("文档"), wi: str = Form(""),
):
    build_authorizer(user).require("document:upload")
    # 保留分类只能由专用端点写入；否则客户端可伪造 kind 绕过图片内容/归属校验。
    if kind == "节点表单图片":
        raise BizError(BizCode.VALIDATION, "节点表单图片请使用专用图片上传接口")
    doc = await _save_uploaded_document(file, user, session, project=project, kind=kind, wi=wi)
    return ok({"doc": doc}, "上传成功：扩展名/MIME/大小校验通过")


@router.post("/images/upload")
async def upload_image(
    file: Annotated[UploadFile, File(...)], user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)], project: str = Form(""), wi: str = Form(""),
):
    """Image-only endpoint for image schema fields; callers cannot loosen its type policy."""
    build_authorizer(user).require("document:upload")
    await _validate_image_upload_target(session, user, project, wi)
    doc = await _save_uploaded_document(file, user, session, project=project, kind="节点表单图片", wi=wi, image_only=True)
    return ok({"doc": doc}, "图片上传成功")

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


@router.get("/{doc_id}/archive")
async def list_archive_entries(
    doc_id: str,
    token: str = "",
    session: AsyncSession = Depends(get_db),
):
    """Return a safe, read-only listing for a normal ZIP package.

    Axure packages continue to use the HTML preview route; packages without an
    entry page can still be inspected in the document viewer and downloaded.
    """
    if not valid_document_content_token(token, doc_id):
        raise BizError(BizCode.UNAUTH, "文档链接无效或已过期", http_status=401)
    doc = await session.get(DocItem, doc_id)
    if doc is None or doc.deleted:
        raise BizError(BizCode.NOT_FOUND, "文档不存在")
    if doc.scan == "含毒":
        raise BizError(BizCode.FORBIDDEN, "文档含毒，已禁止访问")
    if not doc.name.lower().endswith(".zip"):
        raise BizError(BizCode.VALIDATION, "仅支持 ZIP 压缩包浏览")

    entries = _list_zip_entries_checked(await _load_document_bytes(doc))
    paths = {str(entry["path"]) for entry in entries}
    lower_paths = {path.lower() for path in paths}
    has_root_index = "index.html" in lower_paths
    if not has_root_index and paths:
        root = next(iter(paths)).split("/", 1)[0]
        has_root_index = (
            f"{root}/index.html".lower() in lower_paths
            and all(path.startswith(f"{root}/") for path in paths)
        )
    return ok({"preview_type": "axure" if has_root_index else "archive", "entries": entries})


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


def _list_zip_entries_checked(data: bytes) -> list[dict[str, int | str]]:
    """Read ZIP metadata with the same bomb and traversal limits as preview."""
    max_total = 200 * 1024 * 1024
    max_entries = 2000
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            entries: list[dict[str, int | str]] = []
            total = 0
            entry_count = 0
            for info in zf.infolist():
                name = _zip_entry_name(info).replace("\\", "/")
                parts = Path(name).parts
                if "__MACOSX" in parts or parts[-1:] == (".DS_Store",):
                    continue
                entry_count += 1
                if entry_count > max_entries:
                    raise BizError(BizCode.VALIDATION, "压缩包条目过多，拒绝浏览")
                if not name or name.startswith("/") or any(part in {"", ".", ".."} for part in parts):
                    raise BizError(BizCode.VALIDATION, "压缩包包含非法路径，拒绝浏览")
                total += info.file_size
                if total > max_total:
                    raise BizError(BizCode.VALIDATION, "解压后体积超限（上限 200MB），疑似压缩炸弹")
                if not info.is_dir():
                    entries.append({"path": name, "size": info.file_size})
            return sorted(entries, key=lambda entry: str(entry["path"]).lower())
    except zipfile.BadZipFile as exc:
        raise BizError(BizCode.VALIDATION, "压缩包损坏或格式不支持") from exc


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
