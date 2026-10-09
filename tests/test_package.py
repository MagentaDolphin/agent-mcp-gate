import agent_mcp_gate


def test_package_import_and_version():
    assert isinstance(agent_mcp_gate.__version__, str)
    assert agent_mcp_gate.__version__
