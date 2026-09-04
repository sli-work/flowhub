"""模板路由（docs/02 §四-五）：模板池 / 创建 / 改名 / 删除 / 版本 / 画布定义。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from flowhub_api.authz.authorizer import build_authorizer, get_current_user
from flowhub_api.core.response import BizError, BizCode, ok
from flowhub_api.db.session import get_db
from flowhub_api.models import GlobalTemplate, TemplateCanvas, TemplateVersion, User, WorkflowInstance
from flowhub_api.schemas.api import CreateTemplateReq
from flowhub_api.seed.init import gen_id
from flowhub_api.services.audit import AuditService

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

CANVAS_TEMPLATE_ID = "tpl-req"


@router.post("")
async def create_template(
    body: CreateTemplateReq,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """新建流程模板：创建全局模板 + 初始 v1 草稿版本（画布为空，可在画布编辑）。"""
    auth = build_authorizer(user)
    auth.require("workflow_template:create")
    name = body.name.strip()
    if not name:
        raise BizError(BizCode.VALIDATION, "模板名称必填")
    if body.type not in ("requirement", "issue", "change"):
        raise BizError(BizCode.VALIDATION, "模板类型必须是 requirement / issue / change")
    exists = (await session.execute(
        select(GlobalTemplate).where(GlobalTemplate.name == name)
    )).scalar_one_or_none()
    if exists:
        raise BizError(BizCode.DUPLICATE_TITLE, f"模板名称已存在：{name}", http_status=409)
    tpl_id = gen_id("tpl-")
    tpl = GlobalTemplate(id=tpl_id, name=name, type=body.type, versions=["v1"], start_schema=[], nodes=[])
    session.add(tpl)
    session.add(TemplateVersion(
        id=f"{tpl_id}:v1", template_id=tpl_id, version="v1", status="draft",
        updated="刚刚", updated_by=user.name, instances=0, nodes=0,
    ))
    # 默认画布：开始 → 任务 → 结束（新模板开箱即有闭环骨架，可继续添加节点/连线）
    def _node(nid: str, label: str, ntype: str, x: int) -> dict:
        return {
            "id": nid, "label": label, "type": ntype, "sub": ntype.upper(),
            "x": x, "y": 24, "width": 118, "height": 56,
            "cfg": {
                "typeLine": f"{ntype.upper()} · {label}",
                "purpose": "默认节点：双击属性面板编辑目的与产出物。",
                "handler": "人工", "fallback": "—", "sla": "48 小时",
                "schema": [{"key": "title", "label": "标题", "type": "input", "required": True}] if ntype == "start" else [],
                "output": "待配置",
            },
        }
    nodes = [_node("n1", "开始", "start", 24), _node("n2", "任务", "task", 192), _node("n3", "结束", "end", 360)]
    session.add(TemplateCanvas(
        version_id=f"{tpl_id}:v1", nodes=nodes,
        edges=[["n1", "n2"], ["n2", "n3"]], fallbacks=[],
    ))
    tpl.nodes = [{"id": n["id"], "label": n["label"], "type": n["type"]} for n in nodes]
    tpl.start_schema = [{"key": "title", "label": "标题", "type": "input", "required": True}]
    await AuditService(session).record(
        actor=user.name, action="workflow_template:create",
        target=f"{name}（{tpl_id}）· 类型 {body.type}", result="success",
    )
    await session.commit()
    return ok(
        {"template": {"id": tpl_id, "name": name, "type": body.type, "versions": ["v1"]}},
        f"已创建模板「{name}」（初始 v1 草稿：开始→任务→结束，可在画布继续编辑）",
    )


@router.patch("/{template_id}")
async def rename_template(
    template_id: str,
    body: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """模板改名（同名 409，需 workflow_template:update）。"""
    auth = build_authorizer(user)
    auth.require("workflow_template:update")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise BizError(BizCode.VALIDATION, "模板名称必填")
    exists = (await session.execute(
        select(GlobalTemplate).where(GlobalTemplate.name == new_name, GlobalTemplate.id != template_id)
    )).scalar_one_or_none()
    if exists:
        raise BizError(BizCode.DUPLICATE_TITLE, f"模板名称已存在：{new_name}", http_status=409)
    old_name = tpl.name
    tpl.name = new_name
    await AuditService(session).record(
        actor=user.name, action="workflow_template:update",
        target=f"模板改名：{old_name} → {new_name}（{template_id}）", result="success",
    )
    await session.commit()
    return ok({"id": tpl.id, "name": tpl.name}, f"模板已重命名：「{old_name}」→「{new_name}」")


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """删除模板：级联删除全部版本与画布；已被项目绑定或有流程实例时拒绝（409）。"""
    auth = build_authorizer(user)
    auth.require("workflow_template:update")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    binds = (await session.execute(
        text("SELECT count(*) FROM project_template_bindings WHERE template_id = :t"), {"t": template_id}
    )).scalar()
    if binds:
        raise BizError(BizCode.FORBIDDEN, f"模板已被 {binds} 个项目绑定，无法删除（请先解除绑定）", http_status=409)
    insts = (await session.execute(
        select(WorkflowInstance.id).where(WorkflowInstance.template_id == template_id)
    )).scalars().all()
    if insts:
        raise BizError(BizCode.FORBIDDEN, f"模板已有 {len(insts)} 个流程实例，无法删除", http_status=409)
    # 级联删除：画布 → 版本 → 模板
    await session.execute(text("DELETE FROM template_canvases WHERE version_id LIKE :p"), {"p": f"{template_id}:%"})
    await session.execute(delete(TemplateVersion).where(TemplateVersion.template_id == template_id))
    name = tpl.name
    await session.delete(tpl)
    await AuditService(session).record(
        actor=user.name, action="workflow_template:delete",
        target=f"删除模板：{name}（{template_id}）及全部版本", result="success",
    )
    await session.commit()
    return ok(message=f"已删除模板「{name}」及其全部版本")


@router.get("/pool")
async def template_pool(
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = (await session.execute(select(GlobalTemplate))).scalars().all()
    return ok({
        "items": [
            {
                "id": t.id, "name": t.name, "type": t.type,
                "versions": t.versions, "startSchema": t.start_schema, "nodes": t.nodes,
            }
            for t in rows
        ],
        "total": len(rows),
    })


@router.post("/{template_id}/versions")
async def create_template_version(
    template_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """创建新版本草稿：基于当前最新版本复制（docs/02 §4.3）。"""
    auth = build_authorizer(user)
    auth.require("workflow_template:create")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    # 计算下一个版本号（v4 → v5）：综合模板数组 + 版本表（兼容历史孤儿版本，避免重复）
    import re
    nums: list[int] = []
    table_vers = (await session.execute(
        select(TemplateVersion).where(TemplateVersion.template_id == template_id)
    )).scalars().all()
    all_vers = {tv.version for tv in table_vers} | set(tpl.versions)
    for v in all_vers:
        m = re.match(r"v(\d+)$", v)
        if m:
            nums.append(int(m.group(1)))
    next_v = f"v{(max(nums) + 1) if nums else 1}"
    new_id = f"{template_id}:{next_v}"
    if await session.get(TemplateVersion, new_id):
        raise BizError(BizCode.DUPLICATE_OPERATION, f"版本 {next_v} 已存在")
    tv = TemplateVersion(
        id=new_id, template_id=template_id, version=next_v, status="draft",
        updated="刚刚", updated_by=user.name, instances=0, nodes=len(tpl.nodes),
    )
    # 同步模板 versions 数组：保证数组与版本表一致（修复历史孤儿版本问题）
    if next_v not in tpl.versions:
        tpl.versions = [*tpl.versions, next_v]
    session.add(tv)
    await AuditService(session).record(
        actor=user.name, action="workflow_template:create",
        target=f"{tpl.name} {next_v} 草稿", result="success",
    )
    await session.commit()
    return ok({"version": next_v}, f"已创建 {next_v} 草稿（基于最新版本）")


@router.get("/{template_id}/start-schema")
async def template_start_schema(
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    version: str = Query(default="", max_length=16),
):
    """模板最新版本的起始节点表单（硬性要求，FormField[]）：

    指定 version 时严格取该版本画布 start 节点的 cfg.schema；
    未指定时优先取最新 published 版本画布；
    无 published 版本时回退最新草稿版本；画布无 schema 时回退模板级 start_schema（seed 静态值）。
    返回 version 供前端展示「来自最新版本 vX」。
    """
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    rows = (await session.execute(
        select(TemplateVersion).where(TemplateVersion.template_id == template_id)
    )).scalars().all()
    ordered = sorted(
        rows, key=lambda tv: int(tv.version[1:]) if tv.version[1:].isdigit() else 0, reverse=True,
    )
    chosen = next((tv for tv in ordered if tv.version == version), None) if version else next(
        (tv for tv in ordered if tv.status == "published"),
        next((tv for tv in ordered if tv.status == "draft"), None),
    )
    schema: list = []
    version: str | None = None
    status: str | None = None
    if chosen is not None:
        canvas = await session.get(TemplateCanvas, chosen.id)
        if canvas is not None and canvas.nodes:
            start = next((n for n in canvas.nodes if n.get("type") == "start"), None)
            if start is not None:
                schema = (start.get("cfg") or {}).get("schema") or []
        version, status = chosen.version, chosen.status
    fallback = not schema
    if fallback:
        schema = tpl.start_schema
    if not schema:
        # 最终兜底：模板完全没有起始表单时返回默认「标题」字段，
        # 保证新建工作项至少能填写标题（后端创建流程强制 title 必填）
        schema = [{"key": "title", "label": "标题", "type": "input", "required": True}]
    elif not any(f.get("key") == "title" for f in schema):
        # schema 非空但缺 title：补 title 到最前（否则前端表单无标题输入框，
        # 而后端创建流程强制 title 必填 → 用户无法提交，只会收到"标题必填"错误）
        schema = [{"key": "title", "label": "标题", "type": "input", "required": True}, *schema]
    return ok({
        "templateId": template_id, "version": version, "status": status,
        "schema": schema, "fallback": fallback,
    })


@router.get("/{template_id}/versions")
async def template_versions(
    template_id: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    rows = (await session.execute(
        select(TemplateVersion).where(TemplateVersion.template_id == template_id).order_by(TemplateVersion.version.desc())
    )).scalars().all()
    existing = {tv.version for tv in rows}
    # 幂等补齐：为模板缺失的每个可用版本创建记录（v1/v2/v3/v4…）
    n = len(tpl.versions)
    for v in tpl.versions:
        if n == 1:
            expect = "published"
        elif v == tpl.versions[-2]:
            expect = "published"
        elif v == tpl.versions[-1]:
            expect = "draft"
        else:
            expect = "archived"
        if v in existing:
            # 修正占位状态（例如 canvas 预建的 v3 被标 draft，应为 published）
            tv = next((x for x in rows if x.version == v), None)
            if tv is not None and tv.status != expect and tv.updated == "—":
                tv.status = expect
            continue
        session.add(TemplateVersion(
            id=f"{template_id}:{v}", template_id=template_id, version=v,
            status=expect,
            updated="—", updated_by="system", instances=0, nodes=len(tpl.nodes),
        ))
    if len(existing) < len(tpl.versions):
        await session.commit()
        rows = (await session.execute(
            select(TemplateVersion).where(TemplateVersion.template_id == template_id).order_by(TemplateVersion.version.desc())
        )).scalars().all()
    # 按版本号数字降序（v14 > v9 > v2），避免字符串排序导致 v10+ 顺序错乱
    rows = sorted(rows, key=lambda tv: int(tv.version[1:]) if tv.version[1:].isdigit() else 0, reverse=True)
    return ok({
        "template": {"id": tpl.id, "name": tpl.name, "type": tpl.type},
        "items": [
            {
                "version": tv.version, "status": tv.status, "updated": tv.updated,
                "updatedBy": tv.updated_by, "instances": tv.instances, "nodes": tv.nodes,
            }
            for tv in rows
        ],
    })


@router.get("/{template_id}/versions/{version}/canvas")
async def get_canvas(
    template_id: str,
    version: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    vid = f"{template_id}:{version}"
    canvas = await session.get(TemplateCanvas, vid)
    if canvas is None:
        # 确保版本记录存在（canvas 外键引用 template_versions）
        if await session.get(TemplateVersion, vid) is None:
            session.add(TemplateVersion(
                id=vid, template_id=template_id, version=version, status="draft",
                updated="—", updated_by="system", instances=0, nodes=0,
            ))
            await session.flush()
        # 默认画布：需求流程 v3 / 问题流程 v1（空画布时自动生成）
        if template_id == CANVAS_TEMPLATE_ID and version == "v3":
            canvas = _default_req_v3_canvas(vid)
            session.add(canvas)
            await session.commit()
        elif template_id == "tpl-issue" and version == "v1":
            canvas = _default_issue_v1_canvas(vid)
            session.add(canvas)
            await session.commit()
        else:
            return ok({"nodes": [], "edges": [], "fallbacks": []})
    return ok({"nodes": canvas.nodes, "edges": canvas.edges, "fallbacks": canvas.fallbacks})


@router.put("/{template_id}/versions/{version}/canvas")
async def save_canvas(
    template_id: str,
    version: str,
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    auth = build_authorizer(user)
    auth.require("workflow_template:update")
    tv = await session.get(TemplateVersion, f"{template_id}:{version}")
    if tv is None:
        raise BizError(BizCode.NOT_FOUND, "版本不存在")
    if tv.status == "published":
        raise BizError(BizCode.FORBIDDEN, "已发布版本不可修改，请新建草稿版本")
    canvas = await session.get(TemplateCanvas, tv.id)
    if canvas is None:
        canvas = TemplateCanvas(version_id=tv.id, nodes=[], edges=[], fallbacks=[])
        session.add(canvas)
    canvas.nodes = payload.get("nodes", [])
    canvas.edges = payload.get("edges", [])
    canvas.fallbacks = payload.get("fallbacks", [])
    tv.nodes = len(canvas.nodes)
    await AuditService(session).record(
        actor=user.name, action="workflow_template:edit",
        target=f"{template_id} {version} 草稿 · {len(canvas.nodes)} 节点", result="success",
    )
    await session.commit()
    return ok(message="画布草稿已保存（快照）")


def _sync_tpl_nodes(tpl: GlobalTemplate, nodes: list) -> None:
    """保存画布时同步模板节点列表（id/label/type），供流程引擎定位节点类型（决策/并行/定时）。"""
    tpl.nodes = [
        {"id": n.get("id", ""), "label": n.get("label", ""), "type": n.get("type", "task")}
        for n in nodes
    ]


def _validate_canvas(payload: dict) -> list[dict]:
    """发布校验核心：开始/结束数量、入出边、悬空、回退自指、Expert Deployment 绑定。"""
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    fallbacks = payload.get("fallbacks", [])
    errors: list[dict] = []
    starts = [n for n in nodes if n.get("type") == "start"]
    ends = [n for n in nodes if n.get("type") == "end"]
    if len(starts) != 1:
        errors.append({"node_id": "—", "message": f"开始节点必须为 1 个，当前 {len(starts)} 个"})
    if len(ends) != 1:
        errors.append({"node_id": "—", "message": f"结束节点必须为 1 个，当前 {len(ends)} 个"})
    ids = {n.get("id") for n in nodes}
    from_ids = {e[0] for e in edges}
    to_ids = {e[1] for e in edges}
    for n in nodes:
        nid = n.get("id")
        if n.get("type") != "start" and nid not in to_ids:
            errors.append({"node_id": nid, "message": "悬空节点：无入边（非开始节点必须有前置）"})
        if n.get("type") != "end" and nid not in from_ids:
            errors.append({"node_id": nid, "message": "悬空节点：无出边（非结束节点必须有后继）"})
    for f, t in fallbacks:
        if f not in ids or t not in ids:
            errors.append({"node_id": f, "message": "回退边引用不存在的节点"})
        if f == t:
            errors.append({"node_id": f, "message": "回退不能指向自身"})
    # Expert 自动节点必须绑定已发布的 Expert Deployment。
    for n in nodes:
        nid = n.get("id")
        cfg = n.get("cfg") or {}
        handler = cfg.get("handler", "")
        expert_cfg = cfg.get("expert") or {}
        if handler == "Expert 自动" and not expert_cfg.get("expertDeploymentId"):
            errors.append({"node_id": nid, "message": "「Expert 自动」节点必须绑定 Expert Deployment（属性面板配置）"})
        # 子任务拆分约束：仅任务节点、单出边；AI 自动拆分必须绑定 Expert Deployment
        split_mode = (cfg.get("split") or {}).get("mode", "off")
        if split_mode in ("manual", "ai_assist", "ai_auto"):
            label = n.get("label", nid)
            if n.get("type") != "task":
                errors.append({"node_id": nid, "message": f"「{label}」：仅任务类型节点支持子任务拆分"})
            elif len([e for e in edges if e[0] == nid]) != 1:
                errors.append({"node_id": nid, "message": f"「{label}」：拆分要求该节点只有一条后继分支（多分支请用并行分叉）"})
            if split_mode == "ai_auto" and not expert_cfg.get("expertDeploymentId"):
                errors.append({"node_id": nid, "message": f"「{label}」：AI 自动拆分必须绑定 Expert Deployment"})
    return errors


@router.post("/{template_id}/versions/{version}/canvas/validate")
async def validate_canvas(template_id: str, version: str, payload: dict):
    """发布校验（docs/04 §7）：开始/结束数量、入出边、悬空、回退自指。"""
    errors = _validate_canvas(payload)
    return ok({"ok": len(errors) == 0, "errors": errors})


@router.post("/{template_id}/versions/save-draft")
async def save_draft(
    template_id: str,
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """保存草稿（不发布）：复用最新草稿版本，没有草稿则自动创建新版本草稿。

    与 save-and-publish 的区别：只保存画布，不触发发布与校验；返回最新草稿版本号。
    """
    auth = build_authorizer(user)
    auth.require("workflow_template:update")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")

    latest_ver = tpl.versions[-1] if tpl.versions else None
    target = await session.get(TemplateVersion, f"{template_id}:{latest_ver}") if latest_ver else None
    created = False
    if target is None or target.status != "draft":
        import re
        table_vers = (await session.execute(
            select(TemplateVersion.version).where(TemplateVersion.template_id == template_id)
        )).scalars().all()
        all_vers = set(table_vers) | set(tpl.versions)
        nums = [int(m.group(1)) for v in all_vers if (m := re.match(r"v(\d+)$", v))]
        next_v = f"v{(max(nums) + 1) if nums else 1}"
        target = TemplateVersion(
            id=f"{template_id}:{next_v}", template_id=template_id, version=next_v,
            status="draft", updated="刚刚", updated_by=user.name, instances=0, nodes=0,
        )
        if next_v not in tpl.versions:
            tpl.versions = [*tpl.versions, next_v]
        session.add(target)
        created = True

    canvas = await session.get(TemplateCanvas, target.id)
    if canvas is None:
        canvas = TemplateCanvas(version_id=target.id, nodes=[], edges=[], fallbacks=[])
        session.add(canvas)
    canvas.nodes = payload.get("nodes", [])
    canvas.edges = payload.get("edges", [])
    canvas.fallbacks = payload.get("fallbacks", [])
    target.nodes = len(canvas.nodes)
    target.updated = "刚刚"
    target.updated_by = user.name
    await AuditService(session).record(
        actor=user.name, action="workflow_template:edit",
        target=f"{template_id} {target.version} 草稿 · {len(canvas.nodes)} 节点", result="success",
    )
    await session.commit()
    return ok(
        {"version": target.version, "status": "draft", "created": created},
        f"画布已保存为 {target.version} 草稿（未发布）",
    )


@router.post("/{template_id}/versions/save-and-publish")
async def save_draft_and_publish(
    template_id: str,
    payload: dict,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """发布：优先发布最新草稿版本（同版本号转 published），无草稿才自动创建新版本。

    - 保存草稿后直接发布 → 发布的就是该草稿版本号（不会跳号）；
    - 无草稿（或最新版本已发布）→ 自动创建下一个新版本号；
    - 已发布版本只读，不提供发布（前端禁用入口）；不会对既有 published 版本重复发布。
    - 校验失败时草稿保留为 draft（可继续编辑），返回 422 + 首个阻断问题。
    """
    auth = build_authorizer(user)
    auth.require("workflow_template:update")
    auth.require("workflow_template:publish")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")

    # 复用最新草稿版本（若有）：与 save-draft 一致取 tpl.versions 末位；无草稿才创建新版本
    latest_ver = tpl.versions[-1] if tpl.versions else None
    target = await session.get(TemplateVersion, f"{template_id}:{latest_ver}") if latest_ver else None
    if target is None or target.status != "draft":
        import re
        table_vers = (await session.execute(
            select(TemplateVersion.version).where(TemplateVersion.template_id == template_id)
        )).scalars().all()
        all_vers = set(table_vers) | set(tpl.versions)
        nums = [int(m.group(1)) for v in all_vers if (m := re.match(r"v(\d+)$", v))]
        next_v = f"v{(max(nums) + 1) if nums else 1}"
        target = TemplateVersion(
            id=f"{template_id}:{next_v}", template_id=template_id, version=next_v,
            status="draft", updated="刚刚", updated_by=user.name, instances=0, nodes=0,
        )
        if next_v not in tpl.versions:
            tpl.versions = [*tpl.versions, next_v]
        session.add(target)

    # 保存画布
    canvas = await session.get(TemplateCanvas, target.id)
    if canvas is None:
        canvas = TemplateCanvas(version_id=target.id, nodes=[], edges=[], fallbacks=[])
        session.add(canvas)
    canvas.nodes = payload.get("nodes", [])
    canvas.edges = payload.get("edges", [])
    canvas.fallbacks = payload.get("fallbacks", [])
    target.nodes = len(canvas.nodes)
    _sync_tpl_nodes(tpl, canvas.nodes)

    # 静态校验：未通过 → 草稿保留为 draft（可继续编辑），返回 422
    errors = _validate_canvas(payload)
    if errors:
        await AuditService(session).record(
            actor=user.name, action="workflow_template:edit",
            target=f"{template_id} {target.version} 草稿 · 校验未通过 {len(errors)} 项", result="failed",
        )
        await session.commit()
        raise BizError(
            BizCode.FLOW_VALIDATE,
            f"发布校验未通过：{len(errors)} 个阻断问题（{errors[0]['message']}）；"
            f"画布已保留为 {target.version} 草稿，修复后可再次发布",
        )

    # 自动发布
    target.status = "published"
    target.updated = "刚刚"
    target.updated_by = user.name
    await AuditService(session).record(
        actor=user.name, action="workflow_template:publish",
        target=f"{tpl.name} {target.version}", result="success",
    )
    await session.commit()
    return ok(
        {"version": target.version, "status": "published"},
        f"「{tpl.name}」{target.version} 已发布：静态校验通过",
    )


@router.post("/{template_id}/versions/{version}/publish")
async def publish_version(
    template_id: str,
    version: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
):
    """发布模板版本（docs/02 §4.4）：画布校验通过后 draft → published（失败阻断并返回校验报告）。"""
    auth = build_authorizer(user)
    auth.require("workflow_template:publish")
    tpl = await session.get(GlobalTemplate, template_id)
    if tpl is None:
        raise BizError(BizCode.NOT_FOUND, "模板不存在")
    tv = await session.get(TemplateVersion, f"{template_id}:{version}")
    if tv is None:
        raise BizError(BizCode.NOT_FOUND, "版本不存在")
    if tv.status == "published":
        raise BizError(BizCode.DUPLICATE_OPERATION, "该版本已发布")
    canvas = await session.get(TemplateCanvas, tv.id)
    if canvas is None or not canvas.nodes:
        raise BizError(BizCode.FLOW_VALIDATE, "画布为空：请先保存画布草稿再发布")
    errors = _validate_canvas({
        "nodes": canvas.nodes, "edges": canvas.edges, "fallbacks": canvas.fallbacks,
    })
    if errors:
        raise BizError(
            BizCode.FLOW_VALIDATE,
            f"发布校验未通过：{len(errors)} 个阻断问题（{errors[0]['message']}）",
            http_status=422,
        )
    tv.status = "published"
    tv.updated = "刚刚"
    tv.updated_by = user.name
    await AuditService(session).record(
        actor=user.name, action="workflow_template:publish",
        target=f"{tpl.name} {version}", result="success",
    )
    await session.commit()
    return ok({"version": version, "status": "published"}, f"「{tpl.name}」{version} 已发布：静态校验通过")


def _default_req_v3_canvas(vid: str) -> TemplateCanvas:
    """需求流程 v3 默认画布（迁移自前端 mock：蛇形布局 + 完整节点配置）。"""
    def f(key: str, label: str, ftype: str, required: bool, extra: dict | None = None) -> dict:
        return {"key": key, "label": label, "type": ftype, "required": required, **(extra or {})}

    per_node = {
        "n1": {"typeLine": "START · 开始", "purpose": "提报方向平台提交需求，必填标题与描述，可上传需求文档与原型。",
               "handler": "人工 + Agent 可协助", "fallback": "—（开始节点不可回退）", "sla": "48 小时", "output": "需求文档 · 原型（可选）",
               "schema": [f("title", "标题", "input", True, {"placeholder": "一句话描述需求", "hint": "全局唯一，重复提交将被去重拦截"}),
                          f("description", "描述", "textarea", True, {"placeholder": "需求背景与内容详述"}),
                          f("background", "背景", "textarea", False, {"placeholder": "为什么要做？当前痛点"}),
                          f("goal", "目标", "textarea", False, {"placeholder": "期望达到的业务目标"}),
                          f("scope", "范围", "textarea", False, {"placeholder": "包含 / 不包含的范围"}),
                          f("reqDoc", "需求文档", "upload", False, {"hint": "支持 PDF / Markdown / Office，病毒扫描后入库"}),
                          f("prototype", "原型", "file", False, {"hint": "可多选：原型稿 / 线框图"})]},
        "n2": {"typeLine": "TASK · 任务", "purpose": "分析需求背景、目标与验收标准，输出分析结论与拆分建议。",
               "handler": "人工 + Agent 可协助", "fallback": "—", "sla": "24 小时", "output": "分析报告",
               "schema": [f("conclusion", "分析结论", "textarea", True, {"placeholder": "可行性、风险与拆分建议"}),
                          f("priority", "优先级建议", "select", True, {"options": [{"label": "P0 · 紧急", "value": "P0"}, {"label": "P1 · 高", "value": "P1"}, {"label": "P2 · 中", "value": "P2"}, {"label": "P3 · 低", "value": "P3"}]}),
                          f("estimate", "预计工作量", "number", False, {"placeholder": "人日"}),
                          f("report", "分析报告", "upload", True, {"hint": "必填产出物"})]},
        "n3": {"typeLine": "TASK · 人工", "purpose": "组织产品评审并记录结论。", "handler": "人工", "fallback": "需求提交", "sla": "24 小时", "output": "评审记录",
               "schema": [f("verdict", "评审结论", "select", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "有条件通过", "value": "pass_with_cond"}, {"label": "不通过", "value": "reject"}]}),
                          f("opinion", "评审意见", "textarea", True, {"placeholder": "有条件通过时须写明条件"}),
                          f("record", "评审记录", "upload", False, {"hint": "会议纪要 / 签到 / 录音（可选）"})]},
        "n2": {"typeLine": "TASK · 分析", "purpose": "澄清需求边界与验收口径，形成结构化分析结论。",
               "handler": "人工", "fallback": "需求提交", "sla": "48 小时", "output": "需求分析结论",
               "deliverable": {"instruction": "输出需求分析结论：背景、范围、非目标、关键风险。",
                               "acceptance": [],
                               "aiGuidance": "结合工作项背景补充遗漏的分析维度；结论需可直接用于拆分。"},
               "split": {"mode": "ai_assist"},
               "schema": [f("conclusion", "分析结论", "textarea", True, {"placeholder": "背景 / 范围 / 非目标 / 风险"})]},
        "n4": {"typeLine": "TASK · 子工作项", "purpose": "将需求拆分为可交付子工作项，父项仅在必需子项完成后才可继续。",
               "handler": "人工", "fallback": "需求提交", "sla": "48 小时",
               "output": "子工作项",
               "deliverable": {"instruction": "将需求按模块拆解为可独立交付的子工作项，说明各子项负责人建议与完成顺序；本节点之后进入并行开发分支。",
                               "acceptance": [],
                               "aiGuidance": ""},
               "split": {"mode": "manual"},
               "schema": [f("subitems", "子项列表", "textarea", True, {"placeholder": "每行一个子工作项：名称 / 负责人 / 截止时间", "hint": "必需子项完成后父项才可继续（PRD §7.1）"}),
                          f("deps", "依赖关系", "textarea", False, {"placeholder": "子项间依赖，如 B 依赖 A"})]},
        "n5": {"typeLine": "TASK · 技能: backend", "purpose": "实现后端能力并提交交付物。", "handler": "人工", "fallback": "需求拆分", "sla": "72 小时", "output": "接口文档",
               "schema": [f("impl", "接口实现", "textarea", True, {"placeholder": "接口清单与实现说明"}),
                          f("unitTest", "单元测试结果", "select", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "失败", "value": "fail"}]}),
                          f("apiDoc", "接口文档", "upload", True, {"hint": "OpenAPI / Swagger 导出"})]},
        "n6": {"typeLine": "TASK · 技能: frontend", "purpose": "实现前端页面并提交交付物。", "handler": "人工", "fallback": "需求拆分", "sla": "72 小时", "output": "前端交付物",
               "schema": [f("impl", "页面实现", "textarea", True, {"placeholder": "页面清单与交互说明"}),
                          f("components", "组件清单", "textarea", False, {"placeholder": "新增 / 复用的组件列表"}),
                          f("deliverable", "前端交付物", "upload", True, {"hint": "构建产物或源码包"})]},
        "n7": {"typeLine": "TASK · 技能: qa", "purpose": "执行测试用例，失败必须填写缺陷说明与目标回退节点。",
               "handler": "人工 + Agent 可协助", "fallback": "需求拆分 / 后端开发 / 前端开发", "sla": "48 小时", "output": "测试报告",
               "schema": [f("verdict", "测试结论", "select", True, {"options": [{"label": "通过（全部用例通过）", "value": "pass"}, {"label": "部分通过（有缺陷需修复）", "value": "partial"}, {"label": "失败：需退回", "value": "fail"}]}),
                          f("note", "测试说明 / 缺陷说明", "textarea", True, {"placeholder": "失败必须填写缺陷说明与目标回退节点（PRD §7.1）"}),
                          f("bug", "关联缺陷", "select", False, {"options": [{"label": "无", "value": "none"}, {"label": "BUG-2026-0331 · 周末分派未通知", "value": "b331"}, {"label": "BUG-2026-0332 · 超时重试丢失", "value": "b332"}]}),
                          f("report", "测试报告", "upload", True, {"hint": "必填产出物：用例执行结果"})]},
        "n8": {"typeLine": "ACCEPTANCE · 验收", "purpose": "对照验收标准验收，不通过必须填写意见并选择允许回退节点。",
               "handler": "人工", "fallback": "需求拆分 / 后端开发 / 前端开发", "sla": "48 小时", "output": "验收记录",
               "schema": [f("verdict", "验收结论", "radio", True, {"options": [{"label": "通过", "value": "pass"}, {"label": "不通过", "value": "reject"}]}),
                          f("opinion", "验收意见", "textarea", True, {"placeholder": "不通过必须填写意见并选择允许回退节点"}),
                          f("record", "验收记录", "upload", False, {"hint": "验收截图 / 演示录屏（可选）"})]},
        "n9": {"typeLine": "TASK · 发布 / 交付", "purpose": "发布到目标环境并回填交付信息。", "handler": "人工", "fallback": "测试", "sla": "24 小时", "output": "发布记录",
               "schema": [f("version", "发布版本", "input", True, {"placeholder": "如 v3.2.1"}),
                          f("env", "目标环境", "select", True, {"options": [{"label": "生产环境", "value": "prod"}, {"label": "预发环境", "value": "staging"}, {"label": "测试环境", "value": "test"}]}),
                          f("planTime", "发布时间", "date", True),
                          f("record", "发布记录", "upload", True, {"hint": "发布单 / 回滚预案"})]},
        "n10": {"typeLine": "END · 结束", "purpose": "流程出口，工作项进入闭环状态。", "handler": "系统",
                "fallback": "—", "sla": "—", "output": "—", "schema": []},
    }
    cfg = {"typeLine": "", "purpose": "", "handler": "", "fallback": "", "sla": "", "schema": [], "output": ""}
    labels = ["需求提交", "需求分析", "产品评审", "需求拆分", "后端开发", "前端开发", "测试", "产品验收", "发布交付", "完成"]
    types = ["start", "task", "task", "task", "task", "task", "task", "acceptance", "task", "end"]
    x_pos = [24, 176, 328, 480, 632, 632, 480, 328, 176, 24]
    y_pos = [24, 24, 24, 24, 24, 118, 118, 118, 118, 118]
    nodes = [
        {"id": f"n{i + 1}", "label": labels[i], "type": types[i], "x": x_pos[i], "y": y_pos[i],
         "width": 118, "height": 56, "cfg": per_node.get(f"n{i + 1}", cfg)}
        for i in range(10)
    ]
    edges = [["n1", "n2"], ["n2", "n3"], ["n3", "n4"], ["n4", "n5"], ["n4", "n6"],
             ["n5", "n7"], ["n6", "n7"], ["n7", "n8"], ["n8", "n9"], ["n9", "n10"]]
    fallbacks = [["n7", "n4"], ["n8", "n4"], ["n8", "n5"], ["n8", "n6"], ["n9", "n7"]]
    return TemplateCanvas(version_id=vid, nodes=nodes, edges=edges, fallbacks=fallbacks)


def _default_issue_v1_canvas(vid: str) -> TemplateCanvas:
    """问题流程 v1 默认画布（对齐 GLOBAL_TEMPLATES tpl-issue 节点定义）。"""
    cfg = {"typeLine": "", "purpose": "", "handler": "", "fallback": "", "sla": "", "schema": [], "output": ""}
    labels = ["问题提报", "分诊", "二线分析", "开发排查", "修复", "验证", "售后确认", "关闭"]
    types = ["start", "task", "task", "task", "task", "task", "acceptance", "end"]
    x_pos = [24, 176, 328, 480, 480, 328, 176, 24]
    y_pos = [24, 24, 24, 24, 118, 118, 118, 118]
    nodes = [
        {"id": f"i{i + 1}", "label": labels[i], "type": types[i], "x": x_pos[i], "y": y_pos[i],
         "width": 118, "height": 56, "cfg": cfg}
        for i in range(8)
    ]
    edges = [["i1", "i2"], ["i2", "i3"], ["i3", "i4"], ["i4", "i5"],
             ["i5", "i6"], ["i6", "i7"], ["i7", "i8"]]
    fallbacks = [["i5", "i3"], ["i6", "i4"], ["i7", "i4"]]
    return TemplateCanvas(version_id=vid, nodes=nodes, edges=edges, fallbacks=fallbacks)
