"""Localize process diagnostics at the sidecar boundary, leaving source data intact."""

import os
import re


_PROGRESS = {
    "source": "Checking the source", "redirect": "Resolving the source link",
    "authentication": "Using the explicitly selected YouTube cookies",
    "extract": "Checking saved subtitles", "metadata": "Fetching source metadata",
    "metadata_done": "Source metadata received", "captions": "Checking available subtitles",
    "captions_done": "Subtitle track selected", "download": "Downloading subtitles",
    "reading": "Reading the local subtitle file", "parse": "Parsing subtitle text and timestamps",
    "parsed": "Parsed the subtitles", "saved_transcript": "Original transcript saved",
    "audio_download": "Downloading audio for speech recognition", "audio_download_done": "Audio downloaded",
    "audio_split": "Splitting the audio locally", "audio_split_done": "Audio split into chunks",
    "asr_start": "Starting speech recognition", "asr_upload": "Uploading audio for transcription",
    "asr_upload_done": "Audio upload complete", "asr_submit": "Submitting a transcription job",
    "asr_wait": "Waiting for transcription", "asr_result": "Reading the transcription result",
    "asr_chunks_start": "Transcribing audio chunks", "asr_chunk_progress": "Transcription chunk progressing",
    "asr_chunk_done": "Transcription chunk complete", "asr_chunk_reuse": "Reused a transcribed chunk",
    "asr_resume": "Resuming a saved transcription job", "asr_reuse": "Reused the saved transcription",
    "asr_done": "Speech recognition complete", "provider_switch": "The model or page language changed. Starting a separate version",
    "ai_start": "Starting AI analysis", "ai_chunk": "Scheduled an analysis batch", "ai_done": "Analysis batch complete",
    "ai_saved": "AI analysis saved", "reuse": "Reused saved analysis",
    "parallel_start": "Running independent AI stages in parallel", "polish_start": "Starting transcript proofreading",
    "polish_batch": "Proofreading a transcript batch", "polish_verify": "Checking the original wording",
    "polish_retry": "Retrying transcript proofreading", "polish_warning": "Preserved the original wording after a failed check",
    "polish_done": "Proofreading batch complete", "polish_complete": "Proofreading complete",
    "polish_reuse": "Reused the proofread transcript", "polish_skipped": "Skipped proofreading",
    "article_ai": "Preparing the summary, mindmap and reading details", "article_ai_done": "AI processing complete",
    "publication_done": "Reading details complete", "quote_warning": "Preserved exact source wording in excerpts",
    "translation_start": "Translating the complete transcript", "translation_done": "Translation batch complete",
    "replace": "Generating a separate version. The existing article remains available",
    "replace_publish": "Installing the completed version", "render": "Rendering the offline HTML article",
    "saved_article": "Reading page saved", "site": "Updating the offline library", "saved_site": "Offline library saved",
    "performance": "Generation timing recorded",
}
_REQUESTS = {
    "queued": "Waiting for an available model slot", "running": "Model request running",
    "received": "Model response received", "validating": "Validating the model response",
    "complete": "Model response validated", "cache": "Reused a validated model response", "failed": "Model request failed",
}
_ERRORS = {
    "unsupported_source": "Use a supported video, podcast episode, or subtitle file.",
    "source_missing": "Subtitle file not found. Select it again.",
    "subtitles_unavailable": "No usable subtitles are available for this source. Import subtitles or enable speech recognition in Settings.",
    "subtitle_login_required": "Bilibili requires sign-in to view subtitles. Open the original video or import a subtitle file.",
    "subtitle_response_invalid": "Bilibili returned an incomplete subtitle list. Check the source again.",
    "invalid_pasted_subtitle": "Could not read the pasted subtitles. Check their format and timestamps.",
    "source_permission_denied": "Access to this source was denied. Check the source permissions.",
    "source_timeout": "The subtitle service timed out. Try again.",
    "source_rate_limited": "The source service is rate-limiting requests. Try again later.",
    "network_error": "A network request failed. Check your connection and try again.",
    "service_unavailable": "The remote service is unavailable. Try again later.",
    "extraction_failed": "Could not extract subtitles from this source. Check the source or try again.",
    "protected_media": "This audio is protected and cannot be transcribed.",
    "unsupported_audio_stream": "This audio stream format is unsupported.",
    "audio_manifest_invalid": "Could not verify the source audio manifest.",
    "authentication_missing": "The service needs credentials. Check Settings and try again.",
    "authentication_failed": "The service rejected the credentials. Check the key or sign-in in Settings.",
    "permission_denied": "The provider denied access. Check the key and model permissions.",
    "configuration_invalid": "The service settings are invalid. Check the URL, model, and credentials.",
    "dependency_missing": "A required tool is unavailable. Check its installation and path in Settings.",
    "model_output_invalid": "The model response failed validation. Retry to resume from verified batches.",
    "model_timeout": "The model request timed out. Retry to resume from verified batches.",
    "model_failed": "The model could not process this request. Review the provider settings or try again.",
    "provider_exit": "The AI command exited unsuccessfully. Check the provider configuration.",
    "provider_start_failed": "Could not start the AI command. Check its installation and path.",
    "rate_or_quota_limited": "The provider's rate or usage limit was reached. Check quota or try again later.",
    "cache_invalid": "Saved data could not be verified. Check the existing archive before retrying.",
    "storage_error": "Could not read or write the requested file. Check the path and permissions.",
    "storage_full": "There is not enough free storage. Free some space and retry.",
    "invalid_data": "The saved or supplied data is invalid. Check the file and try again.",
    "preview_mismatch": "The saved preview belongs to a different source. Select the original source again.",
    "preview_unavailable": "The original transcript preview is unavailable.",
    "asr_submission_unknown": "The speech service did not confirm submission. Check the current job before trying again.",
    "asr_wait_timeout": "Speech recognition is still pending. Retry to resume polling the existing job.",
    "asr_failed": "Speech recognition failed. Check your configuration and try again.",
    "asr_task_failed": "The speech service reported a failed transcription job.",
}


def english_mode() -> bool:
    return os.environ.get("LINKREAD_UI_LANGUAGE", "").lower().startswith("en")


def localize_event(kind: str, fields: dict) -> dict:
    if not english_mode() or not isinstance(fields.get("message"), str):
        return fields
    original = fields["message"]
    if kind == "progress":
        request = fields.get("request") or {}
        stage = fields.get("stage", "")
        message = _REQUESTS.get(request.get("phase")) if request else _PROGRESS.get(stage)
        if stage == "performance":
            values = re.findall(r"\d+(?:\.\d+)?", original)
            if len(values) == 4:
                message = f"Elapsed: {values[0]} s · Model requests: {values[1]} · Cached batches: {values[2]} · HTML: {values[3]} s"
        if stage == "saved_transcript" and original.startswith("已复用"):
            message = "Reused the original transcript"
        if message and fields.get("total") is not None and fields.get("completed") is not None:
            message += f" ({fields['completed']}/{fields['total']})"
        return {**fields, "message": message or "Processing the article"}
    if kind == "error":
        message = _ERRORS.get(fields.get("code"))
        # Unknown upstream diagnostics may contain private URLs or credentials.
        return {**fields, "message": message or "The operation could not be completed. Check the input and try again."}
    if kind == "connection":
        return {**fields, "message": "Connected"}
    if kind == "source_check":
        code = fields.get("code")
        if code == "subtitle_login_required":
            return {**fields, "message": "Bilibili requires sign-in to view subtitles. Generation can transcribe publicly accessible audio."}
        if code == "subtitle_response_invalid":
            return {**fields, "message": _ERRORS[code]}
        message = {
            "captions": "Public subtitles are available for this source.",
            "needs_asr": "No public subtitles were found. Speech recognition is available if enabled.",
            "blocked": "This source cannot be processed. View the source or import a subtitle file.",
            "unknown": "Source availability could not be confirmed. Check again or view the source.",
        }.get(fields.get("status"))
        if message:
            return {**fields, "message": message}
    return fields
