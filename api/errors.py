# api/errors.py
"""接口错误码与 HTTP 状态码的唯一映射，见 API_CONTRACT.md 第 1 节."""

from enum import IntEnum


class ErrorCode(IntEnum):
    """合同定义的全部错误码."""

    INVALID_ARGUMENT = 40001
    REQUEST_ID_CONFLICT = 40009
    INVALID_STATE = 40010
    NOT_FOUND = 40401
    INTERNAL = 50001
    UNAVAILABLE = 50002
    RUNTIME_BLOCKED = 50004

    @property
    def http_status(self) -> int:
        """错误码对应的 HTTP 状态码."""
        return _HTTP_STATUS[self]


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_ARGUMENT: 422,
    ErrorCode.REQUEST_ID_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL: 500,
    ErrorCode.UNAVAILABLE: 503,
    ErrorCode.RUNTIME_BLOCKED: 503,
}


class ApiError(Exception):
    """可直接转换为统一失败响应的业务错误；message 面向前端，不含路径或凭据."""

    def __init__(self, _code: ErrorCode, _message: str) -> None:
        """保存错误码与中文说明."""
        super().__init__(_message)
        self.code: ErrorCode = _code
        self.message: str = _message


def error_code_of(value: int) -> ErrorCode:
    """把下层 ServiceError 的数值收窄为合同错误码，未知值视为内部错误."""
    try:
        return ErrorCode(value)
    except ValueError:
        return ErrorCode.INTERNAL
