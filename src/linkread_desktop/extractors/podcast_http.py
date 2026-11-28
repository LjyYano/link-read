"""Bounded, anonymous reads of public podcast resources and audio.

RSS feeds may point at a hosting provider outside Apple. Validate every redirect
before following it and reject names resolving to local or reserved addresses.
The requests transport resolves names again when connecting: these checks are
not DNS pinning and do not claim protection against DNS rebinding.
"""
from contextlib import contextmanager
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from ..errors import ExtractionError

USER_AGENT = "LinkRead/0.1 (+https://github.com/LjyYano/link-read)"
_REDIRECTS = {301, 302, 303, 307, 308}


class _AnonymousAuth(requests.auth.AuthBase):
    """An explicit auth handler prevents requests from consulting ~/.netrc.

    Clear credential headers inherited from a caller's Session. Proxy credentials
    configured in the proxy URL are handled separately by requests' transport;
    retaining trust_env keeps legitimate system proxy settings working.
    """

    def __call__(self, request):
        request.headers.pop("Authorization", None)
        request.headers.pop("Proxy-Authorization", None)
        return request


_ANONYMOUS_AUTH = _AnonymousAuth()


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (not address.is_global or address.is_private or address.is_loopback
            or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None and not _is_public_ip(address.ipv4_mapped):
            return False
        if address.sixtofour is not None and not _is_public_ip(address.sixtofour):
            return False
        if address.teredo is not None:
            if not _is_public_ip(address.teredo[0]) or not _is_public_ip(address.teredo[1]):
                return False
        # NAT64 well-known (64:ff9b::/96) and local-use (64:ff9b:1::/48) carry embedded IPv4 in last 32 bits.
        if address in ipaddress.IPv6Network("64:ff9b::/96") or address in ipaddress.IPv6Network("64:ff9b:1::/48"):
            embedded = ipaddress.IPv4Address(address.packed[-4:])
            if not _is_public_ip(embedded):
                return False
    return True


def public_url(value: str, *, resolve: bool = True) -> str:
    """Accept only ordinary public HTTP(S) endpoints, without credentials."""
    try:
        if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 33 for c in value):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {"https", "http"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 443 if parsed.scheme == "https" else 80}):
            raise ValueError
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = []
        if resolve and not addresses:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(
                host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM,
            )]
        if (resolve and not addresses) or any(not _is_public_ip(address) for address in addresses):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError, OSError) as exc:
        raise ExtractionError("播客资源地址无效、无法解析或不是公开网络地址") from exc


@contextmanager
def public_response(session, url: str, *, accept: str,
                    timeout: tuple[float, float] = (10, 30), deadline: float | None = None):
    """No implicit redirects: a publisher cannot redirect a read into localhost."""
    response = None
    try:
        for _ in range(6):
            url = public_url(url)
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                raise ExtractionError("播客信息请求超时，请稍后重试")
            request_timeout = tuple(min(value, remaining) for value in timeout) if remaining is not None else timeout
            response = session.get(url, headers={"User-Agent": USER_AGENT, "Accept": accept},
                                   timeout=request_timeout, stream=True, allow_redirects=False,
                                   auth=_ANONYMOUS_AUTH)
            if response.status_code not in _REDIRECTS:
                response.raise_for_status()
                if response.status_code == 206 or response.headers.get("Content-Range"):
                    raise ExtractionError("播客资源只返回了部分内容，已停止读取以避免遗漏")
                yield response
                return
            target = response.headers.get("Location")
            response.close()
            response = None
            if not target:
                raise ExtractionError("播客资源返回了无效的跳转")
            url = urljoin(url, target)
        raise ExtractionError("播客资源跳转次数过多")
    except requests.RequestException as exc:
        # Signed enclosure URLs and provider error pages are not user-facing logs.
        raise ExtractionError("播客资源请求失败，请检查网络或该单集的公开访问权限") from exc
    finally:
        if response is not None:
            response.close()


def chunks(response, *, max_bytes: int, max_seconds: float = 90):
    length = response.headers.get("Content-Length")
    if length:
        try:
            if int(length) > max_bytes:
                raise ExtractionError("播客资源超过下载大小上限")
        except ValueError as exc:
            raise ExtractionError("播客资源大小信息无效") from exc
    started = time.monotonic()
    count = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        count += len(chunk)
        if count > max_bytes:
            raise ExtractionError("播客资源超过下载大小上限")
        if time.monotonic() - started > max_seconds:
            raise ExtractionError("播客资源下载超时，请稍后重试")
        if chunk:
            yield chunk
    if not count:
        raise ExtractionError("播客资源返回空内容，请稍后重试")
    # requests checks incomplete wire bodies itself. Also check identity bodies
    # here, including test/custom transports; compressed length is wire length.
    if length and not response.headers.get("Content-Encoding") and count != int(length):
        raise ExtractionError("播客资源下载不完整，请重试")


def get_bytes(session, url: str, *, max_bytes: int, accept: str,
              timeout: tuple[float, float] = (10, 30), max_seconds: float = 90) -> bytes:
    deadline = time.monotonic() + max_seconds
    with public_response(session, url, accept=accept, timeout=timeout, deadline=deadline) as response:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExtractionError("播客信息请求超时，请稍后重试")
        return b"".join(chunks(response, max_bytes=max_bytes, max_seconds=remaining))
