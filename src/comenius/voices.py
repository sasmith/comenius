"""Choose which voice speaks each role in a lesson (the "cast").

Single-provider mode uses that provider's voice map. Pool mode picks each dialog
speaker from `tts.pool` plus `tts.extra_voices`, limited to providers you have
credentials for: the same lesson always gets the same cast, different lessons get
different voices, and two speakers in a lesson never share a voice. The narrator is
the first available entry of `tts.narrators`.
"""

from __future__ import annotations

import hashlib
import os
import random
import shutil
from functools import cache
from pathlib import Path

from pydantic import BaseModel

from .config import PoolVoice, TTSConfig
from .schema import LessonScript
from .tts import TTSError, VoiceRef

POOL = "pool"


@cache
def _aws_credentials() -> bool:
    try:
        import boto3
    except ImportError:
        return False
    return boto3.Session().get_credentials() is not None


def provider_available(name: str, cfg: TTSConfig) -> bool:
    """Whether a provider can be used here (credentials, binaries, model files)."""
    if name == "espeak":
        return bool(shutil.which("espeak-ng") or shutil.which("espeak"))
    if name == "kokoro":
        d = Path(os.path.expanduser(str(cfg.kokoro_dir)))
        return (d / "kokoro-v1.0.onnx").exists() and (d / "voices-v1.0.bin").exists()
    if name == "polly":
        return _aws_credentials()
    env = {"azure": "AZURE_SPEECH_KEY", "openai": "OPENAI_API_KEY"}
    env["elevenlabs"] = "ELEVENLABS_API_KEY"
    return bool(os.environ.get(env.get(name, ""), ""))


def _roles(script: LessonScript) -> list[str]:
    return ["narrator"] + list(dict.fromkeys(s.voice_role for s in script.speakers))


def cast_for(
    script: LessonScript,
    cfg: TTSConfig,
    provider: str | None = None,
    available: set[str] | None = None,
) -> dict[str, VoiceRef]:
    name = provider or cfg.provider
    if name != POOL:
        voices = cfg.voice_map(name)
        missing = [r for r in _roles(script) if r not in voices]
        if missing:
            raise TTSError(f"No {name} voice for {missing}; set [tts.voices.{name}]")
        return {role: VoiceRef(provider=name, voice=voices[role]) for role in _roles(script)}

    usable = available if available is not None else {
        p
        for p in {v.provider for v in cfg.pool_voices()} | {n.split(":")[0] for n in cfg.narrators}
        if provider_available(p, cfg)
    }  # fmt: skip
    narrator = next((n for n in cfg.narrators if n.split(":")[0] in usable), None)
    if narrator is None:
        raise TTSError(f"No narrator available from {cfg.narrators}")
    prov, voice = narrator.split(":", 1)
    cast = {"narrator": VoiceRef(provider=prov, voice=voice)}

    seed = int(hashlib.sha256(script.id.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    taken: list[PoolVoice] = []
    for role in _roles(script)[1:]:
        gender = "female" if role.startswith("female") else "male"
        options = [
            v
            for v in cfg.pool_voices()
            if v.provider in usable and v.lang == script.lang and v.gender == gender
            and v not in taken
        ]  # fmt: skip
        if not options:
            raise TTSError(
                f"No available {gender} {script.lang} voice in the pool for {role} "
                f"(usable providers: {sorted(usable) or 'none'})"
            )
        pick = rng.choice(options)
        taken.append(pick)
        cast[role] = VoiceRef(provider=pick.provider, voice=pick.voice)
    return cast


class ElevenLabsLanguage(BaseModel):
    language: str
    accent: str = ""
    locale: str = ""


class ElevenLabsVoice(BaseModel):
    voice_id: str
    name: str
    labels: dict[str, str] = {}
    verified_languages: list[ElevenLabsLanguage] = []

    def speaks(self, lang: str) -> ElevenLabsLanguage | None:
        return next((v for v in self.verified_languages if v.language == lang), None)


class ElevenLabsVoicePage(BaseModel):
    voices: list[ElevenLabsVoice]
    has_more: bool = False
    next_page_token: str | None = None


def elevenlabs_voices(lang: str) -> list[ElevenLabsVoice]:
    """Voices in your ElevenLabs account (including ones added from the library) that are
    verified for `lang`."""
    import httpx

    from . import tts

    key = tts._require_env("ELEVENLABS_API_KEY")
    found: list[ElevenLabsVoice] = []
    token: str | None = None
    while True:
        params = {"page_size": "100"} | ({"next_page_token": token} if token else {})
        r = httpx.get(
            f"{tts.ELEVENLABS_API}/v2/voices",
            params=params,
            headers={"xi-api-key": key},
            timeout=60,
        )
        if r.status_code != 200:
            raise TTSError(f"ElevenLabs voices {r.status_code}: {r.text[:300]}")
        page = ElevenLabsVoicePage.model_validate(r.json())
        found += [v for v in page.voices if v.speaks(lang)]
        if not page.has_more:
            return found
        token = page.next_page_token
