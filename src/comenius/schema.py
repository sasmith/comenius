"""Core data model: what the generator produces and what the renderers consume.

LessonSpec   -> input to the generator (what to teach)
LessonScript -> generator output; the single source of truth for every rendering
Timeline     -> the sequencer's expansion of a script into timed audio segments
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# Voice roles are abstract; config maps them to concrete provider voices.
VoiceRole = Literal["narrator", "female_1", "male_1", "female_2", "male_2"]


class Speaker(BaseModel):
    id: str = Field(description="Short id used by dialog lines, e.g. 'A'.")
    name: str
    voice_role: VoiceRole


class NewWord(BaseModel):
    lemma: str = Field(description="Dictionary form, e.g. 'costar' for 'cuesta'.")
    form: str | None = Field(
        default=None, description="Form as it appears in the dialog, if different."
    )
    pos: str = Field(default="", description="Part of speech, e.g. NOUN, VERB.")
    gloss: str = Field(description="Short English meaning.")


class DialogLine(BaseModel):
    speaker: str
    text: str = Field(description="Target-language text of the line.")
    translation: str = Field(description="Natural English translation.")
    grammar_tags: list[str] = Field(default_factory=list)


class SubstitutionItem(BaseModel):
    cue: str = Field(description="Target-language word or phrase to substitute in.")
    answer: str = Field(description="The full new sentence.")


class SubstitutionDrill(BaseModel):
    type: Literal["substitution"] = "substitution"
    model: str = Field(description="The model sentence in the target language.")
    model_translation: str = ""
    items: list[SubstitutionItem] = Field(min_length=1)


class TranslationItem(BaseModel):
    prompt_en: str
    answers: list[str] = Field(min_length=1, description="First answer is the one voiced.")


class TranslationDrill(BaseModel):
    type: Literal["translation"] = "translation"
    items: list[TranslationItem] = Field(min_length=1)


Drill = Annotated[SubstitutionDrill | TranslationDrill, Field(discriminator="type")]


class ReviewItem(BaseModel):
    """An item from an earlier lesson, prompted English -> target."""

    prompt_en: str
    answer: str
    source_lesson: str | None = None


class LessonScript(BaseModel):
    id: str
    lang: str = "es"
    dialect: str = "es-MX"
    title: str
    setting: str = Field(description="One or two English sentences setting the scene.")
    speakers: list[Speaker] = Field(min_length=1)
    grammar_points: list[str] = Field(default_factory=list)
    new_words: list[NewWord] = Field(default_factory=list)
    dialog: list[DialogLine] = Field(min_length=1)
    grammar_note: str = ""
    drills: list[Drill] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _speakers_exist(self) -> LessonScript:
        ids = {s.id for s in self.speakers}
        for i, line in enumerate(self.dialog):
            if line.speaker not in ids:
                raise ValueError(f"dialog[{i}] speaker {line.speaker!r} not in speakers {ids}")
        return self

    def speaker(self, speaker_id: str) -> Speaker:
        return next(s for s in self.speakers if s.id == speaker_id)


class LessonSpec(BaseModel):
    """What the planner asks the generator to write."""

    id: str
    lang: str = "es"
    dialect: str = "es-MX"
    level: str = "A1"
    topic: str
    situation: str = ""
    grammar_points: list[str] = Field(default_factory=list)
    grammar_descriptions: dict[str, str] = Field(default_factory=dict)
    new_word_budget: tuple[int, int] = (6, 10)
    required_words: list[str] = Field(default_factory=list)
    known_words: list[str] = Field(default_factory=list)
    review: list[ReviewItem] = Field(default_factory=list)
    dialog_lines: tuple[int, int] = (6, 10)
    max_words_per_line: int = 14
    substitution_drills: int = 2
    translation_items: int = 6


# ---------------------------------------------------------------------------
# Timeline


SegmentKind = Literal[
    "narration",
    "dialog",
    "preview",
    "buildup",
    "shadow",
    "drill_cue",
    "drill_answer",
    "recall",
    "review",
]


class Segment(BaseModel):
    chapter: str
    kind: SegmentKind
    voice: VoiceRole
    lang: str
    text: str
    rate: float = 1.0
    pause_after_s: float = 0.0
    start_s: float = 0.0
    duration_s: float = 0.0


class Chapter(BaseModel):
    title: str
    start_s: float


class Timeline(BaseModel):
    lesson_id: str
    segments: list[Segment]
    chapters: list[Chapter]
    duration_s: float

    def learner_pause_s(self) -> float:
        """Total silence where the learner is expected to speak."""
        return sum(
            s.pause_after_s
            for s in self.segments
            if s.kind in {"preview", "buildup", "shadow", "drill_cue", "recall", "review"}
        )
