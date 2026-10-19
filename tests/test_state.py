import pytest

from comenius.config import StateConfig
from comenius.planner import plan_next
from comenius.state import REVIEW_INTERVALS, LearnerState, item_id
from comenius.syllabus import load_syllabus


@pytest.fixture
def state(tmp_path):
    return LearnerState(tmp_path / "state.db")


def test_recording_a_lesson_tracks_grammar_words_and_lines(state, script):
    assert state.record_lesson(script) == 1
    assert set(script.grammar_points) <= state.known_grammar("es")
    assert "llanta" in state.known_words("es")
    due = state.due("es", limit=100)
    assert len(due) == len(script.dialog) + len(script.new_words)
    assert due[0].kind == "phrase" and due[0].due_seq == 1 + REVIEW_INTERVALS[0]
    with pytest.raises(ValueError, match="already recorded"):
        state.record_lesson(script)


def test_reviewing_advances_the_schedule(state, script):
    state.record_lesson(script)
    reviewed = state.due_review_items("es", limit=3)
    nxt = script.model_copy(update={"id": "es-0002", "review": reviewed})
    assert state.record_lesson(nxt) == 2
    by_id = {i.id: i for i in state.items("es", "phrase")}
    first = by_id[item_id("es", "phrase", reviewed[0].answer)]
    assert first.stage == 1 and first.due_seq == 2 + REVIEW_INTERVALS[1]
    # Lines seen again without being reviewed keep their schedule
    unreviewed = by_id[item_id("es", "phrase", script.dialog[-1].text)]
    assert unreviewed.stage == 0


def test_planner_uses_syllabus_state_and_reviews(state, script):
    syl = load_syllabus("es")
    state.assume_grammar("es", [p.id for p in syl.points[:5]])
    state.record_lesson(script)
    spec = plan_next(state, StateConfig(review_items=4), lang="es", topic="school drop-off")
    assert spec.id == "es-0002"
    known = state.known_grammar("es")
    assert spec.grammar_points == [syl.next_points(known, n=1)[0].id]
    assert spec.grammar_points[0] not in known
    assert len(spec.review) == 4 and spec.review[0].source_lesson == "es-0001"
    assert "llanta" in spec.known_words


def test_placement_points_are_never_reviewed(state):
    assert state.assume_grammar("es", ["es.a1.greetings", "es.a1.numbers"]) == 2
    assert state.assume_grammar("es", ["es.a1.greetings"]) == 0
    assert state.due("es", limit=10) == []
    assert {"es.a1.greetings", "es.a1.numbers"} <= state.known_grammar("es")


def test_queue_series_drives_topics_until_done(state, script):
    from comenius.planner import NothingToPlan

    cfg = StateConfig()
    with pytest.raises(NothingToPlan):
        plan_next(state, cfg, lang="es")
    state.queue_add("es", "situation", "At the bike shop", lessons=2)
    first = plan_next(state, cfg, lang="es")
    assert first.topic == "At the bike shop" and "Lesson 1 of 2" in first.situation

    state.record_lesson(script.model_copy(update={"topic": "At the bike shop"}))
    second = plan_next(state, cfg, lang="es")
    assert "Lesson 2 of 2" in second.situation and script.title in second.situation

    state.record_lesson(script.model_copy(update={"id": "es-0002", "topic": "At the bike shop"}))
    assert state.queue("es")[0].complete
    with pytest.raises(NothingToPlan):
        plan_next(state, cfg, lang="es")


def test_queued_words_become_required_until_known(state, script):
    state.queue_add("es", "words", "llanta, pasaporte, equipaje, maleta, boleto")
    state.record_lesson(script)  # teaches llanta
    spec = plan_next(state, StateConfig(queue_words_per_lesson=3), lang="es", topic="travel")
    assert spec.required_words == ["pasaporte", "equipaje", "maleta"]
    entry = state.queue("es")[0]
    assert (entry.done, entry.total) == (1, 5)
