from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

from ..errors import LinkReadError


class AsrError(LinkReadError):
    default_code = "asr_failed"


def api_root(value: str) -> str:
    value = value.strip().rstrip("/")
    p = urlsplit(value)
    host = p.hostname or ""
    if (p.scheme != "https" or p.username or p.password or p.port or p.query or p.fragment
            or p.path != "/api/v1"
            or not (host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                    or host.endswith(".cn-beijing.maas.aliyuncs.com"))):
        raise AsrError("ASR 地址需为百炼原生 /api/v1 地址，不能使用聊天兼容接口", code="configuration_invalid", stage="setup", action="settings")
    return value


@dataclass(frozen=True)
class AsrConfig:
    base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    model: str = "paraformer-v2"
    max_minutes: int = 120
    chunk_minutes: int = 5
    token: str | None = field(default=None, repr=False)
    ffmpeg_path: str = ""

    def credential(self) -> str:
        api_root(self.base_url)
        if self.model != "paraformer-v2":
            raise AsrError("当前文件转写适配器支持 paraformer-v2", code="configuration_invalid", stage="setup", action="settings")
        if not 1 <= self.max_minutes <= 720:
            raise AsrError("单次转写时长上限应在 1–720 分钟之间", code="configuration_invalid", stage="setup", action="settings")
        if type(self.chunk_minutes) is not int or self.chunk_minutes not in {0, 5, 10, 15}:
            raise AsrError("转写分片需为 5、10、15 分钟或 0（整段）", code="configuration_invalid", stage="setup", action="settings")
        value = self.token if self.token is not None else os.environ.get("LINKREAD_ASR_TOKEN", "")
        if not value or len(value) > 8192 or any(ord(c) < 32 for c in value):
            raise AsrError("没有可用字幕，请先在设置中配置语音识别 Token", code="authentication_missing", stage="setup", action="settings")
        return value
