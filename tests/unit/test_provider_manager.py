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
