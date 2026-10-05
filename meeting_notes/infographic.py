"""Render the meeting infographic as HTML and as a 1600 px wide PNG."""

from __future__ import annotations

import html
from importlib import resources
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote
import subprocess
from typing import Any


TONES = ["blue", "red", "teal", "gold", "royal", "brown"]
_WIDTH = 1600
_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "header_meta": {"type": "string"},
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "tone": {"type": "string", "enum": TONES},
                    "columns": {"type": "integer", "enum": [2, 3]},
                    "cards": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "badge": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                                "callout": {"type": "string"},
                                "source": {"type": "string"},
                            },
                            "required": ["title", "badge", "bullets", "callout", "source"],
                        },
                    },
                },
                "required": ["heading", "tone", "columns", "cards"],
            },
        },
        "closing_summary": {"type": "string"},
        "footer_note": {"type": "string"},
        "notes_link_label": {"type": "string"},
        "transcript_link_label": {"type": "string"},
    },
    "required": [
        "title", "subtitle", "header_meta", "sections", "closing_summary",
        "footer_note", "notes_link_label", "transcript_link_label",
    ],
}


def _inline(text: Any) -> str:
    """Escape text and keep only **bold** as <strong>."""
    escaped = html.escape(str(text), quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def render_html(poster: dict[str, Any], *, language: str, notes_name: str) -> str:
    css = resources.files("meeting_notes").joinpath("infographic.css").read_text(encoding="utf-8")
    lang = "zh-Hans" if language == "zh" else language
    lines = [
        "<!doctype html>",
        f'<html lang="{html.escape(lang)}">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(str(poster['title']), quote=False)}</title>",
        "<style>",
        css + "</style>",
        "</head>",
        "<body>",
        '<main class="poster">',
        '  <header class="poster-header">',
        f"    <h1>{_inline(poster['title'])}</h1>",
        f'    <p class="subtitle">{_inline(poster["subtitle"])}</p>',
        f'    <p class="header-meta">{_inline(poster["header_meta"])}</p>',
        "  </header>",
    ]
    for number, section in enumerate(poster["sections"], start=1):
        tone = section["tone"] if section["tone"] in TONES else "blue"
        columns = 3 if section["columns"] == 3 else 2
        lines += [
            "",
            f'  <section class="poster-section tone-{tone}">',
            f'    <h2 class="section-heading"><span class="section-number">{number:02d}</span>'
            f'<span class="section-slash">/</span>{_inline(section["heading"])}</h2>',
            f'    <div class="section-panel"><div class="card-grid columns-{columns}">',
        ]
        for card in section["cards"]:
            lines += [
                '      <article class="info-card">',
                '        <div class="card-heading"><span class="card-mark" aria-hidden="true"></span>'
                f'<h3>{_inline(card["title"])}</h3><span class="status-badge">{_inline(card["badge"])}</span></div>',
                "        <ul>",
                *(f"          <li>{_inline(bullet)}</li>" for bullet in card["bullets"]),
                "        </ul>",
                f'        <div class="card-bottom"><p class="card-callout">{_inline(card["callout"])}</p>'
                f'<p class="source-ref">{_inline(card["source"])}</p></div>',
                "      </article>",
            ]
        lines += ["    </div></div>", "  </section>"]
    lines += [
        "",
        f'  <div class="closing-summary"><p>{_inline(poster["closing_summary"])}</p></div>',
        '  <footer class="poster-footer">',
        f"    <p>{_inline(poster['footer_note'])}</p>",
        f'    <p><a href="{html.escape("./" + quote(notes_name))}">{_inline(poster["notes_link_label"])}</a> · '
        f'<a href="transcript.md">{_inline(poster["transcript_link_label"])}</a></p>',
        "  </footer>",
        "</main>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(lines)


def find_chrome() -> str:
    """Return a Chromium-based browser for the PNG, or raise before any work starts."""
    configured = os.environ.get("MEETING_NOTES_CHROME")
    if configured:
        if not Path(configured).is_file():
            raise RuntimeError(f"MEETING_NOTES_CHROME does not point to a file: {configured}")
        return configured
    for candidate in _CHROME_PATHS:
        if Path(candidate).is_file():
            return candidate
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError(
        "Google Chrome or another Chromium browser is required to render meeting-infographic.png. "
        "Install Chrome or set MEETING_NOTES_CHROME to its executable."
    )


def _chrome(chrome: str, *arguments: str) -> str:
    command = [
        chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--force-device-scale-factor=1", *arguments,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Chrome timed out while rendering the infographic") from error
    if result.returncode:
        raise RuntimeError(f"Chrome failed with exit code {result.returncode}: {result.stderr.strip()}")
    return result.stdout


def render_png(chrome: str, html_path: Path, png_path: Path) -> None:
    """Screenshot the full page at 1600 px wide."""
    # Measure the page height at the same width with a copy that reports its own height.
    probe = html_path.with_name(f".{html_path.stem}-probe.html")
    script = "<script>document.body.dataset.pageHeight=document.documentElement.scrollHeight</script>"
    probe.write_text(html_path.read_text(encoding="utf-8").replace("</body>", f"{script}</body>"), encoding="utf-8")
    try:
        dom = _chrome(chrome, f"--window-size={_WIDTH},1000", "--dump-dom", probe.as_uri())
    finally:
        probe.unlink(missing_ok=True)
    match = re.search(r'data-page-height="(\d+)"', dom)
    if not match:
        raise RuntimeError("Chrome did not report the infographic page height")
    height = int(match.group(1))
    _chrome(chrome, f"--window-size={_WIDTH},{height}", f"--screenshot={png_path}", html_path.as_uri())
    if not png_path.is_file():
        raise RuntimeError("Chrome did not write the infographic PNG")
