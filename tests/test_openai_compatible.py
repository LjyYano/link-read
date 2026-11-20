import pytest

from linkread_desktop.ai.openai_compatible import OpenAICompatibleProvider, validate_base_url
from linkread_desktop.ai.provider import ProviderError, make_provider


@pytest.mark.parametrize("url", [
    "ftp://api.example.com/v1", "http://api.example.com/v1",
    "https://user:password@example.com/v1", "https://api.example.com/v1?token=secret",
    "https://api.example.com/v1#fragment", "https://api.example.com/v1/chat/completions",
])
def test_reject_unsafe_base_urls(url):
    with pytest.raises(ProviderError):
        validate_base_url(url)


def test_supports_nonstandard_api_root_and_local_development():
    assert validate_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1/") == (
        "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert validate_base_url("http://127.0.0.1:8000/v1") == "http://127.0.0.1:8000/v1"


def test_api_requires_model_and_token(monkeypatch):
    monkeypatch.delenv("LINKREAD_API_TOKEN", raising=False)
    with pytest.raises(ProviderError, match="Token"):
        make_provider("api", base_url="https://api.example.com/v1", model="model")
    with pytest.raises(ProviderError, match="模型"):
        make_provider("api", base_url="https://api.example.com/v1", token="secret")


def test_api_chat_completions_schema_and_secret_not_logged(monkeypatch):
    seen = {}
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": '{"summary": "有效结果"}'}}]}
    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    api = make_provider("api", base_url="https://example.com/compatible/v1/",
                        model="some-chat-model", token="private-token")
    result = api.generate("原始字幕", {"type": "object", "properties": {"summary": {"type": "string"}}},
                          timeout_seconds=60)
    assert result == {"summary": "有效结果"}
    assert seen["url"] == "https://example.com/compatible/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer private-token"
    assert seen["json"]["model"] == "some-chat-model"
    assert seen["json"]["messages"][1]["content"] == "原始字幕"
    assert seen["allow_redirects"] is False
    assert seen["timeout"] == (15, 60)
    assert "private-token" not in repr(result)


@pytest.mark.parametrize("status,expected", [
    (302, "重定向"), (401, "Token"), (403, "权限"), (429, "额度"), (503, "503"),
])
def test_api_errors_do_not_echo_response_or_secret(monkeypatch, status, expected):
    class Response:
        status_code = status
        text = "private-token VERY PRIVATE transcript"
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", lambda *a, **k: Response())
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="test", token="private-token")
    with pytest.raises(ProviderError) as error:
        api.generate("原始字幕", {"type": "object"}, timeout_seconds=20)
    assert expected in str(error.value)
    assert "private-token" not in str(error.value)
    assert "VERY PRIVATE" not in str(error.value)


def test_api_rejects_invalid_response(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "not json"}}]}
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", lambda *a, **k: Response())
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="test", token="private")
    with pytest.raises(ProviderError, match="结构化 JSON"):
        api.generate("hello", {"type": "object"}, timeout_seconds=20)


def test_api_connection_uses_active_model_with_no_schema(monkeypatch):
    seen = {}
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}
    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="model-1", token="secret")
    assert api.test_connection(timeout_seconds=20) is None
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["json"]["model"] == "model-1"
    assert seen["json"]["max_tokens"] == 32
    assert seen["allow_redirects"] is False


@pytest.mark.parametrize("status,expected", [
    (401, "Token"), (403, "权限"), (404, "Base URL"), (429, "额度"), (502, "HTTP 502"),
])
def test_api_connection_failure_does_not_echo_tokens(monkeypatch, status, expected):
    class Response:
        status_code = status
        text = "secret-key SHOULD NEVER LEAK"
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", lambda *args, **kwargs: Response())
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="model-1", token="secret-key")
    with pytest.raises(ProviderError) as error:
        api.test_connection()
    assert expected in str(error.value)
    assert "secret-key" not in str(error.value)


def test_api_connection_rejects_non_chat_completions(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {"hello": "world"}
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", lambda *args, **kwargs: Response())
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="model-1", token="secret")
    with pytest.raises(ProviderError, match="兼容 Chat Completions"):
        api.test_connection()
