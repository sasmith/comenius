"""Default lesson topics (data/topics.yaml), used when the queue has no situation."""

from __future__ import annotations

from functools import cache
from importlib import resources

from pydantic import BaseModel, model_validator

from .syllabus import Level
from .yamlio import load_yaml

LEVELS: list[Level] = ["A1", "A2", "B1", "B2", "C1", "C2"]


class Topic(BaseModel):
    id: str
    level: Level
    theme: str
    situation: str


class Topics(BaseModel):
    topics: list[Topic]

    @model_validator(mode="after")
    def _check(self) -> Topics:
        ids = [t.id for t in self.topics]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate topic ids")
        return self

    def next_topic(self, used: set[str], level: Level) -> Topic | None:
        """First unused topic at or below `level`, else the first unused one at all."""
        fresh = [t for t in self.topics if t.situation not in used]
        within = [t for t in fresh if LEVELS.index(t.level) <= LEVELS.index(level)]
        return (within or fresh or [None])[0]


@cache
def load_topics() -> Topics:
    text = resources.files("comenius.data").joinpath("topics.yaml").read_text("utf-8")
    return Topics.model_validate(load_yaml(text))
