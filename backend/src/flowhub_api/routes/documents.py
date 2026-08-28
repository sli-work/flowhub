"""文档路由（docs/06 §一）：上传校验链 / 列表 / 短时链接 / 删除恢复。"""
import mimetypes
from io import BytesIO
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.clients.minio import get_minio
from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import DocItem, User
from flowhub_api.services.audit import AuditService
from flowhub_api.services.document_access import document_content_link, valid_document_content_token

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
        raise BizError(BizCode.VALIDATION, f"不允许的扩展名：{ext}")
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
    return ok({"link": document_content_link(doc_id), "expire": "5 分钟"},
              "权限代理校验通过，短时链接已生成")


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
