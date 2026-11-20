"""Paraformer file transcription over documented DashScope REST APIs.

Only the API host receives the Token. OSS upload/result requests never receive
Authorization. Temporary OSS objects expire on the provider side after 48 h;
this is a low-volume desktop/testing transport, not a production OSS service.
"""
import io
import json
import re
import time
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import requests

from ..models import TranscriptSegment
from ..progress import ProgressCallback, report
from ..errors import error_operation
from ..storage import write_text
from .config import AsrConfig, AsrError, api_root

class AsrHttpError(AsrError):
    def __init__(self, message: str, status: int):
        if status in {401, 403}:
            code, retryable, action = ("authentication_failed" if status == 401 else "permission_denied"), False, "settings"
        elif status == 429:
            code, retryable, action = "rate_or_quota_limited", True, "settings"
        elif status >= 500:
            code, retryable, action = "service_unavailable", True, "retry"
        else:
            code, retryable, action = "configuration_invalid", False, "settings"
        super().__init__(message, code=code, retryable=retryable, action=action)
        self.status = status


class AsrTaskFailed(AsrError):
    default_code = "asr_task_failed"
    default_retryable = True
    default_action = "retry"


MAX_AUDIO_BYTES = 200 * 1024 * 1024
TEST_AUDIO = "https://dashscope.oss-cn-beijing.aliyuncs.com/samples/audio/paraformer/hello_world_female2.wav"


def oss_https(url: str) -> str:
    p = urlsplit(url)
    if (p.scheme != "https" or p.username or p.password or p.port
            or not re.fullmatch(r"[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com", p.hostname or "")
            or "-internal." in (p.hostname or "")):
        raise AsrError("百炼返回了非预期的存储地址，已停止请求")
    return url


def segments_from_result(data: dict, *, allow_empty: bool = False) -> list[TranscriptSegment]:
    """Use service sentence timestamps. Do not invent times from text lengths."""
    segments = []
    try:
        tracks = data["transcripts"]
        if not isinstance(tracks, list):
            raise ValueError()
        for track in tracks:
            if track.get("channel_id", 0) != 0:
                continue
            for sentence in track.get("sentences", []):
                text = sentence["text"]
                if not isinstance(text, str) or not text.strip():
                    continue
                a, b = sentence["begin_time"], sentence["end_time"]
                if type(a) is not int or type(b) is not int:
                    raise ValueError()
                segments.append(TranscriptSegment(start_ms=a, end_ms=b, text=text))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AsrError("语音识别返回了无效的句子时间戳") from exc
    if not segments and not allow_empty:
        raise AsrError("语音识别没有返回可用语句；可能没有人声或音频格式不受支持")
    return sorted(segments, key=lambda x: x.start_ms)


class MultipartBody:
    """Stream form fields + audio with a known length, without loading it in RAM."""
    def __init__(self, fields: dict, path: Path):
        boundary = "linkread-" + uuid4().hex
        self.content_type = "multipart/form-data; boundary=" + boundary
        parts = []
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio{path.suffix}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        first = b"".join(parts)
        end = f"\r\n--{boundary}--\r\n".encode()
        self.length = len(first) + path.stat().st_size + len(end)
        self.streams = [io.BytesIO(first), path.open("rb"), io.BytesIO(end)]
        self.index = 0

    def __len__(self):
        return self.length

    def read(self, size=65536):
        if size is None or size < 0:
            size = 65536
        out = bytearray()
        while len(out) < size and self.index < len(self.streams):
            chunk = self.streams[self.index].read(size - len(out))
            if chunk:
                out.extend(chunk)
            else:
                self.index += 1
        return bytes(out)

    def close(self):
        for stream in self.streams:
            stream.close()


class BailianAsr:
    def __init__(self, config: AsrConfig, *, session=None):
        self.config = config
        self.token = config.credential()
        self.base = api_root(config.base_url)
        self.http = session or requests.Session()

    def _request(self, method: str, url: str, *, authorized=True, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        if authorized:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = self.http.request(method, url, headers=headers, timeout=(10, 60),
                                         allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise AsrError("语音服务网络请求失败，请检查网络后重试", code="network_error", retryable=True, action="retry") from exc
        if not 200 <= response.status_code < 300:
            status = response.status_code
            messages = {401: "语音识别 Token 无效", 403: "没有访问语音模型的权限",
                        429: "语音识别额度不足或请求过于频繁"}
            response.close()
            raise AsrHttpError(messages.get(status, f"语音服务返回 HTTP {status}，请检查 ASR 设置"), status)
        return response

    def _json(self, method: str, url: str, **kwargs) -> dict:
        response = self._request(method, url, **kwargs)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError as exc:
            raise AsrError("语音服务没有返回有效的 JSON") from exc
        finally:
            response.close()

    @error_operation("upload")
    def upload(self, audio: Path, progress=None) -> str:
        if not 0 < audio.stat().st_size <= MAX_AUDIO_BYTES:
            raise AsrError("音频为空或超过当前 200 MB 上传上限")
        report(progress, "asr_upload", "正在上传音频至百炼临时存储")
        upload_root = ("https://dashscope-intl.aliyuncs.com/api/v1" if "dashscope-intl" in self.base
                       else "https://dashscope.aliyuncs.com/api/v1")
        policy = self._json("GET", upload_root + "/uploads",
                            params={"action": "getPolicy", "model": self.config.model}).get("data", {})
        try:
            host = oss_https(policy["upload_host"])
            limit = float(policy.get("max_file_size_mb", 1024)) * 1024 * 1024
            if audio.stat().st_size > limit:
                raise AsrError("音频超过此模型的上传大小限制")
            key = policy["upload_dir"].rstrip("/") + "/" + uuid4().hex + audio.suffix
            fields = {"OSSAccessKeyId": policy["oss_access_key_id"], "Signature": policy["signature"],
                      "policy": policy["policy"], "x-oss-object-acl": policy["x_oss_object_acl"],
                      "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
                      "key": key, "success_action_status": "200"}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise AsrError("语音服务返回了无效的上传凭证") from exc
        body = MultipartBody(fields, audio)
        try:
            self._request("POST", host, authorized=False, data=body,
                          headers={"Content-Type": body.content_type, "Content-Length": str(len(body))}).close()
        finally:
            body.close()
        report(progress, "asr_upload_done", "音频上传完成")
        return "oss://" + key

    @error_operation("asr")
    def submit(self, audio_url: str) -> str:
        value = self._json("POST", self.base + "/services/audio/asr/transcription",
                           headers={"X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"},
                           json={"model": self.config.model, "input": {"file_urls": [audio_url]},
                                 "parameters": {"channel_id": [0], "disfluency_removal_enabled": False,
                                                "timestamp_alignment_enabled": True}})
        task_id = value.get("output", {}).get("task_id")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{8,100}", task_id):
            raise AsrError("语音服务未返回任务编号；请检查模型与 Token 权限")
        return task_id

    @error_operation("asr")
    def wait(self, task_id: str, *, progress=None, timeout=1800, poll_seconds=5) -> dict:
        deadline = time.monotonic() + timeout
        if not re.fullmatch(r"[A-Za-z0-9-]{8,100}", task_id):
            raise AsrError("缓存中的语音任务编号无效")
        while time.monotonic() < deadline:
            output = self._json("GET", self.base + "/tasks/" + task_id).get("output", {})
            if not isinstance(output, dict):
                raise AsrError("语音服务未返回有效任务状态")
            status = output.get("task_status")
            if status == "SUCCEEDED":
                results = output.get("results", [])
                if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict) or results[0].get("subtask_status") != "SUCCEEDED":
                    raise AsrTaskFailed("语音文件转写失败，请检查音频是否完整、可访问")
                url = oss_https(results[0].get("transcription_url", ""))
                report(progress, "asr_result", "转写完成，正在读取带时间戳的识别结果")
                return self._json("GET", url, authorized=False)
            if status not in {"PENDING", "RUNNING"}:
                raise AsrTaskFailed("语音识别任务失败或已过期；再次点击重试将重新提交转写")
            report(progress, "asr_wait", "语音识别排队中" if status == "PENDING" else "正在语音识别，等待百炼返回完整结果")
            time.sleep(poll_seconds)
        raise AsrError("语音识别等待超时，云端任务已保留；重试会继续查询，不会重新计费提交", code="asr_wait_timeout", retryable=True, action="retry")

    @error_operation("asr")
    def transcribe(self, audio: Path, state_file: Path, *, progress=None, allow_empty=False) -> list[TranscriptSegment]:
        state = json.loads(state_file.read_text()) if state_file.is_file() else {}
        task = state.get("task_id")
        if task:
            report(progress, "asr_resume", "继续查询已提交的语音任务，不重复上传")
        else:
            if state.get("submitting"):
                raise AsrError("上次提交语音任务的结果未确认，为避免重复计费已停止。请在百炼控制台核对任务后清除此任务的 ASR 缓存", code="asr_submission_unknown")
            remote = state.get("audio_url")
            if not remote or time.time() - state.get("uploaded_at", 0) > 46 * 3600:
                remote = self.upload(audio, progress)
                state = {"audio_url": remote, "uploaded_at": time.time()}
                write_text(state_file, json.dumps(state))
            # A timeout during POST is ambiguous; do not automatically POST again.
            state["submitting"] = True
            write_text(state_file, json.dumps(state))
            report(progress, "asr_submit", "正在提交语音识别任务")
            try:
                task = self.submit(remote)
            except AsrHttpError as exc:
                # Explicit 4xx rejection creates no task. A fresh user retry is safe.
                if 400 <= exc.status < 500:
                    state["submitting"] = False
                    write_text(state_file, json.dumps(state))
                else:
                    exc.code, exc.retryable, exc.action = "asr_submission_unknown", False, "details"
                raise
            except AsrError as exc:
                raise AsrError("提交语音任务的结果未确认，已保留记录以避免重复计费；请先核对云端任务",
                               code="asr_submission_unknown") from exc
            state.update(task_id=task, submitting=False)
            write_text(state_file, json.dumps(state))
        try:
            result = self.wait(task, progress=progress)
        except AsrTaskFailed:
            # Do not loop/resubmit automatically. A later explicit Retry may do so.
            state.pop("task_id", None)
            state["submitting"] = False
            write_text(state_file, json.dumps(state))
            raise
        segments = segments_from_result(result, allow_empty=allow_empty)
        report(progress, "asr_done", f"语音识别完成：{len(segments)} 条语句")
        return segments

    def test_connection(self):
        # Official ~4 s spoken sample, not the user's audio. This incurs a small ASR charge.
        task = self.submit(TEST_AUDIO)
        return segments_from_result(self.wait(task, timeout=90, poll_seconds=3))
