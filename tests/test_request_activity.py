from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from subprocess import CompletedProcess
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from linkread_desktop.ai.checkpoints import RunProvider, validated_call
from linkread_desktop.ai.openai_compatible import OpenAICompatibleProvider
from linkread_desktop.ai.provider import ProviderError, _failure
from linkread_desktop.asr.bailian import BailianAsr
from linkread_desktop.asr.config import AsrConfig, AsrError
from linkread_desktop.errors import ExtractionError, error_context, error_details


def test_request_counts_are_real_bounded_and_monotonically_ordered():
    release, queued = Event(), Event()
    events = []
    def progress(event):
        events.append(event)
        if event.request.active == 2 and event.request.queued:
            queued.set()
    class Provider:
        name = "api"
        def generate(self, *_args, **_kwargs):
            assert release.wait(3)
            return {"ok": True}
    provider = RunProvider(Provider(), limit=2, progress=progress)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(validated_call, provider, str(i), {}, lambda value: value["ok"],
                            timeout_seconds=10, stage="polish") for i in range(3)]
        assert queued.wait(2)
        assert not any(event.request.phase == "received" for event in events)
        release.set()
        assert [job.result() for job in jobs] == [True] * 3
    states = [event.request for event in events]
    assert [state.sequence for state in states] == list(range(1, len(states) + 1))
    assert all(0 <= state.active <= 2 and state.queued >= 0 and state.validating >= 0 for state in states)
    assert states[-1].active == states[-1].queued == states[-1].validating == 0
    assert states[-1].completed == 3
    for request_id in {state.id for state in states}:
        assert [state.phase for state in states if state.id == request_id] == [
            "queued", "running", "received", "validating", "complete"]
    assert all(event.stage == "polish_request" for event in events)
    assert "cacheHits" in asdict(states[0])
    assert all(isinstance(state.id, str) for state in states)


def test_cache_reuse_emits_verified_cache_hit_without_model_request(tmp_path):
    class Provider:
        name = "api"
        def generate(self, *_a, **_k):
            return {"ok": "original"}
    events, validations = [], []
    provider = RunProvider(Provider(), progress=events.append)
    def validate(value):
        validations.append(value)
        return value["ok"]
    for _ in range(2):
        assert validated_call(provider, "prompt", {}, validate, timeout_seconds=10,
                              stage="analysis", cache_dir=tmp_path) == "original"
    assert provider.calls == 1 and provider.cache_hits == 1 and len(validations) == 2
    assert events[-1].request.phase == "cache" and events[-1].request.cacheHits == 1


def test_rejected_output_never_reports_verified_completion_or_saves_checkpoint(tmp_path):
    provider = RunProvider(SimpleNamespace(name="api", generate=lambda *_a, **_k: {"bad": True}),
                           progress=(events := []).append)
    def reject(_value):
        raise ValueError("invalid")
    with pytest.raises(ValueError):
        validated_call(provider, "prompt", {}, reject, timeout_seconds=10,
                       stage="analysis", cache_dir=tmp_path)
    assert events[-1].request.phase == "failed"
    assert not any(event.request.phase == "complete" for event in events)
    assert not list(tmp_path.glob("*.json"))
    assert events[-1].request.active == events[-1].request.validating == 0


def test_failure_stage_is_call_context_and_message_does_not_change_classification():
    def fail(*_a, **_k):
        raise ProviderError("mentions Token and timeout but has no typed evidence")
    provider = RunProvider(SimpleNamespace(name="api", generate=fail), progress=(events := []).append)
    with pytest.raises(ProviderError) as result:
        validated_call(provider, "prompt", {}, lambda value: value, timeout_seconds=10, stage="polish")
    assert error_details(result.value) == {"message": str(result.value), "code": "model_failed",
                                          "stage": "polish", "retryable": True, "action": "retry"}
    assert events[-1].request.active == 0 and events[-1].request.phase == "failed"


@pytest.mark.parametrize("status,code,action,retryable", [
    (401, "authentication_failed", "settings", False),
    (403, "permission_denied", "settings", False),
    (429, "rate_or_quota_limited", "settings", True),
    (503, "service_unavailable", "retry", True),
    (404, "configuration_invalid", "settings", False),
])
def test_http_error_classification_uses_status_without_echoing_response(monkeypatch, status, code, action, retryable):
    response = SimpleNamespace(status_code=status, text="secret-token private transcript")
    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", lambda *_a, **_k: response)
    provider = OpenAICompatibleProvider(base_url="https://example.com/v1", model="test", token="secret-token")
    with pytest.raises(ProviderError) as result:
        provider.generate("private transcript", {}, timeout_seconds=10)
    failure = error_details(result.value)
    assert (failure["code"], failure["action"], failure["retryable"]) == (code, action, retryable)
    assert "secret-token" not in json.dumps(failure) and "private transcript" not in json.dumps(failure)


def test_source_network_cause_is_typed_and_failed_stage_is_not_guessed():
    with pytest.raises(ExtractionError) as result:
        with error_context("extract"):
            try:
                raise requests.Timeout("signed URL and secret must not escape")
            except requests.Timeout as cause:
                raise ExtractionError("来源请求未完成") from cause
    details = error_details(result.value)
    assert details["code"] == "source_timeout" and details["stage"] == "extract"
    assert details["retryable"] and "secret" not in json.dumps(details)


def test_cli_failure_does_not_echo_untrusted_stderr():
    error = _failure("codex", CompletedProcess([], 1, stdout="private source", stderr="Token secret-source"))
    assert "secret-source" not in str(error) and "private source" not in str(error)
    assert error.code == "provider_exit" and error.action == "details"


def test_ambiguous_asr_submission_never_offers_safe_retry_or_submits_again(tmp_path, monkeypatch):
    api = BailianAsr(AsrConfig(token="offline-test"))
    state = tmp_path / "task.json"
    state.write_text(json.dumps({"audio_url": "oss://audio", "uploaded_at": __import__("time").time()}))
    attempts = []
    def submit(*_args):
        attempts.append(True)
        raise AsrError("network", code="network_error", retryable=True, action="retry")
    monkeypatch.setattr(api, "submit", submit)
    for _ in range(2):
        with pytest.raises(AsrError) as result:
            api.transcribe(tmp_path / "unused", state)
        assert result.value.code == "asr_submission_unknown"
        assert result.value.retryable is False
        assert result.value.stage == "asr"
    assert attempts == [True] and json.loads(state.read_text())["submitting"] is True
