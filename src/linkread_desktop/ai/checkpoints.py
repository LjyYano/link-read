"""Bounded model calls and validated, source-specific batch checkpoints."""
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from threading import Condition
import time

from ..errors import LinkReadError, error_context
from ..progress import RequestStatus, report
from ..storage import write_text


def provider_identity(provider):
    return {"provider": getattr(provider, "name", "codex"),
            "model": getattr(provider, "model", None),
            "endpoint": getattr(provider, "base_url", None),
            "executable": getattr(provider, "executable", None)}


class RunProvider:
    """A fair concurrency budget shared by all stages, plus content-free metrics."""
    def __init__(self, provider, limit=4, *, progress=None):
        if not 1 <= limit <= 4:
            raise LinkReadError("--jobs 需要在 1 到 4 之间")
        self.provider = provider
        self.name = getattr(provider, "name", "codex")
        self.limit = limit
        self.condition = Condition()
        self._waiting = deque()
        self._active = 0
        self._validating = self._completed = self._sequence = self._request_id = 0
        self.progress = progress
        self.calls, self.cache_hits, self.model_seconds = 0, 0, 0.0
        self.peak_concurrency = 0
        self.queue_seconds = self.longest_model_seconds = 0.0
        self.prompt_chars = self.max_prompt_chars = 0
        self.output_chars = self.max_output_chars = 0
        self.identity = provider_identity(provider)

    def generate(self, prompt, schema, *, timeout_seconds):
        return self.execute(prompt, schema, lambda value: value, timeout_seconds=timeout_seconds)

    def _event(self, request_id, stage, phase, *, message=None):
        """Called under the condition so snapshots and sequence share one order."""
        self._sequence += 1
        messages = {"queued": "模型请求已进入并发队列", "running": "模型请求已发出，等待完整返回",
                    "received": "已收到模型结果", "validating": "正在校验模型结果与原文约束",
                    "complete": "模型结果已通过校验", "cache": "已复用校验通过的批次",
                    "failed": "本次模型请求未完成"}
        report(self.progress, {"analysis": "ai_request", "polish": "polish_request",
                               "publication": "publication_request", "translation": "translation_request"}.get(stage, "model_request"),
               message or messages[phase], request=RequestStatus(
                   sequence=self._sequence, id=f"request-{request_id}", stage=stage, phase=phase,
                   active=self._active, queued=len(self._waiting), validating=self._validating,
                   completed=self._completed, cacheHits=self.cache_hits))

    def execute(self, prompt, schema, validate, *, timeout_seconds, stage=None, cancel=None):
        queued = time.monotonic()
        ticket = object()
        with self.condition:
            self._request_id += 1
            request_id = self._request_id
            self._waiting.append(ticket)
            self._event(request_id, stage, "queued")
            self.condition.notify_all()
            # FIFO admission stops a busy proofreading executor from repeatedly
            # taking slots before already-queued analysis/publication requests.
            try:
                while not (self._waiting[0] is ticket and self._active < self.limit):
                    if cancel is not None and cancel.is_set():
                        raise LinkReadError("生成已取消", code="cancelled")
                    self.condition.wait(timeout=0.5 if cancel is not None else None)
            except BaseException:
                self._waiting.remove(ticket)
                self.condition.notify_all()
                raise
            self._waiting.popleft()
            self._active += 1
            self.peak_concurrency = max(self.peak_concurrency, self._active)
            started = time.monotonic()
            self.queue_seconds += started - queued
            self.prompt_chars += len(prompt)
            self.max_prompt_chars = max(self.max_prompt_chars, len(prompt))
            self._event(request_id, stage, "running")
            self.condition.notify_all()
        output_chars = 0
        try:
            if cancel is not None and cancel.is_set():
                raise LinkReadError("生成已取消", code="cancelled")
            with error_context(stage):
                extra = {}
                if stage in {"polish", "translation"} and getattr(self.provider, "supports_reasoning_effort", False):
                    extra["reasoning_effort"] = "low"
                if cancel is not None and getattr(self.provider, "supports_cancel", False):
                    extra["cancel"] = cancel
                payload = self.provider.generate(prompt, schema, timeout_seconds=timeout_seconds, **extra)
            # Count only characters; never persist prompts, model text or tokens.
            try:
                output_chars = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            except (TypeError, ValueError):
                pass  # Validation, rather than telemetry, owns malformed output.
        except BaseException:
            with self.condition:
                self._active -= 1
                self._event(request_id, stage, "failed")
                self.condition.notify_all()
            raise
        else:
            with self.condition:
                self._active -= 1
                self._event(request_id, stage, "received")
                self._validating += 1
                self._event(request_id, stage, "validating")
                self.condition.notify_all()
        finally:
            elapsed = time.monotonic() - started
            with self.condition:
                self.calls += 1
                self.model_seconds += elapsed
                self.longest_model_seconds = max(self.longest_model_seconds, elapsed)
                self.output_chars += output_chars
                self.max_output_chars = max(self.max_output_chars, output_chars)
        try:
            with error_context(stage):
                result = validate(payload)
        except BaseException:
            with self.condition:
                self._validating -= 1
                self._event(request_id, stage, "failed", message="模型结果未通过原文与结构校验")
            raise
        with self.condition:
            self._validating -= 1
            self._completed += 1
            self._event(request_id, stage, "complete")
        return result

    def cache_hit(self, stage=None):
        with self.condition:
            self.cache_hits += 1
            self._request_id += 1
            self._event(self._request_id, stage, "cache")


def validated_call(provider, prompt, schema, validate, *, timeout_seconds, cache_dir=None, stage=None, cancel=None):
    """Never cache unverified model output; always revalidate stored checkpoints."""
    if cancel is not None and cancel.is_set():
        raise LinkReadError("生成已取消", code="cancelled")
    path = None
    if cache_dir is not None:
        identity = getattr(provider, "identity", provider_identity(provider))
        fingerprint = sha256(json.dumps(["validated-v1", identity, prompt, schema],
                                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        folder = Path(cache_dir)
        if folder.is_symlink():
            raise LinkReadError("模型批次缓存不可使用符号链接")
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / (fingerprint + ".json")
        if path.is_symlink():
            raise LinkReadError("模型批次缓存不可使用符号链接")
        if path.is_file() and path.stat().st_size < 8 * 1024**2:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                result = validate(payload)
                if isinstance(provider, RunProvider):
                    provider.cache_hit(stage)
                return result
            except (ValueError, TypeError, KeyError):
                # Interrupted/corrupt/invalid checkpoints are not trusted.
                pass
    payload = None
    def checked(value):
        nonlocal payload
        result = validate(value)
        payload = value
        return result
    with error_context(stage):
        if isinstance(provider, RunProvider):
            result = provider.execute(prompt, schema, checked, timeout_seconds=timeout_seconds, stage=stage, cancel=cancel)
        else:
            extra = {"cancel": cancel} if cancel is not None and getattr(provider, "supports_cancel", False) else {}
            result = checked(provider.generate(prompt, schema, timeout_seconds=timeout_seconds, **extra))
    if path is not None:
        write_text(path, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return result
