"""短时、受限的文档内容链接。"""
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from flowhub_api.core.config import get_settings

_ALGORITHM = "HS256"
_PURPOSE = "document_content"
_EXPIRE_MINUTES = 5


def create_document_content_token(doc_id: str) -> str:
    """签发只可读取指定文档的短时令牌。"""
    expires = datetime.now(UTC) + timedelta(minutes=_EXPIRE_MINUTES)
    return jwt.encode(
        {"doc_id": doc_id, "purpose": _PURPOSE, "exp": expires},
        get_settings().secret_key,
        algorithm=_ALGORITHM,
    )


def document_content_link(doc_id: str) -> str:
    return f"/api/v1/documents/{doc_id}/content?token={create_document_content_token(doc_id)}"


def valid_document_content_token(token: str, doc_id: str) -> bool:
    """令牌必须未过期，且用途和目标文档都精确匹配。"""
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except JWTError:
        return False
    return payload.get("purpose") == _PURPOSE and payload.get("doc_id") == doc_id
