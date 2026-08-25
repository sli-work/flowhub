"""统一响应 { code, message, data } 与业务错误码（对齐 docs/02-REST-API 错误码约定）。"""
from typing import Any

from fastapi import HTTPException, status


class BizCode:
    OK = 0
    VALIDATION = 40001
    UNAUTH = 40101
    FORBIDDEN = 40301
    PERM_DENIED = 40302
    NOT_FOUND = 40401
    DUPLICATE_TITLE = 40901
    DUPLICATE_OPERATION = 40902
    FLOW_VALIDATE = 42201
    LOCKED = 42301
    INTERNAL = 50000


# 业务错误码 → HTTP 状态码映射（docs/02-REST-API 错误码约定表）。
# BizError 未显式传 http_status 时按此映射推导，保证 40401→404、40301/40302→403、40901/40902→409 等契约一致。
CODE_TO_HTTP: dict[int, int] = {
    BizCode.OK: status.HTTP_200_OK,
    BizCode.VALIDATION: status.HTTP_400_BAD_REQUEST,
    BizCode.UNAUTH: status.HTTP_401_UNAUTHORIZED,
    BizCode.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    BizCode.PERM_DENIED: status.HTTP_403_FORBIDDEN,
    BizCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    BizCode.DUPLICATE_TITLE: status.HTTP_409_CONFLICT,
    BizCode.DUPLICATE_OPERATION: status.HTTP_409_CONFLICT,
    BizCode.FLOW_VALIDATE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BizCode.LOCKED: status.HTTP_423_LOCKED,
    BizCode.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class BizError(HTTPException):
    """业务异常：携带业务 code + http 状态码 + 提示消息。

    http_status 缺省时按 CODE_TO_HTTP 由业务错误码推导，显式传入时以显式值为准。
    """

    def __init__(
        self,
        code: int,
        message: str,
        http_status: int | None = None,
    ):
        self.biz_code = code
        super().__init__(status_code=http_status or CODE_TO_HTTP.get(code, status.HTTP_400_BAD_REQUEST), detail=message)


def ok(data: Any = None, message: str = "ok") -> dict:
    return {"code": BizCode.OK, "message": message, "data": data}


def fail(code: int, message: str) -> dict:
    return {"code": code, "message": message, "data": None}
