import pytest

from agent_mcp_gate.mcp.jsonrpc import RpcError
from agent_mcp_gate.mcp.tools import build_registry, validate_schema

_SCHEMA = {
    "type": "object",
    "properties": {
        "levels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "pattern": r"\.json$"},
                    "depth": {"type": "integer", "minimum": 0, "maximum": 3},
                    "enabled": {"type": "boolean"},
                    "mode": {"type": "string", "enum": ["hard", "soft"]},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["levels"],
    "additionalProperties": False,
}


def test_valid_nested_schema_and_boolean_integer_distinction():
    validate_schema({"levels": [{"path": "a.json", "depth": 3, "enabled": False}]}, _SCHEMA)
    with pytest.raises(RpcError):
        validate_schema(True, {"enum": [1]})


@pytest.mark.parametrize(
    "value, path, reason",
    [
        ({}, "levels", "required"),
        ({"levels": "x"}, "levels", "expected array"),
        ({"levels": ["x"]}, "levels[0]", "expected object"),
        ({"levels": [{}]}, "levels[0].path", "required"),
        ({"levels": [{"path": 1}]}, "levels[0].path", "expected string"),
        ({"levels": [{"path": "a.txt"}]}, "levels[0].path", "pattern"),
        ({"levels": [{"path": "a.json", "depth": True}]}, "levels[0].depth", "integer"),
        ({"levels": [{"path": "a.json", "depth": 1.0}]}, "levels[0].depth", "integer"),
        ({"levels": [{"path": "a.json", "depth": -1}]}, "levels[0].depth", ">= 0"),
        ({"levels": [{"path": "a.json", "depth": 4}]}, "levels[0].depth", "<= 3"),
        ({"levels": [{"path": "a.json", "enabled": 1}]}, "levels[0].enabled", "boolean"),
        ({"levels": [{"path": "a.json", "mode": "other"}]}, "levels[0].mode", "one of"),
        ({"levels": [{"path": "a.json", "extra": 1}]}, "levels[0].extra", "forbidden"),
        ({"levels": [], "extra": 1}, "extra", "forbidden"),
    ],
)
def test_schema_failures_report_field_path_and_reason(value, path, reason):
    with pytest.raises(RpcError) as caught:
        validate_schema(value, _SCHEMA)
    assert caught.value.code == -32602
    assert caught.value.data["path"] == path
    assert reason in caught.value.data["reason"]
    assert path in caught.value.message


def test_tool_registry_contracts_and_validation_before_handler():
    registry = build_registry()
    assert set(registry) == {"scan_asset_usage", "apply_scoped_patch"}
    assert registry["scan_asset_usage"].read_only is True
    assert registry["apply_scoped_patch"].read_only is False
    for spec in registry.values():
        assert spec.definition()["inputSchema"] == spec.input_schema
        with pytest.raises(RpcError) as caught:
            spec.call({})
        assert caught.value.code == -32602
    with pytest.raises(RpcError) as caught:
        registry["scan_asset_usage"].call({"root": ".", "level": "x", "include_soft": 1})
    assert caught.value.data["path"] == "include_soft"
