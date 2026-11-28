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
    "audio_retry": "The signed-in session offered no audio. Retrying without YouTube cookies",
    "audio_split": "Splitting the audio locally", "audio_split_done": "Audio split into chunks",
    "asr_start": "Starting speech recognition", "asr_upload": "Uploading audio for transcription",
    "asr_upload_done": "Audio upload complete", "asr_submit": "Submitting a transcription job",
    "asr_wait": "Waiting for transcription", "asr_result": "Reading the transcription result",
    "asr_chunks_start": "Transcribing audio chunks", "asr_chunk_progress": "Transcription chunk progressing",
    "asr_chunk_done": "Transcription chunk complete", "asr_chunk_reuse": "Reused a transcribed chunk",
    "asr_resume": "Resuming a saved transcription job", "asr_reuse": "Reused the saved transcription",
    "asr_done": "Speech recognition complete", "provider_switch": "The model or page language changed. Starting a separate version",
    "ai_start": "Starting AI analysis", "ai_chunk": "Scheduled an analysis batch", "ai_retry": "Retrying an analysis batch after validation check", "ai_done": "Analysis batch complete",
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
    "shownotes_source_missing": "This article has no source from which to fetch Shownotes.",
    "shownotes_source_mismatch": "The description did not match this article's source. Existing Shownotes were kept.",
    "shownotes_too_large": "The source description is too large to save. Existing Shownotes were kept.",
    "linked_source_unavailable": "Could not load the title or cover for this link. Your article details were kept.",
    "linked_source_changed": "This article changed. Reopen Edit details and save again.",
    "subtitles_unavailable": "No usable subtitles are available for this source. Import subtitles or enable speech recognition in Settings.",
    "subtitle_login_required": "Bilibili requires sign-in to view subtitles. Open the original video or import a subtitle file.",
    "subtitle_response_invalid": "Bilibili returned an incomplete subtitle list. Check the source again.",
    "youtube_verification_required": "YouTube requires sign-in verification before this video can be accessed. Configure YouTube cookies in Settings, open the original video, or import a subtitle file.",
    "youtube_cookies_safari_permission": "macOS blocked access to Safari's cookies database. Grant Full Disk Access to LinkRead (or the terminal running dev.sh) in System Settings, or switch to Chrome or a cookies.txt file in Settings.",
    "youtube_cookies_permission_denied": "macOS denied permission to read the browser's cookie folder. Allow access to other apps' data or grant Full Disk Access to LinkRead (or the terminal running dev.sh, then restart it) in System Settings, or switch browsers or use cookies.txt in Settings.",
    "youtube_cookies_keychain_denied": "macOS Keychain denied access to decrypt the browser's cookies. Retry and enter your Mac password, then choose Allow or Always Allow in the Keychain prompt, or switch browsers or use cookies.txt in Settings.",
    "youtube_cookies_not_found": "Could not find the selected browser's cookies database on this Mac. Confirm the browser is installed and signed in, or pick another browser or cookies.txt in Settings.",
    "youtube_cookies_format_invalid": "The selected file is not a valid Netscape-format cookies.txt file. Export it again and reselect it in Settings.",
    "youtube_cookies_empty": "Cookies were read, but no active youtube.com sign-in session was found. Sign in to YouTube in that browser (and restart it to flush cookies to disk) or export a fresh cookies.txt.",
    "youtube_cookies_expired": "YouTube rejected the configured cookies (the browser session may be stale or rotated, or the current proxy/network triggered bot verification). Restart the browser and confirm you are signed in to YouTube, export a fresh cookies.txt from a private window, or try another proxy/network.",
    "youtube_cookies_unavailable": "Could not read the configured YouTube cookies (the cookie database may be locked or restricted). Try again, switch browsers, or use cookies.txt in Settings.",
    "youtube_subtitles_unconfirmed": "YouTube's subtitle list was incomplete or included unreadable tracks. Check the source again.",
    "youtube_subtitle_invalid": "YouTube listed a subtitle track, but the downloaded captions were empty or invalid. Check the source again.",
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
    "audio_unavailable": "The source did not offer a downloadable audio track for this session, so nothing was downloaded or submitted. Try again later, check your sign-in status or network, or import a subtitle file.",
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
        if request:
            phase = request.get("phase")
            if phase == "failed" and original == "模型结果未通过原文与结构校验":
                message = "Response did not match original wording or schema"
            else:
                message = _REQUESTS.get(phase)
        else:
            message = _PROGRESS.get(stage)
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
        code = fields.get("code")
        message = _ERRORS.get(code)
        if fields.get("stage") == "shownotes":
            if code in {"extraction_failed", "operation_failed"}:
                message = "Could not read the description from the original source. Try again later."
            elif code == "source_timeout":
                message = "The source timed out while loading Shownotes. Try again."
        # Unknown upstream diagnostics may contain private URLs or credentials.
        return {**fields, "message": message or "The operation could not be completed. Check the input and try again."}
    if kind == "connection":
        return {**fields, "message": "Connected"}
    if kind == "youtube_cookies_test":
        return {**fields, "message": "YouTube cookies were read locally. Whether YouTube accepts them is confirmed only during generation."}
    if kind == "source_check":
        code = fields.get("code")
        if code == "subtitle_login_required":
            return {**fields, "message": "Bilibili requires sign-in to view subtitles. Generation can transcribe publicly accessible audio."}
        if code in {
            "subtitle_response_invalid", "youtube_verification_required",
            "youtube_cookies_safari_permission", "youtube_cookies_permission_denied",
            "youtube_cookies_keychain_denied",
            "youtube_cookies_not_found", "youtube_cookies_format_invalid",
            "youtube_cookies_empty", "youtube_cookies_expired", "youtube_cookies_unavailable",
            "youtube_subtitles_unconfirmed", "youtube_subtitle_invalid",
        }:
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
