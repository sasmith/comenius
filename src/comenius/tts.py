"""Text-to-speech providers and a content-addressed clip cache.

Every clip is normalized to 24 kHz mono 16-bit WAV with leading/trailing
silence trimmed, so pacing is controlled entirely by the sequencer.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

from .config import TTSConfig

SAMPLE_RATE = 24_000


class TTSError(RuntimeError):
    pass


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


def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if not value:
        raise TTSError(f"Set {var} to use this TTS provider.")
    return value


class EspeakProvider:
    """Offline and free, robotic. Good for testing pipeline and pacing."""

    name = "espeak"

    def __init__(self) -> None:
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not self.binary:
            raise TTSError("espeak-ng not found (apt install espeak-ng / brew install espeak-ng)")

    def synthesize(self, text: str, voice: str, lang: str, rate: float) -> bytes:
        wpm = str(int(165 * rate))
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
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
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
            text, voice=voice, speed=rate, lang=KOKORO_LANGS.get(lang, lang)
        )
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        return _pcm_to_wav(pcm, sr)


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
                "ffmpeg", "-y", "-loglevel", "error", "-i", src,
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


class ClipCache:
    """Synthesizes clips on demand, keyed by a hash of everything that affects audio."""

    def __init__(self, cfg: TTSConfig, provider: TTSProvider | None = None) -> None:
        self.cfg = cfg
        self.provider = provider or make_provider(cfg)
        self.voices = cfg.voice_map(self.provider.name)
        self.dir = Path(cfg.cache_dir) / self.provider.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.synthesized = 0
        self.hits = 0

    def voice_for(self, role: str) -> str:
        try:
            return self.voices[role]
        except KeyError:
            raise TTSError(
                f"No {self.provider.name} voice configured for role {role!r}; "
                f"set [tts.voices.{self.provider.name}] in comenius.toml"
            ) from None

    def key(self, role: str, lang: str, text: str, rate: float) -> str:
        payload = json.dumps(
            {
                "provider": self.provider.name,
                "model": getattr(self.provider, "model", None),
                "voice": self.voice_for(role),
                "lang": lang,
                "rate": round(rate, 3),
                "text": text,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def clip(self, role: str, lang: str, text: str, rate: float = 1.0) -> Path:
        path = self.dir / f"{self.key(role, lang, text, rate)}.wav"
        if path.exists():
            self.hits += 1
            return path
        raw = self.provider.synthesize(text, self.voice_for(role), lang, rate)
        tmp = path.with_suffix(".tmp.wav")
        normalize_audio(raw, tmp)
        tmp.replace(path)
        self.synthesized += 1
        return path

    def duration(self, role: str, lang: str, text: str, rate: float = 1.0) -> float:
        return round(wav_duration(self.clip(role, lang, text, rate)), 3)
