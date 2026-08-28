"""Provider API Key 加密工具（Fernet 对称加密）+ 文档短时链接 token。

密钥由 `agent_key_encrypt_secret`（或回退 `secret_key`）经 sha256 派生：
32 字节摘要 → urlsafe_base64 → 44 字符合法 Fernet key。
加解密失败（InvalidToken，通常为加密密钥变更）映射为明确业务错误。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from flowhub_api.core.config import get_settings
from flowhub_api.core.response import BizCode, BizError


def _fernet_key() -> bytes:
    s = get_settings()
    secret = s.agent_key_encrypt_secret or s.secret_key or "flowhub-provider-key"
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def encrypt_secret(plain: str) -> str:
    """加密明文 API Key，返回 Fernet token（str）。"""
    if not plain:
        return ""
    return Fernet(_fernet_key()).encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    """解密 API Key；密钥变更导致解密失败时抛明确业务错误。"""
    if not token:
        return ""
    try:
        return Fernet(_fernet_key()).decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise BizError(
            BizCode.FORBIDDEN,
            "Provider API Key 解密失败（加密密钥可能已变更），请在 Provider 管理中重新录入",
            http_status=500,
        )
