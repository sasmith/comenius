"""Render one LessonScript to all outputs in out/<lesson-id>/."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .schema import LessonScript, Timeline
from .sequence import build_timeline
from .speechrate import dialog_rates
from .tts import ClipCache
from .voices import cast_for


@dataclass
class RenderResult:
    lesson_dir: Path
    timeline: Timeline
    files: dict[str, Path] = field(default_factory=dict)
    # voice role -> ("provider:voice", syllables per second of full-speed dialog)
    speech_rates: dict[str, tuple[str, float]] = field(default_factory=dict)


def _mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def transcript(timeline: Timeline, script: LessonScript) -> str:
    names = {"narrator": "Narrator"}
    for sp in script.speakers:
        names.setdefault(sp.voice_role, sp.name)
    out: list[str] = [f"{script.title} ({script.id})", ""]
    chapter = None
    for seg in timeline.segments:
        if seg.chapter != chapter:
            chapter = seg.chapter
            out += ["", f"## {chapter}", ""]
        pause = f"   [pause {seg.pause_after_s:.1f}s]" if seg.pause_after_s >= 1.0 else ""
        out.append(f"[{_mmss(seg.start_s)}] {names.get(seg.voice, seg.voice)}: {seg.text}{pause}")
    out += ["", f"Total: {_mmss(timeline.duration_s)}"]
    return "\n".join(out) + "\n"


def render_lesson(
    script: LessonScript,
    cfg: Config,
    *,
    provider: str | None = None,
    mp3: bool = True,
    pdf: bool = True,
    anki: bool = True,
    out_dir: Path | None = None,
) -> RenderResult:
    lesson_dir = (out_dir or cfg.out_dir) / script.id
    lesson_dir.mkdir(parents=True, exist_ok=True)

    cast = cast_for(script, cfg.tts, provider)
    cache = ClipCache(cfg.tts, cast)

    # Durations come from real (cached) TTS clips, so pauses fit the actual audio.
    timeline = build_timeline(script, cfg.lesson, cache.duration)
    rates = dialog_rates(timeline, script.lang)
    result = RenderResult(
        lesson_dir=lesson_dir,
        timeline=timeline,
        speech_rates={role: (str(cast[role]), r) for role, r in rates.items()},
    )

    def write(name: str, text: str) -> None:
        path = lesson_dir / name
        path.write_text(text, encoding="utf-8")
        result.files[name] = path

    write("script.json", script.model_dump_json(indent=2))
    write("timeline.json", timeline.model_dump_json(indent=2))
    write("transcript.txt", transcript(timeline, script))

    if mp3:
        from .audio import render_mp3

        path = lesson_dir / "lesson.mp3"
        render_mp3(timeline, cache, path, title=f"{script.id}: {script.title}", artist="Comenius")
        result.files["lesson.mp3"] = path
    if pdf:
        from .pdf import render_pdf

        result.files["lesson.pdf"] = render_pdf(script, lesson_dir / "lesson.pdf")
    if anki:
        from .anki import render_anki

        result.files["lesson.apkg"] = render_anki(
            script, cache, lesson_dir / "lesson.apkg", lesson_dir / "media"
        )

    meta = {
        "id": script.id,
        "title": script.title,
        "setting": script.setting,
        "lang": script.lang,
        "dialect": script.dialect,
        "duration_s": timeline.duration_s,
        "learner_pause_s": round(timeline.learner_pause_s(), 1),
        "chapters": [c.model_dump() for c in timeline.chapters],
        "voices": {role: str(ref) for role, ref in cast.items()},
        "syllables_per_s": rates,
        "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": sorted(result.files),
    }
    write("meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
    return result
