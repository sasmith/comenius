import io
import json
import shutil

import pytest

from comenius.config import Config, PoolVoice, TTSConfig
from comenius.render import render_lesson
from comenius.tts import PollyProvider, TTSError
from comenius.voices import cast_for

BOTH = {"polly", "azure"}


def test_pool_cast_is_stable_distinct_and_gendered(script):
    cfg = TTSConfig(provider="pool")
    cast = cast_for(script, cfg, available=BOTH)
    assert cast == cast_for(script, cfg, available=BOTH)  # same lesson, same voices
    assert str(cast["narrator"]) == "polly:Ruth"
    pool = {(v.provider, v.voice): v for v in cfg.pool}
    female = pool[(cast["female_1"].provider, cast["female_1"].voice)]
    male = pool[(cast["male_1"].provider, cast["male_1"].voice)]
    assert female.gender == "female" and male.gender == "male"


def test_pool_varies_across_lessons_and_providers(script):
    cfg = TTSConfig(provider="pool")
    picks = {
        str(
            cast_for(script.model_copy(update={"id": f"es-{n:04d}"}), cfg, available=BOTH)[
                "female_1"
            ]
        )
        for n in range(30)
    }
    assert len(picks) >= 4
    assert {p.split(":")[0] for p in picks} == BOTH


def test_pool_respects_available_providers(script):
    cfg = TTSConfig(provider="pool")
    cast = cast_for(script, cfg, available={"azure"})
    assert {ref.provider for ref in cast.values()} == {"azure"}
    with pytest.raises(TTSError, match="No narrator available"):
        cast_for(script, cfg, available=set())


def test_two_speakers_of_same_gender_get_different_voices(script):
    speakers = [s.model_copy(update={"voice_role": "female_1"}) for s in script.speakers]
    speakers[1] = speakers[1].model_copy(update={"voice_role": "female_2"})
    two_women = script.model_copy(update={"speakers": speakers})
    cast = cast_for(two_women, TTSConfig(provider="pool"), available={"polly"})
    assert cast["female_1"] != cast["female_2"]  # Mia and Lupe


def test_single_provider_mode(script):
    cast = cast_for(script, TTSConfig(provider="polly"))
    assert str(cast["male_1"]) == "polly:Andres" and str(cast["narrator"]) == "polly:Ruth"


class FakePolly:
    def __init__(self):
        self.calls = []

    def synthesize_speech(self, **kwargs):
        self.calls.append(kwargs)
        return {"AudioStream": io.BytesIO(b"\x00\x00" * 2400)}


def test_polly_requests():
    provider = PollyProvider.__new__(PollyProvider)
    provider.engine, provider.client = "generative", FakePolly()
    provider.synthesize("Necesito arreglar mi bicicleta.", "Mia", "es", 1.0)
    call = provider.client.calls[-1]
    # Polly accepts only 8000/16000 for PCM, so ask for 24 kHz MP3.
    assert (call["OutputFormat"], call["SampleRate"], call["TextType"]) == ("mp3", "24000", "text")

    provider.synthesize("puedo ayudar", "Mia", "es", 0.9)
    assert '<prosody rate="90%">puedo ayudar</prosody>' in provider.client.calls[-1]["Text"]

    runs = [("Ana", "es-MX"), (" brings her bike & helmet.", "en")]
    provider.synthesize_runs(runs, "Ruth", 1.0, "en")
    text = provider.client.calls[-1]["Text"]
    assert '<lang xml:lang="es-MX">Ana</lang>' in text and "&amp;" in text


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="needs espeak-ng")
def test_render_in_pool_mode(script, tmp_path):
    tts = TTSConfig(
        provider="pool",
        cache_dir=tmp_path / "cache",
        narrators=["espeak:en-us"],
        pool=[
            PoolVoice(provider="espeak", voice="es-419+f3", lang="es", gender="female"),
            PoolVoice(provider="espeak", voice="es-419+f4", lang="es", gender="female"),
            PoolVoice(provider="espeak", voice="es-419+m3", lang="es", gender="male"),
        ],
    )
    cfg = Config(out_dir=tmp_path / "out", tts=tts)
    result = render_lesson(script, cfg, mp3=False, pdf=False, anki=False)
    voices = json.loads(result.files["meta.json"].read_text())["voices"]
    assert voices["narrator"] == "espeak:en-us"
    assert voices["female_1"] in {"espeak:es-419+f3", "espeak:es-419+f4"}
    assert voices["male_1"] == "espeak:es-419+m3"


def test_elevenlabs_voices_lists_spanish_voices_as_pasteable_extra_voices(
    script, tmp_path, monkeypatch
):
    """`comenius voices elevenlabs` against a fake ElevenLabs API, then the pasted
    entries extend (not replace) the pool used for casting."""
    import threading
    import tomllib
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    from typer.testing import CliRunner

    from comenius import tts
    from comenius.cli import app

    pages = {
        None: {
            "voices": [
                {
                    "voice_id": "v_es_f",
                    "name": "Valeria",
                    "labels": {"gender": "female", "description": "warm"},
                    "verified_languages": [{"language": "es", "locale": "es-MX"}],
                },
                {"voice_id": "v_en", "name": "Rachel", "labels": {"gender": "female"},
                 "verified_languages": [{"language": "en", "locale": "en-US"}]},
            ],
            "has_more": True,
            "next_page_token": "p2",
        },
        "p2": {
            "voices": [
                {"voice_id": "v_es_m", "name": "Mateo",
                 "labels": {"gender": "male", "description": None},
                 "verified_languages": [{"language": "fr", "locale": None, "accent": None},
                                        {"language": "es", "accent": "argentine",
                                         "locale": None}]},
            ],
            "has_more": False,
        },
    }  # fmt: skip
    seen = []

    class FakeElevenLabs(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            seen.append((url.path, self.headers["xi-api-key"]))
            token = parse_qs(url.query).get("next_page_token", [None])[0]
            body = json.dumps(pages[token]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), FakeElevenLabs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tts, "ELEVENLABS_API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")

    result = CliRunner().invoke(app, ["voices", "elevenlabs"])
    server.shutdown()
    assert result.exit_code == 0, result.output
    assert seen == [("/v2/voices", "test-key")] * 2
    assert "Rachel" not in result.output

    cfg = TTSConfig.model_validate(
        {"provider": "pool", **tomllib.loads("[tts]\n" + result.output)["tts"]}
    )
    added = [(v.voice, v.gender, v.accent) for v in cfg.extra_voices]
    assert added == [("v_es_f", "female", "es-MX"), ("v_es_m", "male", "argentine")]
    assert len(cfg.pool_voices()) == len(cfg.pool) + 2  # defaults kept
    cast = cast_for(script, cfg, available={"elevenlabs"} | BOTH)
    assert all(ref.provider in {"elevenlabs"} | BOTH for ref in cast.values())


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="needs espeak-ng")
def test_render_says_which_pool_providers_it_skips(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from comenius.cli import app

    from .conftest import FIXTURE

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    config = tmp_path / "comenius.toml"
    config.write_text(
        f'out_dir = "{tmp_path / "out"}"\n'
        f'[tts]\nprovider = "pool"\ncache_dir = "{tmp_path / "tts"}"\n'
        'narrators = ["espeak:en-us"]\n'
        + "".join(
            f'[[tts.pool]]\nprovider = "espeak"\nvoice = "{v}"\nlang = "es"\ngender = "{g}"\n'
            for v, g in [("es-419+f3", "female"), ("es-419+m3", "male")]
        )
        + '[[tts.extra_voices]]\nprovider = "elevenlabs"\nvoice = "abc"\nlang = "es"\n'
        'gender = "female"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["render", str(FIXTURE), "--no-mp3", "--no-pdf", "--no-anki", "-c", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert "Voice pool: skipping elevenlabs (ELEVENLABS_API_KEY not set)" in result.stderr
    meta = json.loads((tmp_path / "out" / "es-0001" / "meta.json").read_text())
    assert meta["voices"]["female_1"] == "espeak:es-419+f3"  # cast from what's usable


class _FakeElevenLabsTTS:
    """Answers ElevenLabs text-to-speech requests with a tone 60 ms per character, and
    with-timestamps requests with matching per-character times. Records each request."""

    PER_CHAR = 0.06

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                timed = self.path.split("?")[0].endswith("/with-timestamps")
                fake.requests.append({**body, "timed": timed})
                n = round(len(body["text"]) * fake.PER_CHAR * 24000)
                pcm = b"".join(
                    (3000 if (i // 40) % 2 else -3000).to_bytes(2, "little", signed=True)
                    for i in range(n)
                )
                if timed:
                    import base64

                    starts = [i * fake.PER_CHAR for i in range(len(body["text"]))]
                    payload = json.dumps({
                        "audio_base64": base64.b64encode(pcm).decode(),
                        "alignment": {
                            "characters": list(body["text"]),
                            "character_start_times_seconds": starts,
                            "character_end_times_seconds": [s + fake.PER_CHAR for s in starts],
                        },
                    }).encode()  # fmt: skip
                    ctype = "application/json"
                else:
                    payload, ctype = pcm, "application/octet-stream"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"


def test_elevenlabs_gets_context_and_build_up_is_cut_from_the_line(script, tmp_path, monkeypatch):
    from comenius import tts

    fake = _FakeElevenLabsTTS()
    monkeypatch.setattr(tts, "ELEVENLABS_API", fake.url)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    roles = ("narrator", "female_1", "male_1", "female_2", "male_2")
    cfg = Config(
        out_dir=tmp_path / "out",
        tts=TTSConfig(
            provider="elevenlabs",
            cache_dir=tmp_path / "cache",
            voices={"elevenlabs": {r: f"voice_{r}" for r in roles}},
        ),
    )
    result = render_lesson(script, cfg, mp3=False, pdf=False)
    fake.server.shutdown()
    plain = [r for r in fake.requests if not r["timed"]]
    timed = [r for r in fake.requests if r["timed"]]

    # Build-up fragments are never synthesized on their own: each is cut from one timed
    # synthesis of its full line, and lasts about as long as its share of the line.
    segs = result.timeline.segments
    frags = [s for s in segs if s.kind == "buildup" and s.context.slice_of]
    assert frags and {r["text"] for r in timed} == {s.context.slice_of for s in frags}
    slow = {r["text"] for r in plain if r["voice_settings"]["speed"] == 0.9}
    assert not {s.text for s in frags} & slow  # (the same words may occur elsewhere at 1.0)
    for s in frags:
        tail = len(s.context.slice_of) - s.context.slice_of.index(s.text.split()[0])
        assert abs(s.duration_s - tail * fake.PER_CHAR) < 0.25, s.text

    # Dialog lines hear their neighbors; a bare drill cue hears its model sentence.
    line = script.dialog[1].text
    req = next(r for r in plain if r["text"] == line)
    assert req["previous_text"] == script.dialog[0].text
    assert req["next_text"] == script.dialog[2].text
    gender = next(d for d in script.drills if getattr(d, "mode", "") == "gender")
    cue = next(r for r in plain if r["text"] == gender.items[0].cue)
    assert cue["previous_text"] == gender.model and cue["language_code"] == "es"
