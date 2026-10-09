"""JSON-RPC 2.0 envelopes, strict parsing, and protocol errors."""

import json
import math
from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
        self.request_id: str | int | float | None = None


def _valid_id(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, str)
        or type(value) is int
        or (type(value) is float and math.isfinite(value))
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def parse_message(line: str) -> dict[str, Any]:
    """Parse a single request, notification, success response, or error response.

    JSON-RPC batches are deliberately unsupported by the line transport.
    Invalid envelopes retain an identifiable, valid request ID on RpcError.
    """
    try:
        message = json.loads(line, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise RpcError(PARSE_ERROR, "Parse error") from exc
    try:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RpcError(INVALID_REQUEST, "Expected a JSON-RPC 2.0 object")
        if "id" in message and not _valid_id(message["id"]):
            raise RpcError(INVALID_REQUEST, "Invalid request ID")
        if "method" in message:
            if not isinstance(message["method"], str):
                raise RpcError(INVALID_REQUEST, "Method must be a string")
            if "result" in message or "error" in message:
                raise RpcError(INVALID_REQUEST, "Request cannot contain result or error")
            if "params" in message and not isinstance(message["params"], (dict, list)):
                raise RpcError(INVALID_REQUEST, "Params must be an object or array")
        else:
            if "id" not in message or ("result" in message) == ("error" in message):
                raise RpcError(INVALID_REQUEST, "Response needs an ID and result or error")
            if "params" in message:
                raise RpcError(INVALID_REQUEST, "Response cannot contain params")
            if "error" in message:
                error = message["error"]
                if (
                    not isinstance(error, dict)
                    or type(error.get("code")) is not int
                    or not isinstance(error.get("message"), str)
                ):
                    raise RpcError(INVALID_REQUEST, "Invalid error object")
    except RpcError as exc:
        if isinstance(message, dict) and _valid_id(message.get("id")):
            exc.request_id = message.get("id")
        raise
    return message


def request(method: str, request_id: str | int | float | None, params: Any = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: Any = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def response(request_id: str | int | float | None, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: str | int | float | None, error: RpcError) -> dict:
    details = {"code": error.code, "message": error.message}
    if error.data is not None:
        details["data"] = error.data
    return {"jsonrpc": "2.0", "id": request_id, "error": details}


def serialize_message(message: dict) -> str:
    """One compact JSON line, with no physical newline in the payload."""
    return json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
