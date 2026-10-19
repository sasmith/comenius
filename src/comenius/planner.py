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


class NothingToPlan(RuntimeError):
    pass


def plan_next(
    state: LearnerState,
    cfg: StateConfig,
    *,
    lang: str,
    topic: str | None = None,
    situation: str = "",
    lesson_id: str | None = None,
) -> LessonSpec:
    """Next lesson spec. Without a topic, the first unfinished situation in the queue is used."""
    syllabus = load_syllabus(lang)
    known = state.known_grammar(lang)
    grammar = [p.id for p in syllabus.next_points(known, n=cfg.new_grammar_points)]
    level = syllabus.get(grammar[0]).level if grammar else syllabus.points[-1].level
    queue = [e for e in state.queue(lang) if not e.complete]

    if topic is None:
        entry = next((e for e in queue if e.kind == "situation"), None)
        if entry is None:
            raise NothingToPlan(
                "No topic given and no unfinished situations in the queue "
                '(comenius queue add "...").'
            )
        topic = entry.text
        if entry.total > 1:
            earlier = [lsn.title for lsn in state.lessons(lang) if lsn.topic == topic]
            series = f"Lesson {entry.done + 1} of {entry.total} on this topic."
            if earlier:
                series += f" Earlier lessons: {'; '.join(earlier)}. Use a different scene."
            situation = f"{situation} {series}".strip()

    known_words = {w.lower() for w in state.known_words(lang)}
    wanted = [
        w for e in queue if e.kind == "words" for w in e.words() if w.lower() not in known_words
    ]
    return LessonSpec(
        id=lesson_id or next_lesson_id(state, lang),
        lang=lang,
        dialect=syllabus.dialect,
        level=level,
        topic=topic,
        situation=situation,
        grammar_points=grammar,
        required_words=list(dict.fromkeys(wanted))[: cfg.queue_words_per_lesson],
        known_words=state.known_words(lang),
        review=state.due_review_items(lang, cfg.review_items),
    )
