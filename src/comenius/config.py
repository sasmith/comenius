"""Configuration, loaded from comenius.toml (optional) with built-in defaults."""

from __future__ import annotations

import tomllib
from pathlib import Path

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
        "narrator": "en-US-AndrewNeural",
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
    # ElevenLabs voice ids are account-specific; fill these in comenius.toml.
    "elevenlabs": {},
}


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
    gap_s: float = 0.5
    step_gap_s: float = Field(
        1.5, description="Silence after a model answer, before the next build-up step or cue."
    )
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
    provider: str = "espeak"
    cache_dir: Path = Path(".cache/tts")
    voices: dict[str, dict[str, str]] = Field(default_factory=dict)
    openai_model: str = "gpt-4o-mini-tts"
    azure_region: str = "westus2"
    elevenlabs_model: str = "eleven_multilingual_v2"

    def voice_map(self, provider: str | None = None) -> dict[str, str]:
        name = provider or self.provider
        merged = dict(DEFAULT_VOICES.get(name, {}))
        merged.update(self.voices.get(name, {}))
        return merged


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-5-5"
    review_model: str | None = None
    max_attempts: int = 3
    max_tokens: int = 8000
    cache_dir: Path = Path(".cache/llm")


class FeedConfig(BaseModel):
    title: str = "Comenius"
    description: str = "Generated audio language lessons."
    author: str = ""
    base_url: str = "https://example.com/comenius"


class Config(BaseModel):
    out_dir: Path = Path("out")
    lesson: LessonConfig = Field(default_factory=LessonConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
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
