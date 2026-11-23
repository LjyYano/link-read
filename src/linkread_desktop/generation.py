"""Generate and resume desktop articles without depending on the public CLI."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from os.path import relpath
from pathlib import Path
from urllib.parse import quote

from .ai.checkpoints import RunProvider
from .ai.early_reading import reading_seed, attach_reading
from .ai.models import NoteDocument
from .ai.pipeline import enrich
from .ai.polish import polish_reading
from .ai.provider import StructuredProvider
from .ai.publication import complete_extras
from .asr.config import AsrConfig
from .errors import LinkReadError, error_context
from .models import TranscriptDocument
from .pipeline import extract as extract_document
from .pipeline import video_source_key
from .progress import ProgressCallback, report
from .renderers.catalog import save_catalog
from .renderers.extras import load_extras, save_missing_extras
from .renderers.html import render_html
from .renderers.site import article_metadata, build_site, load_document
from .storage import write_text
from .translation import LANGUAGE_NAMES, PageLanguage, page_locale, prepare_edition
from .workspace import find_workspace, keep_transcript, load_saved_transcript, workspace_dir


def _save_json(document, target: Path) -> None:
    """Write only complete, schema-validated output. Never modify the source."""
    write_text(target, document.model_dump_json(indent=2, exclude_none=True) + "\n")


def _run_input(
    source: str, library: Path, output: Path | None, progress: ProgressCallback | None, auth_options: dict,
) -> tuple[Path, TranscriptDocument, bool]:
    key = video_source_key(source)
    folder = output or (find_workspace(library, key) if key else None)
    if folder and key and (not any(k.startswith("cookies") for k in auth_options) or key.startswith("youtube-")):
        target = folder / "transcript.json"
        if target.exists() or target.is_symlink():
            return folder, load_saved_transcript(target, key), True
    raw = extract_document(source, progress=progress, **auth_options)
    folder = folder or workspace_dir(library, raw)
    document, reused = keep_transcript(folder / "transcript.json", raw)
    return folder, document, reused


def _run_note(folder: Path, raw: TranscriptDocument) -> tuple[Path, NoteDocument | None]:
    note_path = folder / "note.json"
    candidates = []
    for path in (folder / "note-polished.json", note_path):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise LinkReadError(f"笔记路径不可用：{path}")
            note = load_document(path)
            if not isinstance(note, NoteDocument) or note.transcript != raw:
                raise LinkReadError(f"已保存的笔记与原始字幕不匹配：{path}")
            candidates.append((path, note))
    if candidates:
        return max(candidates, key=lambda candidate: len(candidate[1].analysis.reading_paragraphs))
    return note_path, None


def generate_article(
    source: str, agent: StructuredProvider, *, library: Path, output: Path | None = None,
    site: Path | None = None, progress: ProgressCallback | None = None,
    max_chars: int = 4500, timeout: int = 300, jobs: int = 4, polish: bool = True,
    cookies_from_browser: str | None = None, cookies_file: Path | None = None,
    asr: AsrConfig | None = None, asr_cache_dir: Path | None = None, page_language: PageLanguage = "original",
) -> Path:
    """Reuse archived stages and publish the desktop article only after success."""
    started = time.monotonic()
    agent = RunProvider(agent, jobs, progress=progress)
    root = library.expanduser()
    output = output.expanduser() if output is not None else None
    auth_options = {}
    if cookies_from_browser is not None:
        auth_options["cookies_from_browser"] = cookies_from_browser
    if cookies_file is not None:
        auth_options["cookies_file"] = cookies_file
    if asr is not None:
        auth_options["asr"] = asr
        auth_options["asr_cache_dir"] = asr_cache_dir or root / ".asr"
    report(progress, "extract", "正在检查已归档的字幕")
    with error_context("extract"):
        folder, raw, reused = _run_input(source, root, output, progress, auth_options)
    status = "已复用原始字幕" if reused else "已保存原始字幕"
    report(progress, "saved_transcript", f"{status}：{folder / 'transcript.json'}", work_dir=str(folder))

    note_path, saved_note = _run_note(folder, raw)
    cache_dir = folder / ".ai-checkpoints"
    extras = load_extras(note_path)
    language_options = {"language": LANGUAGE_NAMES[page_language]} if page_language != "original" else {}

    def analyze(workers):
        result = enrich(raw, agent, max_chars=max_chars, timeout_seconds=timeout,
                        progress=progress, workers=workers, cache_dir=cache_dir, **language_options)
        _save_json(result, note_path)
        report(progress, "ai_saved", "AI 提炼完成，已保存可复用的笔记")
        return result

    def publish(document):
        result = complete_extras(document, extras, agent, timeout_seconds=timeout, progress=progress,
                                 cache_dir=cache_dir, **language_options)
        # Save this successful independent stage even if proofreading later fails.
        if result is not extras:
            save_missing_extras(note_path, extras, result)
        if result.catalog is not None:
            save_catalog(note_path, result.catalog, document)
        return result

    note = saved_note
    if note is None and polish and jobs >= 3:
        # Punctuation edits depend only on source text, not its AI summary. Start
        # immediately, then split verified text at the real chapter boundaries.
        report(progress, "parallel_start", "提炼与正文校对同时开始，使用统一并发上限")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="linkread-stages") as pool:
            early_reading = pool.submit(polish_reading, reading_seed(raw, agent), agent,
                max_chars=max_chars, timeout_seconds=timeout, progress=progress,
                workers=jobs, cache_dir=cache_dir)
            note = analyze(min(2, jobs))
            publication = pool.submit(publish, note)
            note = attach_reading(note, early_reading.result())
            _save_json(note, note_path)
            completed = publication.result()
    else:
        if note is None:
            note = analyze(jobs)
        else:
            report(progress, "reuse", "已复用完整的 AI 提炼结果")
        needs_polish = polish and not note.analysis.reading_paragraphs
        needs_publication = any(getattr(extras, name) is None for name in ("reading", "mindmap", "quotes", "catalog"))
        if needs_polish and needs_publication and jobs >= 2:
            report(progress, "parallel_start", "并行执行全文校对与阅读信息生成")
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="linkread-publish") as pool:
                publication = pool.submit(publish, note)
                proof = polish_reading(reading_seed(raw, agent) if jobs >= 3 else note, agent,
                    max_chars=max_chars, timeout_seconds=timeout, progress=progress,
                    workers=jobs, cache_dir=cache_dir)
                note = attach_reading(note, proof) if jobs >= 3 else proof
                _save_json(note, note_path)
                completed = publication.result()
        else:
            if needs_polish:
                proof = polish_reading(reading_seed(raw, agent) if jobs >= 3 else note, agent,
                    max_chars=max_chars, timeout_seconds=timeout, progress=progress,
                    workers=jobs, cache_dir=cache_dir)
                note = attach_reading(note, proof) if jobs >= 3 else proof
                _save_json(note, note_path)
            completed = publish(note)
        if not needs_polish:
            report(progress, "polish_reuse" if polish else "polish_skipped",
                   "已复用完整校对正文" if polish else "已跳过全文校对")
    report(progress, "article_ai_done", "AI 整理与阅读信息生成完成")
    completed = load_extras(note_path)
    edition = prepare_edition(folder, raw, page_language, agent, max_chars=max_chars,
                              timeout_seconds=timeout, workers=jobs, progress=progress, cache_dir=cache_dir)

    article = folder / ("article.html" if output is None else "index.html")
    home = site / "index.html" if site is not None else None
    home_href = None
    if home is not None:
        try:
            home_href = quote(Path(relpath(home, folder)).as_posix())
        except ValueError:  # The files can be on different Windows drives.
            home_href = home.resolve().as_uri()
    render_started = time.monotonic()
    report(progress, "render", "正在生成独立 HTML 阅读页")
    metadata = article_metadata(note, note_path, overview=completed.reading)
    write_text(article, render_html(note, extras=completed, home_href=home_href,
                                   category=metadata["category"], description=metadata["description"],
                                   title=metadata["title"], edition=edition))
    metrics = {"wall_seconds": round(time.monotonic() - started, 3),
               "html_seconds": round(time.monotonic() - render_started, 3),
               "model_calls": agent.calls, "cache_hits": agent.cache_hits,
               "model_seconds_sum": round(agent.model_seconds, 3),
               "model_seconds_max": round(agent.longest_model_seconds, 3),
               "queue_seconds_sum": round(agent.queue_seconds, 3),
               "concurrency_limit": jobs, "max_concurrency": agent.peak_concurrency,
               "prompt_chars_sum": agent.prompt_chars, "prompt_chars_max": agent.max_prompt_chars,
               "output_json_chars_sum": agent.output_chars, "output_json_chars_max": agent.max_output_chars}
    write_text(folder / ".performance.json", json.dumps(metrics, ensure_ascii=False, indent=2))
    report(progress, "performance", f"本次用时 {metrics['wall_seconds']:.1f} 秒，模型请求 {agent.calls} 次，复用 {agent.cache_hits} 批；HTML 渲染 {metrics['html_seconds']:.2f} 秒")
    report(progress, "saved_article", f"已生成阅读文件：{article}")
    if site is not None:
        report(progress, "site", "正在更新阅读书架")
        count = build_site(root, site, language=page_locale(raw, page_language))
        report(progress, "saved_site", f"已更新阅读书架：{home}（{count} 篇文章）")
    return article
