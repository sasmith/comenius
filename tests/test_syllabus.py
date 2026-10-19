from pathlib import Path

import pytest
from pydantic import ValidationError

from comenius.schema import LessonSpec
from comenius.syllabus import Syllabus, load_syllabus

ROOT = Path(__file__).resolve().parent.parent


def test_spanish_syllabus_loads_and_is_ordered():
    syl = load_syllabus("es")
    assert len(syl.points) >= 50
    levels = [p.level for p in syl.points]
    assert levels[0] == "A1" and "A2" in levels and levels[-1] == "B1"


def test_next_points_respect_prerequisites():
    syl = load_syllabus("es")
    first = syl.next_points(set(), n=3)
    assert all(not p.requires for p in first)
    known = {p.id for p in syl.points if p.level == "A1"}
    nxt = syl.next_points(known, n=1)[0]
    assert nxt.level == "A2" and set(nxt.requires) <= known


def test_requires_must_come_earlier():
    bad = {
        "language": "es",
        "dialect": "es-MX",
        "points": [
            {
                "id": "es.a1.b",
                "level": "A1",
                "title": "b",
                "description": "",
                "requires": ["es.a1.a"],
            },
            {"id": "es.a1.a", "level": "A1", "title": "a", "description": ""},
        ],
    }
    with pytest.raises(ValidationError, match="must appear earlier"):
        Syllabus.model_validate(bad)


def test_fixture_and_example_grammar_ids_are_in_syllabus(script):
    syl = load_syllabus("es")
    for g in script.grammar_points:
        assert g in syl, g
    spec = LessonSpec.from_file(ROOT / "examples/specs/es-0002.yaml")
    assert "no" in spec.known_words  # not parsed as a boolean
    for g in spec.grammar_points:
        assert g in syl, g
