from pathlib import Path

import pytest

from comenius.schema import LessonScript

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "lessons" / "es-0001.json"


@pytest.fixture
def script() -> LessonScript:
    return LessonScript.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
