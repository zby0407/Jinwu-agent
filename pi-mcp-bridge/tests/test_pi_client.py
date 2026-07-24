"""Unit tests for pi_mcp_bridge.pi_client."""


from pi_mcp_bridge.pi_client import PiConfig, config_from_env


def test_pi_config_default_argv():
    cfg = PiConfig()
    argv = cfg.build_argv()
    assert argv[:3] == ["pi", "--mode", "rpc"]
    assert "--name" in argv
    assert "jw-pi-bridge" in argv


def test_pi_config_full_argv():
    cfg = PiConfig(
        pi_bin="/usr/local/bin/pi",
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        session_dir="/tmp/pi-sessions",
        session_name="test-session",
        no_session=True,
        extra_args=["--thinking", "high"],
    )
    argv = cfg.build_argv()
    assert argv[0] == "/usr/local/bin/pi"
    assert "--mode" in argv
    assert "rpc" in argv
    assert "--no-session" in argv
    assert "--provider" in argv and "anthropic" in argv
    assert "--model" in argv and "claude-sonnet-4-20250514" in argv
    assert "--session-dir" in argv and "/tmp/pi-sessions" in argv
    assert "--name" in argv and "test-session" in argv
    assert "--thinking" in argv and "high" in argv


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("PI_MCP_BIN", "/opt/pi")
    monkeypatch.setenv("PI_MCP_CWD", "/workspace")
    monkeypatch.setenv("PI_MCP_PROVIDER", "openai")
    monkeypatch.setenv("PI_MCP_MODEL", "gpt-5.4")
    monkeypatch.setenv("PI_MCP_SESSION_DIR", "/sessions")
    monkeypatch.setenv("PI_MCP_SESSION_NAME", "env-session")
    monkeypatch.setenv("PI_MCP_NO_SESSION", "true")

    cfg = config_from_env()
    assert cfg.pi_bin == "/opt/pi"
    assert cfg.cwd == "/workspace"
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-5.4"
    assert cfg.session_dir == "/sessions"
    assert cfg.session_name == "env-session"
    assert cfg.no_session is True
