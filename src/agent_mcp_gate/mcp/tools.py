"""Explicit tool registry and a dependency-free JSON Schema subset."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_mcp_gate.mcp.jsonrpc import INVALID_PARAMS, RpcError


def _field(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _invalid(path: str, reason: str) -> None:
    path = path or "$"
    raise RpcError(INVALID_PARAMS, f"{path}: {reason}", {"path": path, "reason": reason})


def validate_schema(value: Any, schema: dict, path: str = "") -> None:
    """Validate the supported subset; do not coerce values or mutate arguments."""
    expected = schema.get("type")
    checks = {
        "object": lambda: isinstance(value, dict),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "boolean": lambda: type(value) is bool,
        "array": lambda: isinstance(value, list),
    }
    if expected is not None:
        if expected not in checks:
            raise ValueError(f"Unsupported schema type: {expected}")
        if not checks[expected]():
            _invalid(path, f"expected {expected}, got {type(value).__name__}")
    if "enum" in schema:
        # Python otherwise equates True with 1, unlike the schema type system.
        if not any(type(value) is type(item) and value == item for item in schema["enum"]):
            _invalid(path, f"must be one of {schema['enum']!r}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                _invalid(_field(path, name), "required field is missing")
        properties = schema.get("properties", {})
        for name, item in value.items():
            if not isinstance(name, str):
                _invalid(path, "object keys must be strings")
            if name in properties:
                validate_schema(item, properties[name], _field(path, name))
            elif schema.get("additionalProperties") is False:
                _invalid(_field(path, name), "additional property is forbidden")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate_schema(item, schema["items"], f"{path}[{index}]")
    if type(value) is int:
        if "minimum" in schema and value < schema["minimum"]:
            _invalid(path, f"must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            _invalid(path, f"must be <= {schema['maximum']}")
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            _invalid(path, f"must match pattern {schema['pattern']!r}")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., dict]
    read_only: bool

    def call(self, arguments: dict) -> dict:
        validate_schema(arguments, self.input_schema)
        return self.handler(**arguments)

    def definition(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"readOnlyHint": self.read_only},
        }


def build_registry() -> dict[str, ToolSpec]:
    """Fresh registry; handlers can also be used directly without a sandbox."""
    from agent_mcp_gate.tools.apply_scoped_patch import apply_scoped_patch
    from agent_mcp_gate.tools.scan_asset_usage import scan_asset_usage

    specs = [
        ToolSpec(
            name="scan_asset_usage",
            description="Scan local JSON assets and deterministic transitive level dependencies.",
            input_schema={
                "type": "object",
                "properties": {
                    "root": {"type": "string"},
                    "level": {"type": "string"},
                    "include_soft": {"type": "boolean", "default": False},
                },
                "required": ["root", "level"],
                "additionalProperties": False,
            },
            handler=scan_asset_usage,
            read_only=True,
        ),
        ToolSpec(
            name="apply_scoped_patch",
            description="Atomically apply a matching unified diff within the configured workspace.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}, "patch": {"type": "string"}},
                "required": ["path", "patch"],
                "additionalProperties": False,
            },
            handler=apply_scoped_patch,
            read_only=False,
        ),
    ]
    return {spec.name: spec for spec in specs}
