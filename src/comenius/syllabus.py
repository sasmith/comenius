"""Grammar syllabus: an ordered list of points with prerequisites, per language.

The syllabus is the long-term backbone the planner walks through. Order in the
file is teaching order; `requires` must point at earlier entries.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .yamlio import load_yaml

Level = Literal["A1", "A2", "B1", "B2", "C1", "C2"]


class GrammarPoint(BaseModel):
    id: str
    level: Level
    title: str
    description: str
    examples: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class Syllabus(BaseModel):
    language: str
    dialect: str
    points: list[GrammarPoint]

    @model_validator(mode="after")
    def _check(self) -> Syllabus:
        seen: set[str] = set()
        levels = ["A1", "A2", "B1", "B2", "C1", "C2"]
        last_level = 0
        for p in self.points:
            if p.id in seen:
                raise ValueError(f"duplicate syllabus id {p.id!r}")
            prefix = f"{self.language}.{p.level.lower()}."
            if not p.id.startswith(prefix):
                raise ValueError(f"{p.id!r} should start with {prefix!r}")
            missing = [r for r in p.requires if r not in seen]
            if missing:
                raise ValueError(f"{p.id!r} requires {missing}, which must appear earlier")
            level = levels.index(p.level)
            if level < last_level:
                raise ValueError(f"{p.id!r} ({p.level}) comes after a higher-level point")
            last_level = level
            seen.add(p.id)
        return self

    def get(self, point_id: str) -> GrammarPoint:
        return self._by_id()[point_id]

    def __contains__(self, point_id: str) -> bool:
        return point_id in self._by_id()

    def _by_id(self) -> dict[str, GrammarPoint]:
        return {p.id: p for p in self.points}

    def next_points(self, known: set[str], n: int = 1) -> list[GrammarPoint]:
        """The first `n` points, in syllabus order, not yet known whose prerequisites are."""
        ready = [p for p in self.points if p.id not in known and set(p.requires) <= known]
        return ready[:n]


@cache
def load_syllabus(lang: str) -> Syllabus:
    text = resources.files("comenius.data.syllabus").joinpath(f"{lang}.yaml").read_text("utf-8")
    return Syllabus.model_validate(load_yaml(text))
