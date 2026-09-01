"""预定义标签路由：工作项绑定 tag 需先在此登记（如版本号 v1.2.0）。"""
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizCode, BizError, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import TagItem, User
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/tags", tags=["tags"])

TAG_COLORS = {"suc", "warn", "err", "info", "pur", "cyn", "orgx", "gry", "blk"}


class TagReq(BaseModel):
    name: str
    color: str = "gry"


def _brief(t: TagItem) -> dict:
    return {"id": t.id, "name": t.name, "color": t.color, "creator": t.creator, "time": t.time}


@router.get("")
async def list_tags(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = (await session.execute(
        select(TagItem).where(TagItem.deleted == False).order_by(TagItem.id)  # noqa: E712
    )).scalars().all()
    return ok({"items": [_brief(t) for t in rows]})


@router.post("")
async def create_tag(
    body: TagReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("workflow_template:create")
    name = body.name.strip()
    if not name:
        raise BizError(BizCode.VALIDATION, "标签名称不能为空")
    if len(name) > 64:
        raise BizError(BizCode.VALIDATION, "标签名称过长（上限 64 字符）")
    if body.color not in TAG_COLORS:
        raise BizError(BizCode.VALIDATION, f"不支持的颜色：{body.color}")
    dup = (await session.execute(
        select(TagItem).where(TagItem.name == name, TagItem.deleted == False)  # noqa: E712
    )).scalar_one_or_none()
    if dup:
        raise BizError(BizCode.VALIDATION, f"标签已存在：{name}")
    tag = TagItem(id=f"tag{uuid4().hex[:8]}", name=name, color=body.color, creator=user.name, time="刚刚")
    session.add(tag)
    await AuditService(session).record(actor=user.name, action="tag:create", target=name, result="success")
    await session.commit()
    return ok({"tag": _brief(tag)}, "标签已创建")


@router.delete("/{tag_id}")
async def delete_tag(
    tag_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    build_authorizer(user).require("workflow_template:create")
    tag = await session.get(TagItem, tag_id)
    if tag is None or tag.deleted:
        raise BizError(BizCode.NOT_FOUND, "标签不存在")
    tag.deleted = True
    await AuditService(session).record(actor=user.name, action="tag:delete", target=tag.name, result="success")
    await session.commit()
    return ok(message="标签已删除")
