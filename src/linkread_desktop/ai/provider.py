"""Secure, non-interactive CLI adapters with strict JSON output and timeouts.

LinkRead does not provide an authentication bypass. Use your own compatible
Codex login, or a Claude Console API key for `claude --bare`.
"""

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from ..errors import LinkReadError


class ProviderError(LinkReadError):
    """A model provider could not return a valid JSON response."""
    default_code = "model_failed"
    default_retryable = True
    default_action = "retry"


class StructuredProvider(Protocol):
    name: str

    def generate(self, prompt: str, schema: dict, *, timeout_seconds: int) -> dict: ...


def _process(command: list[str], prompt: str, *, cwd: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, input=prompt, text=True, encoding="utf-8", errors="replace",
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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

    def __init__(self, model: str | None = None, *, executable: str = "codex"):
        self.model = model
        self.executable = executable

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
            if reasoning_effort in {"low", "medium", "high"}:
                cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
            cmd.append("-")
            proc = _process(cmd, prompt, cwd=directory, timeout_seconds=timeout_seconds)
            if proc.returncode != 0:
                raise _failure(self.name, proc)
            if not answer_file.is_file():
                raise ProviderError("Codex 未输出结构化结果文件，请检查 CLI 版本")
            return _json_object(answer_file.read_text(encoding="utf-8"), self.name)


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, *, executable: str = "claude"):
        self.model = model
        self.executable = executable

    def generate(self, prompt: str, schema: dict, *, timeout_seconds: int) -> dict:
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
            proc = _process(cmd, prompt, cwd=directory, timeout_seconds=timeout_seconds)
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
                  base_url: str | None = None, token: str | None = None) -> StructuredProvider:
    if name == "codex":
        return CodexProvider(model=model, executable=executable or name)
    if name == "claude":
        return ClaudeProvider(model=model, executable=executable or name)
    if name == "api":
        from .openai_compatible import OpenAICompatibleProvider
        return OpenAICompatibleProvider(base_url=base_url or "", model=model or "", token=token)
    raise ProviderError("--provider 仅支持 codex、claude 或 api")
