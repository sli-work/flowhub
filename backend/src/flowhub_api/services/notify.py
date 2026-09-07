"""通知渠道投递：站内 / 钉钉、企微应用消息 / 群机器人 / 邮件 SMTP。

- 站内：落库即成功（通知记录本身已存在）。
- 钉钉、企微应用消息：按本地用户绑定的外部 userid 点对点投递。
- 群机器人：仅作广播兜底，不视为对指定处理人的送达。
- 邮件：SMTP（smtplib，异步线程池执行避免阻塞事件循环）。

每个渠道独立 try/catch：失败只标记该渠道，不影响其他渠道与流程（通知为异步副作用）。
渠道未配置（webhook/smtp 为空）→ 不投递、不计入 channels（由 channels/health 展示配置状态）。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import smtplib
import time
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import httpx

from flowhub_api.services.runtime_config import settings as runtime_settings
from flowhub_api.models import User


def _ding_sign(secret: str, timestamp: str) -> str:
    """钉钉机器人加签：HMAC-SHA256(timestamp + \n + secret)。"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(hmac_code))


async def _send_dingtalk(webhook: str, secret: str, title: str, text: str) -> bool:
    """钉钉群机器人 markdown 消息。"""
    params = {}
    if secret:
        ts = str(round(time.time() * 1000))
        params = {"timestamp": ts, "sign": _ding_sign(secret, ts)}
    payload = {"msgtype": "markdown", "markdown": {"title": title[:64], "text": text[:4000]}}
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        r = await client.post(webhook, params=params, json=payload)
        data = r.json()
        return r.status_code == 200 and data.get("errcode") in (0, None)


async def _send_wecom(webhook: str, title: str, text: str) -> bool:
    """企业微信群机器人 markdown 消息。"""
    payload = {"msgtype": "markdown", "markdown": {"content": f"**{title}**\n\n{text[:4000]}"}}
    async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
        r = await client.post(webhook, json=payload)
        data = r.json()
        return r.status_code == 200 and data.get("errcode") in (0, None)


async def _dingtalk_access_token(app_key: str, app_secret: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={"appkey": app_key, "appsecret": app_secret},
            )
        return response.json().get("access_token") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def _send_dingtalk_app(app_key: str, app_secret: str, agent_id: str, user_id: str, title: str, text: str) -> bool:
    """钉钉内部应用工作通知；userid 来自组织同步，单用户精确投递。"""
    token = await _dingtalk_access_token(app_key, app_secret)
    if not token:
        return False
    payload = {
        "agent_id": int(agent_id), "userid_list": user_id,
        "msg": {"msgtype": "markdown", "markdown": {"title": title[:64], "text": text[:4000]}},
    }
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.post(
                "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
                params={"access_token": token}, json=payload,
            )
        data = response.json()
        return response.status_code == 200 and data.get("errcode") in (0, None)
    except (httpx.HTTPError, ValueError, TypeError):
        return False


async def _wecom_access_token(corp_id: str, app_secret: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": corp_id, "corpsecret": app_secret},
            )
        return response.json().get("access_token") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def _send_wecom_app(corp_id: str, app_secret: str, agent_id: str, user_id: str, title: str, text: str) -> bool:
    """企业微信自建应用消息；touser 为通讯录同步得到的 userid。"""
    token = await _wecom_access_token(corp_id, app_secret)
    if not token:
        return False
    payload = {
        "touser": user_id, "msgtype": "text", "agentid": int(agent_id),
        "text": {"content": f"{title}\n{text}"[:2048]}, "safe": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.post(
                "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                params={"access_token": token}, json=payload,
            )
        data = response.json()
        return response.status_code == 200 and data.get("errcode") == 0
    except (httpx.HTTPError, ValueError, TypeError):
        return False


def _site_link(settings) -> str:
    """外发通知附带的站点访问地址：public_base_url 配置优先，缺省内网部署地址。"""
    base = (getattr(settings, "public_base_url", "") or "").rstrip("/")
    return base or "http://192.168.21.195:8088"


def _email_html(title: str, body: str, site: str) -> str:
    """通知邮件 HTML 版（与纯文本互为 alternative）。

    邮件客户端兼容性约束：不用外部 CSS/图片，全部内联样式 + 表格布局，
    品牌色与前端一致（blue-600 #2563eb）。
    """
    lines = "".join(
        f'<tr><td style="padding:0 0 10px;font-size:14px;line-height:1.7;color:#334155;">{l}</td></tr>'
        for l in body.splitlines() if l.strip()
    )
    return (
        '<!DOCTYPE html><html lang="zh-CN"><body style="margin:0;padding:0;background:#f1f5f9;">'
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'  # 预览头部：客户端列表摘要
        f'{body[:88]}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 12px;">'
        '<tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;'
        'box-shadow:0 1px 3px rgba(15,23,42,.08);">'
        # 页头：品牌标识
        '<tr><td style="background:#2563eb;padding:22px 32px;">'
        '<span style="display:inline-block;background:rgba(255,255,255,.18);color:#ffffff;'
        'font-size:17px;font-weight:600;letter-spacing:.5px;padding:6px 14px;border-radius:8px;">FlowHub</span>'
        '</td></tr>'
        # 标题
        '<tr><td style="padding:28px 32px 8px;">'
        f'<h1 style="margin:0;font-size:19px;font-weight:600;color:#0f172a;line-height:1.4;">{title}</h1>'
        '<div style="margin-top:12px;height:1px;background:#e2e8f0;"></div>'
        '</td></tr>'
        # 正文
        f'<tr><td style="padding:12px 32px 4px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{lines}</table></td></tr>'
        # 按钮
        '<tr><td style="padding:16px 32px 28px;">'
        f'<a href="{site}" style="display:inline-block;background:#2563eb;color:#ffffff;text-decoration:none;'
        'font-size:14px;font-weight:600;padding:10px 28px;border-radius:8px;">前往 FlowHub 处理</a>'
        f'<p style="margin:12px 0 0;font-size:12px;color:#94a3b8;">按钮无法点击？复制链接访问：{site}</p>'
        '</td></tr>'
        # 页脚
        '<tr><td style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0;">'
        '<p style="margin:0;font-size:11.5px;color:#94a3b8;line-height:1.7;">'
        'FlowHub · 让每个流程节点可运行、可追溯、可审计<br>'
        '此邮件由系统自动发送，请勿直接回复。</p>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def _send_email_sync(host: str, port: int, user: str, password: str, from_addr: str, to: str, title: str, text: str, html: str) -> bool:
    """SMTP 发送（同步，供线程池调用）：HTML 为主、纯文本回退（multipart/alternative）。"""
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = formataddr(("FlowHub", from_addr))
    msg["To"] = to
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=8)
    else:
        server = smtplib.SMTP(host, port, timeout=8)
    try:
        server.login(user, password)
        server.send_message(msg)
        return True
    finally:
        server.quit()


async def _send_email(host: str, port: int, user: str, password: str, from_addr: str, to: str, title: str, text: str, html: str) -> bool:
    try:
        return await asyncio.to_thread(_send_email_sync, host, port, user, password, from_addr, to, title, text, html)
    except Exception:
        return False


async def deliver_channels(title: str, body: str, target: User | str | None = None) -> list[dict[str, Any]]:
    """按配置把通知投递到已启用渠道，返回 channels 结果列表（仅含已启用渠道）。

    target 可为本地 User（优先使用其钉钉/企微 userid 点对点投递）或邮件地址。
    外发渠道（钉钉/企微/邮件）正文统一附带站点链接；站内通知在应用内展示，不附。
    """
    settings = runtime_settings()
    site = _site_link(settings)
    results: list[dict[str, Any]] = [{"name": "站内", "ok": True}]
    out_body = f"{body}\n\n请访问 FlowHub：{site}"

    recipient = target if isinstance(target, User) else None
    # 任务流转传入的是处理人 User。此前只在 target 为邮箱字符串时发邮件，
    # 因而所有由任务流转、转办和追加信息触发的个人邮件都会被跳过。
    email = (recipient.email or "").strip() if recipient else (target.strip() if isinstance(target, str) and "@" in target else "")
    if recipient and settings.dingtalk_app_key and settings.dingtalk_app_secret and settings.dingtalk_agent_id and recipient.ding_talk:
        ok = await _send_dingtalk_app(
            settings.dingtalk_app_key, settings.dingtalk_app_secret, settings.dingtalk_agent_id,
            recipient.ding_talk, title, out_body,
        )
        results.append({"name": "钉钉", "ok": ok})

    if recipient and settings.wecom_corp_id and settings.wecom_app_secret and settings.wecom_agent_id and recipient.wecom:
        ok = await _send_wecom_app(
            settings.wecom_corp_id, settings.wecom_app_secret, settings.wecom_agent_id,
            recipient.wecom, title, out_body,
        )
        results.append({"name": "企微", "ok": ok})

    if settings.dingtalk_webhook:
        ok = await _send_dingtalk(settings.dingtalk_webhook, settings.dingtalk_secret, title, out_body)
        results.append({"name": "钉钉群", "ok": ok})

    if settings.wecom_webhook:
        ok = await _send_wecom(settings.wecom_webhook, title, out_body)
        results.append({"name": "企微群", "ok": ok})

    if settings.smtp_host and settings.smtp_user and email:
        ok = await _send_email(
            settings.smtp_host, settings.smtp_port, settings.smtp_user,
            settings.smtp_password, settings.smtp_from or settings.smtp_user,
            email, title, out_body, _email_html(title, body, site),
        )
        results.append({"name": "邮件", "ok": ok})
    return results


def welcome_email_body(name: str) -> str:
    """用户账号激活后的欢迎邮件正文：系统简介 + 快速上手 + Skill/MCP 使用指南。"""
    return (
        f"{name}，你好！\n\n"
        "你的 FlowHub 账号已激活，首次登录请先修改初始密码。\n\n"
        "【FlowHub 是什么】\n"
        "FlowHub 是一个可审计的流程协同平台：需求、问题、任务、文档与 AI Expert 在同一条流程中流转，"
        "每一步状态变更都可追溯到人、时间与操作，高风险 AI 操作必须经人工确认。\n\n"
        "【快速上手】\n"
        "1. 「我的任务」：处理分配给你的节点任务，填写产出表单并提交，流程自动流转到下一节点；\n"
        "2. 「工作项」：按标签（如版本号）分类查看全部工作项，点击标题进入详情可看到任务树与处理历史；\n"
        "3. 「AiChat」：直接用自然语言查询项目/任务/流程状态；选择 Expert 后可执行更专业的分析产出；\n"
        "4. 「文档中心」：上传与预览需求文档、接口文档，支持 Axure 导出包在线预览。\n\n"
        "【外部 Skill 与 MCP 使用指南】\n"
        "- MCP 中心：注册 MCP Server（如公司内部工具网关），Expert 运行时可调用其工具；"
        "外部 Agent 也可通过「MCP / Skill 下载」页获取接入凭证（Access Key）接入 FlowHub；\n"
        "- Expert Skill：在「Expert Skill」页上传技能包（提示词 + 工具编排），创建 Expert 时勾选即注入其运行上下文；\n"
        "- 「MCP 下载」页提供的 Skill 包可直接导入 Expert 编辑器，作为构建专属 Expert 的起点；\n"
        "- 安全约定：Skill 与 MCP 工具遵循最小权限，受治理写入操作会在审批节点中断等待人工确认。\n\n"
        "如在「代码仓库」页绑定项目仓库，Expert 处理任务时会自动带上仓库结构上下文，分析更精准。\n\n"
        "—— FlowHub 平台"
    )


def channel_health() -> dict[str, dict[str, Any]]:
    """渠道可用性（docs/02 §通知）：各渠道配置状态。"""
    s = runtime_settings()
    return {
        "站内": {"enabled": True, "ok": True, "desc": "通知中心站内消息（落库即达）"},
        "钉钉": {"enabled": bool(s.dingtalk_app_key and s.dingtalk_app_secret and s.dingtalk_agent_id), "ok": bool(s.dingtalk_app_key and s.dingtalk_app_secret and s.dingtalk_agent_id), "desc": "内部应用点对点通知" + ("" if s.dingtalk_app_key and s.dingtalk_app_secret and s.dingtalk_agent_id else "（需配置 DINGTALK_APP_KEY/SECRET/AGENT_ID）")},
        "企微": {"enabled": bool(s.wecom_corp_id and s.wecom_app_secret and s.wecom_agent_id), "ok": bool(s.wecom_corp_id and s.wecom_app_secret and s.wecom_agent_id), "desc": "自建应用点对点通知" + ("" if s.wecom_corp_id and s.wecom_app_secret and s.wecom_agent_id else "（需配置 WECOM_CORP_ID/APP_SECRET/AGENT_ID）")},
        "钉钉群": {"enabled": bool(s.dingtalk_webhook), "ok": bool(s.dingtalk_webhook), "desc": "群机器人广播兜底"},
        "企微群": {"enabled": bool(s.wecom_webhook), "ok": bool(s.wecom_webhook), "desc": "群机器人广播兜底"},
        "邮件": {"enabled": bool(s.smtp_host and s.smtp_user), "ok": bool(s.smtp_host and s.smtp_user), "desc": "SMTP" + ("" if s.smtp_host and s.smtp_user else "（未配置 SMTP_*）")},
    }
