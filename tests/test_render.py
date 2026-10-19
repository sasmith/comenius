"""End-to-end rendering with the offline espeak provider."""

import json
import shutil
import subprocess
import zipfile

import pytest

from comenius.config import Config, TTSConfig
from comenius.feed import build_feed
from comenius.render import render_lesson

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("espeak-ng")),
    reason="needs ffmpeg and espeak-ng",
)


def test_render_all_outputs(script, tmp_path):
    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    result = render_lesson(script, cfg, provider="espeak")

    mp3 = result.files["lesson.mp3"]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-show_format", "-of", "json", str(mp3)],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(probe.stdout)
    assert len(info["chapters"]) == len(result.timeline.chapters) == 9
    assert abs(float(info["format"]["duration"]) - result.timeline.duration_s) < 1.0

    assert result.files["lesson.pdf"].read_bytes().startswith(b"%PDF")
    with zipfile.ZipFile(result.files["lesson.apkg"]) as z:
        names = z.namelist()
    assert "media" in names and any(n.startswith("collection") for n in names)

    feed = build_feed(cfg.out_dir, tmp_path / "site", cfg.feed)
    xml = feed.read_text()
    assert "es-0001.mp3" in xml and "<itunes:duration>" in xml
    assert (tmp_path / "site" / "es-0001.mp3").exists()


def test_second_render_uses_cache(script, tmp_path):
    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    render_lesson(script, cfg, provider="espeak", mp3=False, pdf=False, anki=False)
    clips = len(list((tmp_path / "cache" / "espeak").glob("*.wav")))
    render_lesson(script, cfg, provider="espeak", mp3=False, pdf=False, anki=False)
    assert len(list((tmp_path / "cache" / "espeak").glob("*.wav"))) == clips
