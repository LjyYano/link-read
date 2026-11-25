from contextlib import contextmanager
from functools import wraps


class LinkReadError(Exception):
    """A user-facing error with explicit, transport-safe recovery metadata."""

    default_code = "operation_failed"
    default_action = "details"
    default_retryable = False

    def __init__(self, message: str, *, code: str | None = None,
                 stage: str | None = None, retryable: bool | None = None,
                 action: str | None = None):
        super().__init__(message)
        self.code = code or self.default_code
        self.stage = stage
        self.retryable = self.default_retryable if retryable is None else retryable
        self.action = action or self.default_action


@contextmanager
def error_context(stage: str | None):
    """Attribute only the operation actually executing, never a last UI event."""
    try:
        yield
    except LinkReadError as exc:
        if exc.stage is None:
            exc.stage = stage
        raise


def error_operation(stage: str):
    def decorate(function):
        @wraps(function)
        def operation(*args, **kwargs):
            with error_context(stage):
                return function(*args, **kwargs)
        return operation
    return decorate


def error_details(error: Exception) -> dict:
    if isinstance(error, LinkReadError):
        result = {"message": str(error), "code": error.code, "stage": error.stage,
                  "retryable": error.retryable, "action": error.action}
        if error.code in {"operation_failed", "extraction_failed"}:
            # Extractors deliberately replace HTTP exception text to avoid
            # leaking signed URLs. Typed causes still allow safe recovery hints.
            import requests
            cause = error.__cause__
            while cause is not None:
                if isinstance(cause, requests.Timeout):
                    result.update(code="source_timeout", retryable=True, action="retry")
                    break
                if isinstance(cause, requests.ConnectionError):
                    result.update(code="network_error", retryable=True, action="retry")
                    break
                if isinstance(cause, requests.HTTPError) and cause.response is not None:
                    status = cause.response.status_code
                    if status >= 500:
                        result.update(code="service_unavailable", retryable=True, action="retry")
                    elif status in {401, 403}:
                        result.update(code="source_permission_denied", retryable=False, action="source")
                    elif status == 429:
                        result.update(code="source_rate_limited", retryable=True, action="retry")
                    break
                cause = cause.__cause__
        return result
    if isinstance(error, OSError):
        return {"message": str(error), "code": "storage_error", "stage": None,
                "retryable": False, "action": "details"}
    return {"message": str(error), "code": "invalid_data", "stage": None,
            "retryable": False, "action": "details"}


class UnsupportedSourceError(LinkReadError):
    """The supplied URL or file format is not supported."""
    default_code = "unsupported_source"
    default_action = "source"


class SubtitleUnavailableError(LinkReadError):
    """No usable subtitles could be retrieved."""

    def __init__(self, message: str, *, source=None):
        super().__init__(message, code="subtitles_unavailable", stage="extract", action="source")
        # Preserve already verified publisher metadata across opt-in ASR. This
        # carries no permission to transcribe; the dispatcher still owns that.
        self.source = source


class ExtractionError(LinkReadError):
    """A supported source could not be extracted."""
    default_code = "extraction_failed"
    default_action = "source"


class SubtitleLoginRequiredError(ExtractionError):
    """Subtitle lookup reached the player, which requires sign-in."""

    def __init__(self, message: str, *, source):
        super().__init__(message, code="subtitle_login_required")
        self.source = source
