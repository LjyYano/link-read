import pytest

from linkread_desktop.ai.openai_compatible import OpenAICompatibleProvider, validate_base_url
from linkread_desktop.ai.provider import ProviderError, make_provider


@pytest.mark.parametrize("url", [
    "ftp://api.example.com/v1", "http://api.example.com/v1", "http://[::2]/v1",
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
    assert validate_base_url("http://[::1]:8000/v1") == "http://[::1]:8000/v1"


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
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible._sleep", lambda *_: None)
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


def test_dashscope_disables_thinking_by_default_and_falls_back_on_http_400(monkeypatch):
    bodies = []

    class Response:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    def fake_post(url, **kwargs):
        bodies.append(dict(kwargs["json"]))
        if kwargs["json"].get("enable_thinking") is False and len(bodies) == 1:
            return Response(400, {"error": {"message": "enable_thinking unsupported"}})
        return Response(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    api = OpenAICompatibleProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwq-thinking-only",
        token="secret",
    )
    assert api.generate("hello", {"type": "object"}, timeout_seconds=30) == {"ok": True}
    assert bodies[0]["enable_thinking"] is False
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "enable_thinking" not in bodies[1]
    assert "response_format" not in bodies[1]
    # Subsequent call remembers fallback and does not trigger another 400.
    assert api.generate("hello again", {"type": "object"}, timeout_seconds=30) == {"ok": True}
    assert len(bodies) == 3 and "enable_thinking" not in bodies[2] and "response_format" not in bodies[2]


def test_non_dashscope_endpoint_does_not_send_enable_thinking(monkeypatch):
    seen = {}

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def fake_post(url, **kwargs):
        seen.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="gpt-4o-mini", token="secret")
    assert api.generate("hello", {"type": "object"}, timeout_seconds=30) == {"ok": True}
    assert "enable_thinking" not in seen


def test_pooled_connection_reset_retries_once_but_timeout_never_retries(monkeypatch):
    import requests as req

    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="gpt-4o-mini", token="secret")
    attempts = 0

    class Response:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def flaky_session_post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise req.ConnectionError("remote closed idle socket")
        return Response()

    monkeypatch.setattr(api._session, "post", flaky_session_post)
    assert api.generate("hello", {"type": "object"}, timeout_seconds=30) == {"ok": True}
    assert attempts == 2

    timeouts = 0

    def slow_session_post(url, **kwargs):
        nonlocal timeouts
        timeouts += 1
        raise req.Timeout("timed out")

    monkeypatch.setattr(api._session, "post", slow_session_post)
    with pytest.raises(ProviderError, match="超时"):
        api.generate("hello", {"type": "object"}, timeout_seconds=30)
    assert timeouts == 1


def test_transient_429_and_503_retry_with_backoff_and_single_line_fence(monkeypatch):
    sleeps = []
    calls = 0

    class Response:
        def __init__(self, status_code, headers=None, payload=None):
            self.status_code = status_code
            self.headers = headers or {}
            self._payload = payload
            self.closed = False

        def json(self):
            return self._payload

        def close(self):
            self.closed = True

    responses = [
        Response(429, headers={"Retry-After": "2"}),
        Response(503),
        Response(200, payload={"choices": [{"message": {"content": '```{"ok": true}```'}}]}),
    ]

    def fake_post(url, **kwargs):
        nonlocal calls
        resp = responses[calls]
        calls += 1
        return resp

    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible._sleep", sleeps.append)
    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="gpt-4o-mini", token="secret")
    assert api.generate("hello", {"type": "object"}, timeout_seconds=30) == {"ok": True}
    assert calls == 3
    assert sleeps[0] == 2.0 and len(sleeps) == 2
    assert all(r.closed for r in responses)


def test_concurrent_workers_use_isolated_thread_local_sessions():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    api = OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="gpt-4o-mini", token="secret")
    barrier = threading.Barrier(4)

    def worker():
        session = api._thread_session()
        barrier.wait(timeout=5)
        return id(session)

    with ThreadPoolExecutor(max_workers=4) as pool:
        session_ids = [f.result() for f in [pool.submit(worker) for _ in range(4)]]
    assert len(set(session_ids)) == 4


def test_subprocess_providers_and_ffmpeg_never_inherit_linkread_secret_tokens(monkeypatch, tmp_path):
    from pathlib import Path
    import subprocess
    from linkread_desktop.ai.provider import ClaudeProvider, CodexProvider
    from linkread_desktop.asr.segments import split_audio

    monkeypatch.setenv("LINKREAD_API_TOKEN", "chat-secret")
    monkeypatch.setenv("LINKREAD_ASR_TOKEN", "asr-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-secret")

    seen_envs = {}

    def fake_codex_run(cmd, **kwargs):
        seen_envs["codex"] = kwargs["env"]
        answer = Path(cmd[cmd.index("--output-last-message") + 1])
        answer.write_text('{"ok": true}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_claude_run(cmd, **kwargs):
        seen_envs["claude"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout='{"structured_output": {"ok": true}}', stderr="")

    monkeypatch.setattr("linkread_desktop.ai.provider.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("linkread_desktop.ai.provider.subprocess.run", fake_codex_run)
    assert CodexProvider().generate("hi", {"type": "object"}, timeout_seconds=10) == {"ok": True}
    assert "LINKREAD_API_TOKEN" not in seen_envs["codex"]
    assert "LINKREAD_ASR_TOKEN" not in seen_envs["codex"]
    assert "ANTHROPIC_API_KEY" not in seen_envs["codex"]

    monkeypatch.setattr("linkread_desktop.ai.provider.subprocess.run", fake_claude_run)
    assert ClaudeProvider().generate("hi", {"type": "object"}, timeout_seconds=10) == {"ok": True}
    assert "LINKREAD_API_TOKEN" not in seen_envs["claude"]
    assert "LINKREAD_ASR_TOKEN" not in seen_envs["claude"]
    assert seen_envs["claude"].get("ANTHROPIC_API_KEY") == "claude-secret"

    def fake_ffmpeg_run(cmd, **kwargs):
        seen_envs["ffmpeg"] = kwargs["env"]
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr("linkread_desktop.asr.segments.ffmpeg_binary", lambda *_: "/usr/bin/ffmpeg")
    monkeypatch.setattr("linkread_desktop.asr.segments.subprocess.run", fake_ffmpeg_run)
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"fake")
    with pytest.raises(Exception):
        split_audio(audio, tmp_path, minutes=5, duration_ms=600_000)
    assert "LINKREAD_API_TOKEN" not in seen_envs["ffmpeg"]
    assert "LINKREAD_ASR_TOKEN" not in seen_envs["ffmpeg"]
    assert "ANTHROPIC_API_KEY" not in seen_envs["ffmpeg"]
