"""Small, offline SVG icons for the reading interface."""


_PATHS = {
    "summary": '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M8 13v2m8-2v2M1 12v4m22-4v4M9 18h6"/><circle cx="12" cy="3" r="1"/>',
    "mindmap": '<rect x="2" y="9" width="6" height="6" rx="1.5"/><rect x="16" y="2" width="6" height="6" rx="1.5"/><rect x="16" y="16" width="6" height="6" rx="1.5"/><path d="M8 12h4V5h4m-4 7v7h4"/>',
    "transcript": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h3m4 0h3M7 14h10"/>',
    "keywords": '<path d="M9 18h6m-5 3h4M8.1 14.5a6 6 0 1 1 7.8 0c-.8.7-.9 1.3-.9 2.5H9c0-1.2-.1-1.8-.9-2.5Z"/>',
    "highlights": '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/><path d="M8 7h2v3l-2 2m6-5h2v3l-2 2"/>',
    "shownotes": '<rect x="5" y="3" width="16" height="18" rx="2"/><path d="M9 3v18M2 7h4m-4 5h4m-4 5h4m7-10h4m-4 5h4"/>',
    "complete": '<path d="m9 11 3 3L22 4"/><path d="M21 12v.9a9 9 0 1 1-5.3-8.2"/>',
    "star": '<path d="m12 3 2.8 5.7 6.3.9-4.6 4.4 1.1 6.3-5.6-3-5.6 3 1.1-6.3-4.6-4.4 6.3-.9Z"/>',
}


def icon(name: str, *, css: str = "reader-icon") -> str:
    """Only fixed paths enter this markup; labels remain separate text."""
    return (f'<svg class="{css}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            f'width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
            f'{_PATHS[name]}</svg>')
