"""Assemble a Timeline into one MP3 with chapter markers."""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

from .schema import Timeline
from .tts import SAMPLE_RATE, ClipCache, TTSError, ffmpeg_exe

BYTES_PER_SAMPLE = 2


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * max(0, round(seconds * SAMPLE_RATE))


def _ffmeta_escape(text: str) -> str:
    for ch in ("\\", "=", ";", "#", "\n"):
        text = text.replace(ch, "\\" + ch)
    return text


def write_wav(timeline: Timeline, cache: ClipCache, out_wav: Path) -> float:
    """Place every segment at its start time; return total duration in seconds."""
    written = 0  # samples
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(BYTES_PER_SAMPLE)
        out.setframerate(SAMPLE_RATE)
        for seg in timeline.segments:
            target = round(seg.start_s * SAMPLE_RATE)
            if target > written:
                out.writeframes(_silence((target - written) / SAMPLE_RATE))
                written = target
            clip = cache.clip(seg.voice, seg.lang, seg.text, seg.rate, seg.terms, seg.term_lang)
            with wave.open(str(clip), "rb") as w:
                if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1:
                    raise TTSError(f"Clip {clip} is not normalized")
                frames = w.readframes(w.getnframes())
            out.writeframes(frames)
            written += len(frames) // BYTES_PER_SAMPLE
        end = round(timeline.duration_s * SAMPLE_RATE)
        if end > written:
            out.writeframes(_silence((end - written) / SAMPLE_RATE))
            written = end
    return written / SAMPLE_RATE


def chapter_metadata(timeline: Timeline, total_s: float, title: str, artist: str) -> str:
    lines = [
        ";FFMETADATA1",
        f"title={_ffmeta_escape(title)}",
        f"artist={_ffmeta_escape(artist)}",
        f"album={_ffmeta_escape(artist)}",
    ]
    chapters = timeline.chapters
    for i, ch in enumerate(chapters):
        start = round(ch.start_s * 1000)
        end = round((chapters[i + 1].start_s if i + 1 < len(chapters) else total_s) * 1000)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={max(end, start + 1)}",
            f"title={_ffmeta_escape(ch.title)}",
        ]
    return "\n".join(lines) + "\n"


def render_mp3(
    timeline: Timeline,
    cache: ClipCache,
    out_mp3: Path,
    *,
    title: str,
    artist: str = "Comenius",
    bitrate: str = "64k",
) -> float:
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "lesson.wav"
        meta = Path(tmp) / "meta.txt"
        total = write_wav(timeline, cache, wav)
        meta.write_text(chapter_metadata(timeline, total, title, artist), encoding="utf-8")
        result = subprocess.run(
            [
                ffmpeg_exe(), "-y", "-loglevel", "error",
                "-i", str(wav), "-i", str(meta),
                "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "24000", "-ac", "1",
                "-c:a", "libmp3lame", "-b:a", bitrate,
                "-id3v2_version", "3",
                str(out_mp3),
            ],
            capture_output=True,
            check=False,
        )  # fmt: skip
        if result.returncode != 0:
            raise TTSError(f"ffmpeg encode failed: {result.stderr.decode(errors='replace')}")
    return total


def clip_to_mp3(clip: Path, out: Path, bitrate: str = "48k") -> None:
    """Small MP3 of one cached clip (for Anki media)."""
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(clip),
         "-c:a", "libmp3lame", "-b:a", bitrate, str(out)],
        check=True,
    )  # fmt: skip
