import json

import pytest

from agent_mcp_gate.mcp.jsonrpc import (
    INVALID_REQUEST,
    PARSE_ERROR,
    RpcError,
    error_response,
    notification,
    parse_message,
    request,
    response,
    serialize_message,
)


@pytest.mark.parametrize(
    "message",
    [
        request("tools/list", 0),
        request("initialize", "client", {}),
        request("tools/call", None, {"name": "x"}),
        notification("notifications/initialized"),
        response(1, {"text": "one\ntwo"}),
        error_response(2, RpcError(-32602, "bad", {"path": "levels[0].path"})),
    ],
)
def test_round_trip(message):
    encoded = serialize_message(message)
    assert "\n" not in encoded
    assert parse_message(encoded) == message


@pytest.mark.parametrize(
    "line, code",
    [
        ("{", PARSE_ERROR),
        ('{"jsonrpc":"2.0","id":NaN,"method":"x"}', PARSE_ERROR),
        ("null", INVALID_REQUEST),
        ("[]", INVALID_REQUEST),
        ('{"id":1,"method":"x"}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":true,"method":"x"}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","method":1}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","method":"x","params":null}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":1,"method":"x","result":1}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":1,"result":1,"error":{}}', INVALID_REQUEST),
        ('{"jsonrpc":"2.0","id":1,"error":{"code":true,"message":"x"}}', INVALID_REQUEST),
    ],
)
def test_invalid_messages(line, code):
    with pytest.raises(RpcError) as caught:
        parse_message(line)
    assert caught.value.code == code


def test_error_data_optional_and_request_id_preserved():
    assert "data" not in error_response(None, RpcError(-32700, "Parse error"))["error"]
    with pytest.raises(RpcError) as caught:
        parse_message(json.dumps({"jsonrpc": "2.0", "id": "client", "method": False}))
    assert caught.value.request_id == "client"
