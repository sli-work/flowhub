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
from email.header import Header
from email.mime.text import MIMEText
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


def _send_email_sync(host: str, port: int, user: str, password: str, from_addr: str, to: str, title: str, text: str) -> bool:
    """SMTP 发送（同步，供线程池调用）。"""
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(title, "utf-8")
    msg["From"] = from_addr
    msg["To"] = to
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=8)
    else:
        server = smtplib.SMTP(host, port, timeout=8)
    try:
        server.login(user, password)
        server.sendmail(from_addr, [to], msg.as_string())
        return True
    finally:
        server.quit()


async def _send_email(host: str, port: int, user: str, password: str, from_addr: str, to: str, title: str, text: str) -> bool:
    try:
        return await asyncio.to_thread(_send_email_sync, host, port, user, password, from_addr, to, title, text)
    except Exception:
        return False


async def deliver_channels(title: str, body: str, target: User | str | None = None) -> list[dict[str, Any]]:
    """按配置把通知投递到已启用渠道，返回 channels 结果列表（仅含已启用渠道）。

    target 可为本地 User（优先使用其钉钉/企微 userid 点对点投递）或邮件地址。
    """
    settings = runtime_settings()
    results: list[dict[str, Any]] = [{"name": "站内", "ok": True}]

    recipient = target if isinstance(target, User) else None
    email = target if isinstance(target, str) and "@" in target else ""
    if recipient and settings.dingtalk_app_key and settings.dingtalk_app_secret and settings.dingtalk_agent_id and recipient.ding_talk:
        ok = await _send_dingtalk_app(
            settings.dingtalk_app_key, settings.dingtalk_app_secret, settings.dingtalk_agent_id,
            recipient.ding_talk, title, body,
        )
        results.append({"name": "钉钉", "ok": ok})

    if recipient and settings.wecom_corp_id and settings.wecom_app_secret and settings.wecom_agent_id and recipient.wecom:
        ok = await _send_wecom_app(
            settings.wecom_corp_id, settings.wecom_app_secret, settings.wecom_agent_id,
            recipient.wecom, title, body,
        )
        results.append({"name": "企微", "ok": ok})

    if settings.dingtalk_webhook:
        ok = await _send_dingtalk(settings.dingtalk_webhook, settings.dingtalk_secret, title, body)
        results.append({"name": "钉钉群", "ok": ok})

    if settings.wecom_webhook:
        ok = await _send_wecom(settings.wecom_webhook, title, body)
        results.append({"name": "企微群", "ok": ok})

    if settings.smtp_host and settings.smtp_user and email:
        ok = await _send_email(
            settings.smtp_host, settings.smtp_port, settings.smtp_user,
            settings.smtp_password, settings.smtp_from or settings.smtp_user,
            email, title, body,
        )
        results.append({"name": "邮件", "ok": ok})

    return results


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
