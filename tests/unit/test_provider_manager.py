from gimp_mcp.provider_manager import ProviderManager


def test_account_model_prefix_is_stripped():
    assert ProviderManager._strip_account_model("chatgpt", "account:chatgpt/gpt-x") == "gpt-x"
    assert ProviderManager._strip_account_model("claude", "sonnet") == "sonnet"


def test_chatgpt_models_come_from_codex_catalog(monkeypatch):
    manager = ProviderManager()
    monkeypatch.setattr(manager, "_codex_rpc", lambda method, params: {
        "data": [{"id": "gpt-test", "model": "gpt-test", "displayName": "GPT Test", "isDefault": True}]
    })
    rows = manager.models("chatgpt")
    assert rows == [{
        "provider": "chatgpt", "model": "gpt-test",
        "id": "account:chatgpt/gpt-test", "display": "GPT Test", "is_default": True,
    }]


def test_cross_provider_account_model_is_rejected():
    manager = ProviderManager()
    try:
        manager._strip_account_model("chatgpt", "account:claude/opus")
    except Exception as exc:
        assert "Provider/model mismatch" in str(exc)
    else:
        raise AssertionError("cross-provider model must be rejected")


def test_codex_mcp_disable_args_from_config(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text('''[mcp_servers.alpha]\nurl="http://127.0.0.1/a"\n[mcp_servers.beta]\ncommand="x"\n''')
    monkeypatch.setenv("CODEX_HOME", str(home))
    args = ProviderManager._codex_mcp_disable_args()
    assert "mcp_servers.alpha.enabled=false" in args
    assert "mcp_servers.beta.enabled=false" in args


def test_triforce_config_reuses_local_aicoder_session_when_env_missing(monkeypatch, tmp_path):
    import json
    from gimp_mcp.provider_manager import ProviderManager
    monkeypatch.delenv("GIMP_MCP_TRIFORCE_TOKEN", raising=False)
    monkeypatch.delenv("GIMP_MCP_TRIFORCE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / ".config" / "ai-coder"
    config.mkdir(parents=True)
    (config / "session.json").write_text(json.dumps({"base_url":"https://api.ailinux.me", "token":"account-token"}))
    assert ProviderManager._triforce_config() == ("https://api.ailinux.me", "account-token")


def test_triforce_config_env_token_has_priority(monkeypatch, tmp_path):
    from gimp_mcp.provider_manager import ProviderManager
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIMP_MCP_TRIFORCE_URL", "https://example.invalid/")
    monkeypatch.setenv("GIMP_MCP_TRIFORCE_TOKEN", "explicit-token")
    assert ProviderManager._triforce_config() == ("https://example.invalid", "explicit-token")


def test_triforce_models_normalizes_string_catalog(monkeypatch):
    from gimp_mcp.provider_manager import ProviderManager
    manager = ProviderManager()
    monkeypatch.setattr(manager, "_triforce_request", lambda *a, **k: {"models": ["openai/test", {"id":"other/test", "name":"Other"}]})
    rows = manager.triforce_models()
    assert rows[0]["model"] == "openai/test"
    assert rows[0]["id"] == "openai/test"
    assert rows[1]["model"] == "other/test"
