"""Build a static podcast site (RSS + MP3 + PDF) from rendered lessons.

Upload the resulting directory to any static host under a hard-to-guess path
(e.g. `aws s3 sync site/ s3://bucket/<random>/` or `rclone sync`), then
subscribe to <public url>/feed.xml in a podcast app.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .config import FeedConfig


def _hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _lessons(out_dir: Path) -> list[tuple[dict, Path]]:
    found = []
    for meta_path in sorted(out_dir.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (meta_path.parent / "lesson.mp3").exists():
            found.append((meta, meta_path.parent))
    return found


def build_feed(out_dir: Path, site_dir: Path, cfg: FeedConfig, base: str) -> Path:
    """`base` is the public URL the site is served from (see FeedConfig.public_url)."""
    site_dir.mkdir(parents=True, exist_ok=True)
    items: list[str] = []
    for meta, lesson_dir in _lessons(out_dir):
        lid = meta["id"]
        mp3 = site_dir / f"{lid}.mp3"
        shutil.copy2(lesson_dir / "lesson.mp3", mp3)
        links = []
        for ext in ("pdf", "apkg"):
            src = lesson_dir / f"lesson.{ext}"
            if src.exists():
                shutil.copy2(src, site_dir / f"{lid}.{ext}")
                label = "PDF companion" if ext == "pdf" else "Anki deck"
                links.append(f'<a href="{base}/{lid}.{ext}">{label}</a>')
        transcript = lesson_dir / "transcript.txt"
        if transcript.exists():
            shutil.copy2(transcript, site_dir / f"{lid}.txt")
        chapters = "".join(
            f"<li>{_hms(c['start_s'])} {escape(c['title'])}</li>" for c in meta.get("chapters", [])
        )
        desc = f"<p>{escape(meta.get('setting', ''))}</p><p>{' · '.join(links)}</p>"
        if chapters:
            desc += f"<ul>{chapters}</ul>"
        pub = datetime.fromisoformat(meta.get("rendered_at", datetime.now(UTC).isoformat()))
        items.append(
            "    <item>\n"
            f"      <title>{escape(lid)}: {escape(meta['title'])}</title>\n"
            f'      <guid isPermaLink="false">comenius-{escape(lid)}</guid>\n'
            f"      <pubDate>{format_datetime(pub)}</pubDate>\n"
            f"      <description><![CDATA[{desc}]]></description>\n"
            f'      <enclosure url="{base}/{lid}.mp3" length="{mp3.stat().st_size}" '
            'type="audio/mpeg"/>\n'
            f"      <itunes:duration>{_hms(meta['duration_s'])}</itunes:duration>\n"
            f"      <itunes:episode>{len(items) + 1}</itunes:episode>\n"
            "    </item>"
        )
    now = format_datetime(datetime.now(UTC))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">\n'
        "  <channel>\n"
        f"    <title>{escape(cfg.title)}</title>\n"
        f"    <link>{escape(base)}/</link>\n"
        f"    <description>{escape(cfg.description)}</description>\n"
        "    <language>en</language>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        f"    <itunes:author>{escape(cfg.author)}</itunes:author>\n"
        "    <itunes:block>Yes</itunes:block>\n"
        "    <itunes:type>serial</itunes:type>\n" + "\n".join(items) + "\n  </channel>\n</rss>\n"
    )
    feed = site_dir / "feed.xml"
    feed.write_text(xml, encoding="utf-8")
    return feed
