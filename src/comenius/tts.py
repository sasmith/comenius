"""Text-to-speech providers and a content-addressed clip cache.

Every clip is normalized to 24 kHz mono 16-bit WAV with leading/trailing
silence trimmed, so pacing is controlled entirely by the sequencer.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

import httpx
import imageio_ffmpeg
from pydantic import BaseModel

from .config import TTSConfig

SAMPLE_RATE = 24_000


@cache
def ffmpeg_exe() -> str:
    """System ffmpeg if installed, else the static binary bundled with imageio-ffmpeg."""
    return shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()


class TTSError(RuntimeError):
    pass


# A run of text and the language to pronounce it in, e.g. ("Ana", "es-MX").
Run = tuple[str, str]
_PUNCT = ",.;:!?…"


def language_runs(text: str, lang: str, terms: Sequence[str], term_lang: str) -> list[Run]:
    """Split `text` into runs, with whole-word occurrences of `terms` in `term_lang`.

    >>> language_runs("The mechanic, Don Luis, greets her.", "en", ["Don Luis"], "es-MX")
    [('The mechanic, ', 'en'), ('Don Luis', 'es-MX'), (', greets her.', 'en')]
    """
    if not terms:
        return [(text, lang)]
    alternatives = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    parts = re.split(rf"(?<!\w)({alternatives})(?!\w)", text)
    runs = [(part, term_lang if i % 2 else lang) for i, part in enumerate(parts)]
    return [(t, lg) for t, lg in runs if t]


def terms_in(text: str, terms: Sequence[str]) -> list[str]:
    return [t for t in terms if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text)]


class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        """Return audio bytes in any format ffmpeg can read."""
        ...


def _pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


ELEVENLABS_API = "https://api.elevenlabs.io"


def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        raise TTSError(f"Set {var} to use this TTS provider.")
    return value


class EspeakProvider:
    """Offline and free, robotic. Good for testing pipeline and pacing."""

    name = "espeak"
    per_run_language = True

    def __init__(self) -> None:
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.binary:
            raise TTSError("espeak-ng not found (apt install espeak-ng / brew install espeak-ng)")

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        wpm = str(int(165 * rate))
        base = lang.split("-")[0]
        if not voice.startswith(base):
            voice = base  # e.g. the English narrator voice reading a Spanish name
        result = subprocess.run(
            [self.binary, "-v", voice, "-s", wpm, "--stdout", text],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            raise TTSError(f"espeak failed: {result.stderr.decode(errors='replace')}")
        return result.stdout


class OpenAIProvider:
    name = "openai"
    url = "https://api.openai.com/v1/audio/speech"

    def __init__(self, model: str) -> None:
        self.model = model
        self.key = _require_env("OPENAI_API_KEY")

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        body: dict[str, object] = {
            "model": self.model,
            "voice": voice,
            "input": text,
            "speed": rate,
            "response_format": "wav",
        }
        if lang != "en":
            body["instructions"] = (
                f"Speak naturally and clearly as a native speaker of {lang}. "
                "Do not add anything to the text."
            )
        r = httpx.post(
            self.url, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=60
        )
        if r.status_code != 200:
            raise TTSError(f"OpenAI TTS {r.status_code}: {r.text[:300]}")
        return r.content


class AzureProvider:
    name = "azure"

    def __init__(self, region: str) -> None:
        self.key = _require_env("AZURE_SPEECH_KEY")
        self.region = os.environ.get("AZURE_SPEECH_REGION", region)

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        locale = "-".join(voice.split("-")[:2])
        pct = f"{round((rate - 1.0) * 100):+d}%"
        ssml = (
            f'<speak version="1.0" xml:lang="{locale}">'
            f'<voice name="{voice}"><prosody rate="{pct}">{escape(text)}</prosody></voice>'
            "</speak>"
        )
        return self._post(ssml)

    def synthesize_runs(self, runs: list[Run], voice: str, rate: float, lang: str) -> bytes:
        """One request; foreign runs wrapped in <lang> (needs a Multilingual voice)."""
        locale = "-".join(voice.split("-")[:2])
        pct = f"{round((rate - 1.0) * 100):+d}%"
        body = "".join(
            escape(t) if lg.split("-")[0] == locale.split("-")[0]
            else f'<lang xml:lang="{lg}">{escape(t)}</lang>'
            for t, lg in runs
        )  # fmt: skip
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{locale}">'
            f'<voice name="{voice}"><prosody rate="{pct}">{body}</prosody></voice></speak>'
        )
        return self._post(ssml)

    def _post(self, ssml: str) -> bytes:
        r = httpx.post(
            f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            content=ssml.encode(),
            headers={
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                "User-Agent": "comenius",
            },
            timeout=60,
        )
        if r.status_code != 200:
            raise TTSError(f"Azure TTS {r.status_code}: {r.text[:300]}")
        return r.content


class ElevenLabsProvider:
    name = "elevenlabs"

    def __init__(self, model: str) -> None:
        self.model = model
        self.key = _require_env("ELEVENLABS_API_KEY")

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        r = httpx.post(
            f"{ELEVENLABS_API}/v1/text-to-speech/{voice}",
            params={"output_format": "pcm_24000"},
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {"speed": max(0.7, min(1.2, rate))},
            },
            headers={"xi-api-key": self.key},
            timeout=60,
        )
        if r.status_code != 200:
            raise TTSError(f"ElevenLabs TTS {r.status_code}: {r.text[:300]}")
        return _pcm_to_wav(r.content)


KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_LANGS = {"en": "en-us", "es": "es", "fr": "fr-fr", "it": "it", "pt": "pt-br"}


class KokoroProvider:
    """Open-weights neural TTS that runs locally (pip install 'comenius[kokoro]')."""

    name = "kokoro"
    per_run_language = True

    def __init__(self, model_dir: Path) -> None:
        model_dir = Path(os.path.expanduser(str(model_dir)))
        model, voices = model_dir / "kokoro-v1.0.onnx", model_dir / "voices-v1.0.bin"
        if not (model.exists() and voices.exists()):
            raise TTSError(
                f"Kokoro model files not found in {model_dir}. Download them with:\n"
                f"  mkdir -p {model_dir} && cd {model_dir} && "
                f"curl -LO {KOKORO_URL}/kokoro-v1.0.onnx -LO {KOKORO_URL}/voices-v1.0.bin"
            )
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            raise TTSError("Install the kokoro extra: uv sync --extra kokoro") from None
        self.model = "kokoro-v1.0"
        self.kokoro = Kokoro(str(model), str(voices))

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        import numpy as np

        samples, sr = self.kokoro.create(
            text, voice=voice, speed=rate, lang=KOKORO_LANGS[lang.split("-")[0]]
        )
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return _pcm_to_wav(pcm, sr)


class PollyProvider:
    """Amazon Polly. Credentials from the usual AWS environment/profile (needs boto3)."""

    name = "polly"

    def __init__(self, engine: str, region: str) -> None:
        try:
            import boto3
        except ImportError:
            raise TTSError("Polly needs boto3: uv sync --extra aws") from None
        self.engine = engine
        self.model = f"polly-{engine}"
        self.client = boto3.client("polly", region_name=region)

    def _speak(self, body: str, voice: str, rate: float, ssml: bool) -> bytes:
        if rate != 1.0:
            body = f'<prosody rate="{round(rate * 100)}%">{body}</prosody>'
            ssml = True
        r = self.client.synthesize_speech(
            Text=f"<speak>{body}</speak>" if ssml else body,
            TextType="ssml" if ssml else "text",
            VoiceId=voice,
            Engine=self.engine,
            # Polly's PCM tops out at 16 kHz; MP3 goes to 24 kHz. normalize_audio decodes it.
            OutputFormat="mp3",
            SampleRate=str(SAMPLE_RATE),
        )
        return r["AudioStream"].read()

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        return self._speak(escape(text), voice, rate, ssml=False)

    def synthesize_runs(self, runs: list[Run], voice: str, rate: float, lang: str) -> bytes:
        body = "".join(
            escape(t) if lg == lang else f'<lang xml:lang="{lg}">{escape(t)}</lang>'
            for t, lg in runs
        )
        return self._speak(body, voice, rate, ssml=True)


def make_provider(cfg: TTSConfig, name: str | None = None) -> TTSProvider:
    name = name or cfg.provider
    if name == "espeak":
        return EspeakProvider()
    if name == "openai":
        return OpenAIProvider(cfg.openai_model)
    if name == "azure":
        return AzureProvider(cfg.azure_region)
    if name == "elevenlabs":
        return ElevenLabsProvider(cfg.elevenlabs_model)
    if name == "kokoro":
        return KokoroProvider(cfg.kokoro_dir)
    if name == "polly":
        return PollyProvider(cfg.polly_engine, cfg.polly_region)
    raise TTSError(f"Unknown TTS provider {name!r}")


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


_TRIM = (
    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,"
    "areverse,"
    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
    "areverse"
)


def normalize_audio(raw: bytes, out: Path) -> None:
    """Decode anything ffmpeg reads -> trimmed 24 kHz mono s16 WAV."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(raw)
        src = f.name
    try:
        result = subprocess.run(
            [
                ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src,
                "-af", _TRIM, "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16",
                str(out),
            ],
            capture_output=True,
            check=False,
        )  # fmt: skip
        if result.returncode != 0:
            raise TTSError(f"ffmpeg normalize failed: {result.stderr.decode(errors='replace')}")
    finally:
        os.unlink(src)


class VoiceRef(BaseModel):
    """A concrete voice: which provider, and that provider's voice id."""

    provider: str
    voice: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.voice}"


class ClipCache:
    """Synthesizes clips on demand, keyed by a hash of everything that affects audio.

    `cast` maps each voice role (narrator, female_1, ...) to a provider and voice, so
    one lesson can mix providers. Providers are created on first use.
    """

    def __init__(self, cfg: TTSConfig, cast: dict[str, VoiceRef]) -> None:
        self.cfg = cfg
        self.cast = cast
        self._providers: dict[str, TTSProvider] = {}
        self.synthesized = 0
        self.hits = 0

    def voice_ref(self, role: str) -> VoiceRef:
        try:
            return self.cast[role]
        except KeyError:
            raise TTSError(f"No voice cast for role {role!r}") from None

    def provider_for(self, role: str) -> TTSProvider:
        name = self.voice_ref(role).provider
        if name not in self._providers:
            self._providers[name] = make_provider(self.cfg, name)
        return self._providers[name]

    def voice_for(self, role: str) -> str:
        return self.voice_ref(role).voice

    def key(
        self, role: str, lang: str, text: str, rate: float, terms: Sequence[str], term_lang: str
    ) -> str:
        provider = self.provider_for(role)
        payload = json.dumps(
            {
                "provider": provider.name,
                "model": getattr(provider, "model", None),
                "voice": self.voice_for(role),
                "lang": lang,
                "rate": round(rate, 3),
                "text": text,
                **({"terms": sorted(terms), "term_lang": term_lang} if terms else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def clip(
        self,
        role: str,
        lang: str,
        text: str,
        rate: float = 1.0,
        terms: Sequence[str] = (),
        term_lang: str = "",
        tempo: float = 1.0,
    ) -> Path:
        """Synthesize `text`; whole-word `terms` inside it are pronounced in `term_lang`.

        Providers with in-request language switching (Azure) get one request.
        Providers that take a language per call (Kokoro, espeak) synthesize each run
        in the same voice and the runs are spliced. Others ignore `terms`.
        `tempo` speeds the synthesized clip up afterward, keeping pitch.
        """
        if tempo != 1.0:
            base = self.clip(role, lang, text, rate, terms, term_lang)
            fast = base.with_name(f"{base.stem}-x{tempo:.2f}.wav")
            if not fast.exists():
                tmp = fast.with_suffix(".tmp.wav")
                subprocess.run(
                    [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(base),
                     "-af", f"atempo={tempo:.2f}", str(tmp)],
                    check=True,
                )  # fmt: skip
                tmp.replace(fast)
            return fast
        provider = self.provider_for(role)
        runs = language_runs(text, lang, terms, term_lang)
        if len(runs) == 1 or not (
            hasattr(provider, "synthesize_runs") or getattr(provider, "per_run_language", False)
        ):
            terms = ()
        folder = Path(self.cfg.cache_dir) / provider.name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.key(role, lang, text, rate, terms, term_lang)}.wav"
        if path.exists():
            self.hits += 1
            return path
        tmp = path.with_suffix(".tmp.wav")
        if not terms:
            normalize_audio(provider.synthesize(text, self.voice_for(role), lang, rate), tmp)
        elif hasattr(provider, "synthesize_runs"):
            normalize_audio(provider.synthesize_runs(runs, self.voice_for(role), rate, lang), tmp)
        else:
            # Each run is synthesized on its own (and cached); a comma or period at
            # a join becomes a short pause, otherwise the pieces nearly touch.
            spoken = [(t.strip().strip(_PUNCT).strip(), t, lg) for t, lg in runs]
            spoken = [r for r in spoken if r[0]]
            with wave.open(str(tmp), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                for n, (_, raw_text, run_lang) in enumerate(spoken):
                    if n:
                        prev = spoken[n - 1][1].rstrip()
                        joint = prev[-1:] + raw_text.lstrip()[:1]
                        gap = 0.22 if set(joint) & set(_PUNCT) else 0.05
                        out.writeframes(b"\x00\x00" * round(gap * SAMPLE_RATE))
                    # Keep sentence-final punctuation on the last piece for intonation.
                    text_n = raw_text.strip().lstrip(_PUNCT).strip()
                    with wave.open(str(self.clip(role, run_lang, text_n, rate)), "rb") as w:
                        out.writeframes(w.readframes(w.getnframes()))
        tmp.replace(path)
        self.synthesized += 1
        return path

    def duration(
        self,
        role: str,
        lang: str,
        text: str,
        rate: float = 1.0,
        terms: Sequence[str] = (),
        term_lang: str = "",
        tempo: float = 1.0,
    ) -> float:
        return round(wav_duration(self.clip(role, lang, text, rate, terms, term_lang, tempo)), 3)
