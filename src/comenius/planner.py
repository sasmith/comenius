"""Plan the next lesson from learner state and the syllabus.

The output is an ordinary LessonSpec, the same thing you'd write by hand, so
generation, validation, and rendering don't change.
"""

from __future__ import annotations

import re

from .config import StateConfig
from .schema import LessonSpec
from .state import LearnerState
from .syllabus import load_syllabus


def next_lesson_id(state: LearnerState, lang: str) -> str:
    numbers = [
        int(m.group(1))
        for lesson in state.lessons(lang)
        if (m := re.fullmatch(rf"{re.escape(lang)}-(\d+)", lesson.id))
    ]
    return f"{lang}-{max(numbers, default=0) + 1:04d}"


def plan_next(
    state: LearnerState,
    cfg: StateConfig,
    *,
    lang: str,
    topic: str,
    situation: str = "",
    lesson_id: str | None = None,
) -> LessonSpec:
    syllabus = load_syllabus(lang)
    known = state.known_grammar(lang)
    grammar = [p.id for p in syllabus.next_points(known, n=cfg.new_grammar_points)]
    level = syllabus.get(grammar[0]).level if grammar else syllabus.points[-1].level
    return LessonSpec(
        id=lesson_id or next_lesson_id(state, lang),
        lang=lang,
        dialect=syllabus.dialect,
        level=level,
        topic=topic,
        situation=situation,
        grammar_points=grammar,
        known_words=state.known_words(lang),
        review=state.due_review_items(lang, cfg.review_items),
    )
