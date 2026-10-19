"""Learner state: which lessons you've done, what they taught, and what's due for review.

SQLite, one database per learner. Lessons are numbered in the order they're
recorded (`seq`), and review is scheduled in lessons rather than days: an item
comes back after REVIEW_INTERVALS[stage] more lessons, and each review moves it
to the next stage. Past the last stage an item is considered known and stops
being scheduled. This is the exposure-only signal from the roadmap; Anki results
and explicit feedback can later move items between stages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .schema import LessonScript, ReviewItem
from .sequence import fold_accents, strip_punct

REVIEW_INTERVALS = [1, 2, 4, 8, 16]  # lessons until the next review, by stage

ItemKind = Literal["grammar", "word", "phrase"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT UNIQUE NOT NULL,
    lang        TEXT NOT NULL,
    title       TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    script_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id          TEXT PRIMARY KEY,
    lang        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    answer      TEXT NOT NULL,
    prompt_en   TEXT NOT NULL,
    first_seq   INTEGER NOT NULL,
    stage       INTEGER NOT NULL,
    due_seq     INTEGER
);
CREATE TABLE IF NOT EXISTS exposures (
    item_id     TEXT NOT NULL,
    lesson_seq  INTEGER NOT NULL,
    role        TEXT NOT NULL,
    PRIMARY KEY (item_id, lesson_seq, role)
);
"""


class Item(BaseModel):
    id: str
    lang: str
    kind: ItemKind
    answer: str
    prompt_en: str
    first_seq: int
    stage: int
    due_seq: int | None


class LessonRecord(BaseModel):
    seq: int
    id: str
    title: str
    recorded_at: str


def item_id(lang: str, kind: ItemKind, key: str) -> str:
    """Stable id; phrases are keyed by normalized text so punctuation and case don't split them."""
    if kind == "phrase":
        key = fold_accents(strip_punct(key).lower())
    elif kind == "word":
        key = key.lower()
    return f"{lang}:{kind}:{key}"


def _due(seq: int, stage: int) -> int | None:
    return seq + REVIEW_INTERVALS[stage] if stage < len(REVIEW_INTERVALS) else None


class LearnerState:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # -- recording ---------------------------------------------------------------

    def record_lesson(self, script: LessonScript) -> int:
        """Record a lesson as done. Returns its sequence number. Errors if already recorded."""
        with self._db() as db:
            if db.execute("SELECT 1 FROM lessons WHERE id = ?", (script.id,)).fetchone():
                raise ValueError(f"lesson {script.id} is already recorded")
            seq = db.execute(
                "INSERT INTO lessons (id, lang, title, recorded_at, script_json) "
                "VALUES (?,?,?,?,?)",
                (
                    script.id,
                    script.lang,
                    script.title,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    script.model_dump_json(),
                ),
            ).lastrowid
            assert seq is not None

            def introduce(kind: ItemKind, key: str, answer: str, prompt_en: str) -> None:
                iid = item_id(script.lang, kind, key)
                existing = db.execute("SELECT stage FROM items WHERE id = ?", (iid,)).fetchone()
                if existing is None:
                    db.execute(
                        "INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                        (iid, script.lang, kind, answer, prompt_en, seq, 0, _due(seq, 0)),
                    )
                    role = "new"
                else:
                    role = "used"  # seen again in new material; doesn't advance its stage
                db.execute("INSERT OR IGNORE INTO exposures VALUES (?,?,?)", (iid, seq, role))

            for g in script.grammar_points:
                introduce("grammar", g, g, g)
            for w in script.new_words:
                introduce("word", w.lemma, w.lemma, w.gloss)
            for line in script.dialog:
                introduce("phrase", line.text, line.text, line.translation)

            # Review items prompted in this lesson advance a stage.
            for r in script.review:
                iid = item_id(script.lang, "phrase", r.answer)
                row = db.execute("SELECT stage FROM items WHERE id = ?", (iid,)).fetchone()
                if row is None:
                    iid = item_id(script.lang, "word", r.answer)
                    row = db.execute("SELECT stage FROM items WHERE id = ?", (iid,)).fetchone()
                if row is None:
                    continue  # review of something not tracked (e.g. hand-written fixture)
                stage = row["stage"] + 1
                db.execute(
                    "UPDATE items SET stage = ?, due_seq = ? WHERE id = ?",
                    (stage, _due(seq, stage), iid),
                )
                db.execute("INSERT OR IGNORE INTO exposures VALUES (?,?,?)", (iid, seq, "review"))
            return seq

    def assume_grammar(self, lang: str, point_ids: list[str]) -> int:
        """Mark syllabus points as already known (placement). They're never scheduled."""
        done = len(REVIEW_INTERVALS)
        added = 0
        with self._db() as db:
            for g in point_ids:
                cur = db.execute(
                    "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?)",
                    (item_id(lang, "grammar", g), lang, "grammar", g, g, 0, done, None),
                )
                added += cur.rowcount
        return added

    # -- queries -----------------------------------------------------------------

    def lessons(self, lang: str) -> list[LessonRecord]:
        with self._db() as db:
            rows = db.execute(
                "SELECT seq, id, title, recorded_at FROM lessons WHERE lang = ? ORDER BY seq",
                (lang,),
            ).fetchall()
        return [LessonRecord(**dict(r)) for r in rows]

    def next_seq(self) -> int:
        with self._db() as db:
            (last,) = db.execute("SELECT COALESCE(MAX(seq), 0) FROM lessons").fetchone()
        return last + 1

    def items(self, lang: str, kind: ItemKind) -> list[Item]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM items WHERE lang = ? AND kind = ? ORDER BY first_seq, id",
                (lang, kind),
            ).fetchall()
        return [Item(**dict(r)) for r in rows]

    def known_grammar(self, lang: str) -> set[str]:
        return {i.answer for i in self.items(lang, "grammar")}

    def known_words(self, lang: str) -> list[str]:
        return [i.answer for i in self.items(lang, "word")]

    def due(self, lang: str, limit: int) -> list[Item]:
        """Phrases and words due by the next lesson, most overdue first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM items WHERE lang = ? AND kind IN ('phrase', 'word') "
                "AND due_seq IS NOT NULL AND due_seq <= ? "
                "ORDER BY due_seq, kind, first_seq LIMIT ?",  # phrases before words
                (lang, self.next_seq(), limit),
            ).fetchall()
        return [Item(**dict(r)) for r in rows]

    def due_review_items(self, lang: str, limit: int) -> list[ReviewItem]:
        lessons_by_seq = {lesson.seq: lesson.id for lesson in self.lessons(lang)}
        return [
            ReviewItem(
                prompt_en=i.prompt_en, answer=i.answer, source_lesson=lessons_by_seq[i.first_seq]
            )
            for i in self.due(lang, limit)
        ]
