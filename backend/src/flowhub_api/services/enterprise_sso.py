"""企业微信、钉钉授权码免登：校验 state 后以同步的外部 userid 映射本地用户。"""
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from flowhub_api.core.config import get_settings
from flowhub_api.services.runtime_config import settings as runtime_settings
from flowhub_api.core.response import BizCode, BizError

_ALG = "HS256"


def _return_to(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") else "/"


def authorization_url(provider: str, return_to: str = "/") -> str:
    s = runtime_settings()
    if not s.public_base_url.startswith("https://"):
        raise BizError(BizCode.VALIDATION, "企业免登需要配置 HTTPS 的 FLOWHUB_PUBLIC_BASE_URL")
    if provider == "dingtalk":
        client_id = s.dingtalk_app_key
        base = "https://login.dingtalk.com/oauth2/auth"
        params = {"redirect_uri": f"{s.public_base_url.rstrip('/')}/login", "response_type": "code", "client_id": client_id, "scope": "openid", "state": _state(provider, return_to)}
    elif provider == "wecom":
        client_id = s.wecom_corp_id
        base = "https://open.weixin.qq.com/connect/oauth2/authorize"
        params = {"appid": client_id, "redirect_uri": f"{s.public_base_url.rstrip('/')}/login", "response_type": "code", "scope": "snsapi_base", "state": _state(provider, return_to)}
    else:
        raise BizError(BizCode.VALIDATION, "不支持的企业身份源")
    if not client_id:
        raise BizError(BizCode.VALIDATION, f"{provider} 免登尚未配置应用凭证")
    suffix = "#wechat_redirect" if provider == "wecom" else ""
    return f"{base}?{urlencode(params)}{suffix}"


def _state(provider: str, return_to: str) -> str:
    return jwt.encode({"purpose": "enterprise_sso", "provider": provider, "return_to": _return_to(return_to), "exp": datetime.now(UTC) + timedelta(minutes=10)}, get_settings().secret_key, algorithm=_ALG)


def validate_state(provider: str, state: str) -> str:
    try:
        payload = jwt.decode(state, get_settings().secret_key, algorithms=[_ALG])
    except JWTError as exc:
        raise BizError(BizCode.UNAUTH, "企业免登请求无效或已过期", http_status=401) from exc
    if payload.get("purpose") != "enterprise_sso" or payload.get("provider") != provider:
        raise BizError(BizCode.UNAUTH, "企业免登请求无效", http_status=401)
    return _return_to(str(payload.get("return_to", "/")))


async def external_user_id(provider: str, code: str) -> str:
    s = runtime_settings()
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            if provider == "wecom":
                token_res = await client.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken", params={"corpid": s.wecom_corp_id, "corpsecret": s.wecom_app_secret})
                token = token_res.json().get("access_token")
                response = await client.get("https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo", params={"access_token": token, "code": code}) if token else None
                data = response.json() if response else {}
                user_id = data.get("UserId")
            else:
                token_res = await client.get("https://oapi.dingtalk.com/gettoken", params={"appkey": s.dingtalk_app_key, "appsecret": s.dingtalk_app_secret})
                token = token_res.json().get("access_token")
                response = await client.get("https://oapi.dingtalk.com/user/getuserinfo", params={"access_token": token, "code": code}) if token else None
                data = response.json() if response else {}
                user_id = data.get("userid")
    except (httpx.HTTPError, ValueError) as exc:
        raise BizError(BizCode.UNAUTH, "企业身份服务暂不可用", http_status=401) from exc
    if not user_id:
        raise BizError(BizCode.UNAUTH, "无法从企业授权码识别用户", http_status=401)
    return str(user_id)
