"""Executable, line-oriented MCP stdio server (no SDK, network, or LLM)."""

import sys
from typing import TextIO

from agent_mcp_gate import __version__
from agent_mcp_gate.mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    RpcError,
    error_response,
    parse_message,
    response,
    serialize_message,
)
from agent_mcp_gate.mcp.tools import ToolSpec, build_registry, validate_schema

PROTOCOL_VERSION = "2025-06-18"
_EMPTY_PARAMS = {"type": "object", "additionalProperties": False}
_INITIALIZE_PARAMS = {
    "type": "object",
    "properties": {
        "protocolVersion": {"type": "string"},
        "capabilities": {"type": "object"},
        "clientInfo": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["name", "version"],
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}
_CALL_PARAMS = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "arguments": {"type": "object"},
        "_meta": {"type": "object"},
    },
    "required": ["name"],
    "additionalProperties": False,
}


class Server:
    def __init__(self, registry: dict[str, ToolSpec] | None = None):
        self.registry = build_registry() if registry is None else registry
        self.shutdown_requested = False
        self.exited = False

    def dispatch(self, method: str, params: dict) -> dict | None:
        if method == "exit":
            validate_schema(params, _EMPTY_PARAMS)
            self.exited = True
            return None
        known = {"initialize", "notifications/initialized", "tools/list", "tools/call", "shutdown"}
        if method not in known:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        if self.shutdown_requested:
            raise RpcError(INVALID_REQUEST, "Server has shut down")
        if method == "initialize":
            validate_schema(params, _INITIALIZE_PARAMS)
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "agent-mcp-gate", "version": __version__},
                "capabilities": {"tools": {"listChanged": False}},
            }
        if method == "tools/call":
            validate_schema(params, _CALL_PARAMS)
            name = params["name"]
            if name not in self.registry:
                raise RpcError(
                    INVALID_PARAMS,
                    f"Unknown tool: {name}",
                    {"path": "name", "available": sorted(self.registry)},
                )
            output = self.registry[name].call(params.get("arguments", {}))
            return {
                "content": [{"type": "text", "text": serialize_message(output)}],
                "structuredContent": output,
                "isError": output.get("applied") is False,
            }
        validate_schema(params, _EMPTY_PARAMS)
        if method == "tools/list":
            return {"tools": [spec.definition() for spec in self.registry.values()]}
        if method == "shutdown":
            self.shutdown_requested = True
        return None

    def handle_line(self, line: str) -> dict | None:
        request_id = None
        is_notification = False
        try:
            message = parse_message(line)
            if "method" not in message:
                raise RpcError(INVALID_REQUEST, "Server accepts requests, not responses")
            request_id = message.get("id")
            is_notification = "id" not in message
            output = self.dispatch(message["method"], message.get("params", {}))
            if not is_notification:
                return response(request_id, output)
        except RpcError as exc:
            if not is_notification:
                return error_response(request_id if request_id is not None else exc.request_id, exc)
        except Exception:
            if not is_notification:
                return error_response(request_id, RpcError(INTERNAL_ERROR, "Internal error"))
        return None

    def run(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            result = self.handle_line(line)
            if result is not None:
                stdout.write(serialize_message(result) + "\n")
                stdout.flush()
            if self.exited:
                break


def main() -> None:
    Server().run(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
