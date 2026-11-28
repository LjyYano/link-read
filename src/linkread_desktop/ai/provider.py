"""Secure, non-interactive CLI adapters with strict JSON output and timeouts.

LinkRead does not provide an authentication bypass. Use your own compatible
Codex login, or a Claude Console API key for `claude --bare`.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from ..errors import LinkReadError

_SENSITIVE_ENV_VARS = ("LINKREAD_API_TOKEN", "LINKREAD_ASR_TOKEN")
_CLAUDE_SETTINGS_ENV_ALLOWLIST = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_CUSTOM_HEADERS",
)
_MAX_CLAUDE_SETTINGS_BYTES = 4 * 1024 * 1024
CODEX_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
CLAUDE_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _claude_settings_env() -> dict[str, str]:
    """Read allowlisted Anthropic API env entries from ~/.claude/settings.json (e.g. cc-switch)."""
    custom_dir = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    config_dir = Path(custom_dir) if custom_dir and Path(custom_dir).is_absolute() else Path.home() / ".claude"
    settings_path = config_dir / "settings.json"
    try:
        if not settings_path.is_file() or settings_path.stat().st_size > _MAX_CLAUDE_SETTINGS_BYTES:
            return {}
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw_env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(raw_env, dict):
        return {}
    result: dict[str, str] = {}
    for key in _CLAUDE_SETTINGS_ENV_ALLOWLIST:
        val = raw_env.get(key)
        if isinstance(val, str) and val.strip() and not any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in val):
            result[key] = val
    return result


def _validated_cli_model(model: str | None) -> str | None:
    if model is None:
        return None
    cleaned = model.strip()
    if not cleaned:
        return None
    if (len(cleaned.encode("utf-8")) > 256
            or cleaned.startswith("-")
            or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in cleaned)):
        raise ProviderError("模型名称无效，请检查设置", code="configuration_invalid", retryable=False, action="settings")
    return cleaned


def _validated_effort(effort: str | None, allowed: frozenset[str]) -> str | None:
    if effort is None:
        return None
    cleaned = effort.strip().lower()
    if not cleaned:
        return None
    if cleaned not in allowed:
        raise ProviderError("不支持的思考深度设置，请检查配置", code="configuration_invalid", retryable=False, action="settings")
    return cleaned


def _subprocess_env(*, keep_anthropic: bool) -> dict[str, str]:
    env = dict(os.environ)
    for key in _SENSITIVE_ENV_VARS:
        env.pop(key, None)
    if not keep_anthropic:
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    elif not env.get("ANTHROPIC_API_KEY") and not env.get("ANTHROPIC_AUTH_TOKEN"):
        for key, value in _claude_settings_env().items():
            env.setdefault(key, value)
    return env


class ProviderError(LinkReadError):
    """A model provider could not return a valid JSON response."""
    default_code = "model_failed"
    default_retryable = True
    default_action = "retry"


class StructuredProvider(Protocol):
    name: str

    def generate(self, prompt: str, schema: dict, *, timeout_seconds: int) -> dict: ...


def _process(
    command: list[str], prompt: str, *, cwd: str, timeout_seconds: int, keep_anthropic: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, input=prompt, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, env=_subprocess_env(keep_anthropic=keep_anthropic),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout_seconds, check=False,
        )
    except FileNotFoundError as exc:
        raise ProviderError(f"未找到 {command[0]}；请先安装并配置对应 CLI", code="dependency_missing", retryable=False, action="setup") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"{command[0]} 调用超过 {timeout_seconds} 秒；可使用 --timeout 增大限制", code="model_timeout") from exc
    except OSError as exc:
        raise ProviderError(f"启动 {command[0]} 失败：{exc}", code="provider_start_failed", retryable=False, action="setup") from exc


def _failure(provider: str, process: subprocess.CompletedProcess[str]) -> ProviderError:
    # Do not print full stdout: model results may contain the original transcript.
    # CLI stderr is untrusted and may contain credentials or echoed transcripts.
    # Exit status alone cannot identify authentication vs. transport failures.
    return ProviderError(f"{provider} 返回错误（退出码 {process.returncode}），请检查工具配置或稍后重试",
                         code="provider_exit", retryable=False, action="details")


def _json_object(payload: str, name: str) -> dict:
    try:
        result = json.loads(payload)
    except ValueError as exc:
        raise ProviderError(f"{name} 没有返回有效 JSON；请检查 CLI 版本与模型输出", code="model_output_invalid") from exc
    if not isinstance(result, dict):
        raise ProviderError(f"{name} 返回的 JSON 必须是对象", code="model_output_invalid")
    return result


class CodexProvider:
    name = "codex"
    supports_reasoning_effort = True

    def __init__(self, model: str | None = None, *, executable: str = "codex",
                 reasoning_effort: str | None = None):
        self.model = _validated_cli_model(model)
        self.executable = executable
        self.reasoning_effort = _validated_effort(reasoning_effort, CODEX_REASONING_EFFORTS)

    def generate(self, prompt: str, schema: dict, *, timeout_seconds: int,
                 reasoning_effort: str | None = None) -> dict:
        if not shutil.which(self.executable):
            raise ProviderError("没有找到 codex。请安装 Codex CLI 并完成登录", code="dependency_missing", retryable=False, action="setup")
        with TemporaryDirectory(prefix="linkread-codex-") as directory:
            schema_file = Path(directory) / "schema.json"
            answer_file = Path(directory) / "answer.json"
            schema_file.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            cmd = [
                self.executable, "exec", "--sandbox", "read-only", "--skip-git-repo-check",
                "--output-schema", str(schema_file), "--output-last-message", str(answer_file),
            ]
            if self.model:
                cmd.extend(["--model", self.model])
            effective_effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
            if effective_effort in CODEX_REASONING_EFFORTS:
                cmd.extend(["-c", f'model_reasoning_effort="{effective_effort}"'])
            cmd.append("-")
            proc = _process(cmd, prompt, cwd=directory, timeout_seconds=timeout_seconds, keep_anthropic=False)
            if proc.returncode != 0:
                raise _failure(self.name, proc)
            if not answer_file.is_file():
                raise ProviderError("Codex 未输出结构化结果文件，请检查 CLI 版本")
            return _json_object(answer_file.read_text(encoding="utf-8"), self.name)


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, *, executable: str = "claude",
                 reasoning_effort: str | None = None):
        self.model = _validated_cli_model(model)
        self.executable = executable
        self.reasoning_effort = _validated_effort(reasoning_effort, CLAUDE_REASONING_EFFORTS)

    def generate(self, prompt: str, schema: dict, *, timeout_seconds: int,
                 reasoning_effort: str | None = None) -> dict:
        if not shutil.which(self.executable):
            raise ProviderError("没有找到 claude。请安装 Claude Code，并使用合规的 API Key 配置", code="dependency_missing", retryable=False, action="setup")
        with TemporaryDirectory(prefix="linkread-claude-") as directory:
            cmd = [
                self.executable, "--bare", "-p", "--output-format", "json",
                "--json-schema", json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                "--tools", "", "--disallowedTools", "mcp__*", "--no-session-persistence",
            ]
            if self.model:
                cmd.extend(["--model", self.model])
            effective_effort = reasoning_effort if reasoning_effort is not None else self.reasoning_effort
            if effective_effort in CLAUDE_REASONING_EFFORTS:
                cmd.extend(["--effort", effective_effort])
            proc = _process(cmd, prompt, cwd=directory, timeout_seconds=timeout_seconds, keep_anthropic=True)
            if proc.returncode != 0:
                raise _failure(self.name, proc)
            envelope = _json_object(proc.stdout, self.name)
            if envelope.get("is_error"):
                raise ProviderError("Claude 返回错误，未生成有效的结构化结果")
            result = envelope.get("structured_output")
            if not isinstance(result, dict):
                raise ProviderError("Claude 未返回 structured_output；需要支持 --json-schema 的 Claude Code 版本")
            return result


def make_provider(name: str, *, model: str | None = None, executable: str | None = None,
                  base_url: str | None = None, token: str | None = None,
                  reasoning_effort: str | None = None) -> StructuredProvider:
    if name == "codex":
        return CodexProvider(model=model, executable=executable or name, reasoning_effort=reasoning_effort)
    if name == "claude":
        return ClaudeProvider(model=model, executable=executable or name, reasoning_effort=reasoning_effort)
    if name == "api":
        from .openai_compatible import OpenAICompatibleProvider
        return OpenAICompatibleProvider(base_url=base_url or "", model=model or "", token=token)
    raise ProviderError("--provider 仅支持 codex、claude 或 api")
