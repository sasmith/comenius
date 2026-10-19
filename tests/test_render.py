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
    rates = json.loads(result.files["meta.json"].read_text())["syllables_per_s"]
    assert set(rates) == {sp.voice_role for sp in script.speakers}
    assert all(2 < r < 15 for r in rates.values())

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

    feed = build_feed(cfg.out_dir, tmp_path / "site", cfg.feed, "https://cdn.example/x")
    xml = feed.read_text()
    assert "es-0001.mp3" in xml and "<itunes:duration>" in xml
    assert (tmp_path / "site" / "es-0001.mp3").exists()


def test_second_render_uses_cache(script, tmp_path):
    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    render_lesson(script, cfg, provider="espeak", mp3=False, pdf=False, anki=False)
    clips = len(list((tmp_path / "cache" / "espeak").glob("*.wav")))
    render_lesson(script, cfg, provider="espeak", mp3=False, pdf=False, anki=False)
    assert len(list((tmp_path / "cache" / "espeak").glob("*.wav"))) == clips


def test_spanish_syllables():
    from comenius.speechrate import spanish_syllables

    cases = {
        "Buenos días": 4,  # bue-nos dí-as
        "¿Qué quieres?": 3,  # qué quie-res
        "Hoy muy bien": 3,
        "El país y la guía": 7,  # el pa-ís y la guí-a
        "pingüino": 3,
        "ahora leer": 5,  # a-ho-ra le-er
    }
    assert {text: spanish_syllables(text) for text in cases} == cases
