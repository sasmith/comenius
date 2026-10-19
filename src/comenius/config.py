"""Configuration, loaded from comenius.toml (optional) with built-in defaults."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_VOICES: dict[str, dict[str, str]] = {
    # Offline, robotic, free. For pipeline testing and pacing experiments.
    "espeak": {
        "narrator": "en-us",
        "female_1": "es-419+f3",
        "male_1": "es-419+m3",
        "female_2": "es-419+f4",
        "male_2": "es-419+m4",
    },
    "azure": {
        "narrator": "en-US-AndrewMultilingualNeural",  # can switch language mid-sentence
        "female_1": "es-MX-DaliaNeural",
        "male_1": "es-MX-JorgeNeural",
        "female_2": "es-MX-RenataNeural",
        "male_2": "es-MX-LibertoNeural",
    },
    "openai": {
        "narrator": "alloy",
        "female_1": "nova",
        "male_1": "onyx",
        "female_2": "shimmer",
        "male_2": "echo",
    },
    # Local neural voices; Spanish has one female (ef_dora) and two male voices.
    "kokoro": {
        "narrator": "af_heart",
        "female_1": "ef_dora",
        "male_1": "em_alex",
        "female_2": "ef_dora",
        "male_2": "em_santa",
    },
    # Amazon Polly ids are ASCII (Andres, not Andrés).
    "polly": {
        "narrator": "Ruth",
        "female_1": "Mia",
        "male_1": "Andres",
        "female_2": "Lupe",
        "male_2": "Pedro",
    },
    # ElevenLabs voice ids are account-specific; fill these in comenius.toml.
    "elevenlabs": {},
}


class PoolVoice(BaseModel):
    """A native voice the pool may cast as a dialog speaker."""

    provider: str
    voice: str
    lang: str
    gender: Literal["female", "male"]
    accent: str = ""  # informational, e.g. es-MX


# Native Latin American Spanish voices from the paid providers. Kokoro and OpenAI are
# left out by default: their Spanish can carry a non-native accent.
DEFAULT_POOL = [
    PoolVoice(provider="polly", voice="Mia", lang="es", gender="female", accent="es-MX"),
    PoolVoice(provider="polly", voice="Andres", lang="es", gender="male", accent="es-MX"),
    PoolVoice(provider="polly", voice="Lupe", lang="es", gender="female", accent="es-US"),
    PoolVoice(provider="polly", voice="Pedro", lang="es", gender="male", accent="es-US"),
    PoolVoice(
        provider="azure", voice="es-MX-DaliaNeural", lang="es", gender="female", accent="es-MX"
    ),
    PoolVoice(
        provider="azure", voice="es-MX-JorgeNeural", lang="es", gender="male", accent="es-MX"
    ),
    PoolVoice(
        provider="azure", voice="es-MX-RenataNeural", lang="es", gender="female", accent="es-MX"
    ),
    PoolVoice(
        provider="azure", voice="es-MX-LibertoNeural", lang="es", gender="male", accent="es-MX"
    ),
    PoolVoice(
        provider="azure", voice="es-US-PalomaNeural", lang="es", gender="female", accent="es-US"
    ),
    PoolVoice(
        provider="azure", voice="es-US-AlonsoNeural", lang="es", gender="male", accent="es-US"
    ),
]

# English narrator, first available wins. Kept stable across lessons on purpose.
DEFAULT_NARRATORS = [
    "polly:Ruth",
    "azure:en-US-AndrewMultilingualNeural",
    "kokoro:af_heart",
    "espeak:en-us",
]


class ConfigError(ValueError):
    pass


class LessonConfig(BaseModel):
    pause_multiplier: float = Field(1.0, description="Global scale on learner pauses.")
    pause_answer_factor: float = 1.5
    pause_base_s: float = 1.0
    buildup_rate: float = 0.9
    max_buildup_steps: int = 4
    in_lesson_recall: bool = Field(
        False,
        description="Weave English-cued recall of earlier lines into the lesson (Pimsleur-style).",
    )
    recall_intervals_s: list[float] = Field(default_factory=lambda: [5.0, 25.0, 120.0, 600.0])
    final_listen_syllables_per_s: float = Field(
        7.0,
        description="Speed the final 'Listen again' up to this rate per voice (native Spanish "
        "is ~7.8). 0 plays it at the voices' own speed.",
    )
    max_tempo: float = Field(1.2, description="Cap on the final-listen speed-up.")
    tempo_method: Literal["stretch", "provider"] = Field(
        "stretch",
        description="How the final listen is sped up: 'stretch' speeds the normal clip up "
        "afterward (uniform, free, every provider); 'provider' asks the TTS service to speak "
        "faster (new synthesis, may sound more like natural fast speech).",
    )
    gap_s: float = 0.5
    step_gap_s: float = Field(1.5, description="Silence after a drill answer, before the next cue.")
    line_gap_s: float = 0.7
    section_gap_s: float = 1.5
    # English narration. Off by default: the target language carries the lesson,
    # and meaning comes from context, the intro, and the PDF.
    line_translations: bool = Field(False, description="Narrate each line's English in build-up.")
    introduce_new_words: bool = Field(False, description="'The word for X is ...' before lines.")
    guideposts: bool = Field(False, description="Instructions like 'Now the whole line.'")
    signposts: bool = Field(True, description="Announce each section ('Substitution drills.').")

    def learner_pause(self, answer_duration_s: float) -> float:
        raw = self.pause_answer_factor * answer_duration_s + self.pause_base_s
        return round(self.pause_multiplier * raw, 3)


class TTSConfig(BaseModel):
    provider: str = Field(
        "espeak",
        description="espeak, kokoro, polly, azure, openai, elevenlabs, or 'pool' to cast each "
        "lesson's speakers from `pool` across providers.",
    )
    pool: list[PoolVoice] = Field(default_factory=lambda: list(DEFAULT_POOL))
    narrators: list[str] = Field(
        default_factory=lambda: list(DEFAULT_NARRATORS),
        description="'provider:voice' narrator preferences for pool mode; first available wins.",
    )
    polly_engine: str = "generative"
    polly_region: str = "us-east-1"
    cache_dir: Path = Path(".cache/tts")
    voices: dict[str, dict[str, str]] = Field(default_factory=dict)
    openai_model: str = "gpt-4o-mini-tts"
    azure_region: str = "westus2"
    elevenlabs_model: str = "eleven_multilingual_v2"
    kokoro_dir: Path = Path("~/.cache/comenius/kokoro")

    def voice_map(self, provider: str | None = None) -> dict[str, str]:
        name = provider or self.provider
        merged = dict(DEFAULT_VOICES.get(name, {}))
        merged.update(self.voices.get(name, {}))
        return merged


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-5-5"
    review_model: str | None = None
    max_attempts: int = 3
    max_tokens: int = 16000
    cache_dir: Path = Path(".cache/llm")


class StateConfig(BaseModel):
    path: Path = Path("~/.local/share/comenius/state.db")
    review_items: int = Field(5, description="Due items to put in each lesson's Review section.")
    new_grammar_points: int = Field(1, description="New syllabus points per planned lesson.")
    queue_words_per_lesson: int = Field(3, description="Queued words required per lesson.")


class FeedConfig(BaseModel):
    title: str = "Comenius"
    description: str = "Generated audio language lessons."
    author: str = ""
    base_url: str | None = Field(
        None,
        description="Public URL of the uploaded site; the feed is base_url/feed.xml. Leave unset "
        "for AWS S3 (derived from bucket, region, prefix); set it for R2, CloudFront, or a "
        "custom domain.",
    )
    site_dir: Path = Path("site")
    # Upload target (S3, or S3-compatible such as Cloudflare R2). Credentials come from
    # the usual AWS environment variables or profile.
    bucket: str = ""
    prefix: str = Field("", description="Key prefix; use a long random string for privacy.")
    endpoint_url: str | None = Field(None, description="For R2/MinIO; omit for AWS S3.")
    region: str | None = Field(None, description="Bucket region; `publish` looks it up if unset.")

    def public_url(self, region: str | None = None) -> str:
        """base_url, else the AWS S3 virtual-hosted URL of bucket/prefix."""
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.endpoint_url:
            raise ConfigError("Set [feed] base_url (the public URL) when using endpoint_url.")
        region = region or self.region
        if not (self.bucket and region):
            raise ConfigError("Set [feed] bucket and region, or base_url, in comenius.toml.")
        prefix = self.prefix.strip("/")
        return f"https://{self.bucket}.s3.{region}.amazonaws.com" + (f"/{prefix}" if prefix else "")


class Config(BaseModel):
    out_dir: Path = Path("out")
    lesson: LessonConfig = Field(default_factory=LessonConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    feed: FeedConfig = Field(default_factory=FeedConfig)


def load_config(path: Path | None = None) -> Config:
    """Load config from `path`, else ./comenius.toml if present, else defaults."""
    candidate = path or Path("comenius.toml")
    if candidate.exists():
        with candidate.open("rb") as f:
            return Config.model_validate(tomllib.load(f))
    if path is not None:
        raise FileNotFoundError(path)
    return Config()
