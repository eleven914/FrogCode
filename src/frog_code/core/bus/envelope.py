from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FrogJsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class FrogEventPushEnvelope(BaseModel):
    kind: Literal["event"] = "event"
    event: dict[str, Any]  # Event.model_dump() 的序列化结果


class FrogJsonRpcSuccess(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: Any


class FrogJsonRpcErrorObject(BaseModel):
    code: int
    message: str
    data: Any = None


class FrogJsonRpcError(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | None = None
    error: FrogJsonRpcErrorObject


PARSE_ERROR = -32700      # 解析错误
INVALID_REQUEST = -32600  # 请求格式错误
METHOD_NOT_FOUND = -32601 # 方法不存在
INVALID_PARAMS = -32602   # 参数错误
INTERNAL_ERROR = -32603   # 服务器内部错误


class FrogHandlerError(Exception):
    """命令 handler 抛出此异常，FrogSocketServer 将其转换为结构化 JSON-RPC 错误响应。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


# 构造一个 JSON-RPC 错误响应对象
def frog_make_error(id: str | None, code: int, message: str, data: Any = None) -> FrogJsonRpcError:
    return FrogJsonRpcError(id=id, error=FrogJsonRpcErrorObject(code=code, message=message, data=data))
