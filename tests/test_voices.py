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
