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
