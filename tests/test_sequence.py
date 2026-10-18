import os
from pathlib import Path

from comenius.config import LessonConfig
from comenius.sequence import (
    build_timeline,
    buildup_fragments,
    estimate_duration,
    split_sentences,
)

GOLDEN = Path(__file__).parent / "golden" / "es-0001.timeline.txt"


def test_buildup_grows_from_the_end():
    frags = buildup_fragments("Perdón, ¿dónde está la estación?", 3)
    assert frags == ["la estación", "está la estación", "Perdón, ¿dónde está la estación?"]
    # Every fragment is a suffix of the stripped sentence
    for f in frags[:-1]:
        assert "dónde está la estación".endswith(f) or "Perdón dónde está la estación".endswith(f)


def test_short_sentences_are_not_split():
    assert buildup_fragments("¿Qué le pasa?") == ["¿Qué le pasa?"]


def test_intermediate_fragments_have_no_punctuation():
    for frag in buildup_fragments("Muy bien, ¿para cuándo la necesita usted?", 4)[:-1]:
        assert not set(frag) & set("¿?¡!.,")


def test_buildup_spans_the_whole_line():
    frags = buildup_fragments("Buenas tardes. ¿En qué le puedo ayudar?")
    assert frags[0] == "puedo ayudar"
    assert "¿En qué le puedo ayudar?" in frags
    assert frags[-1] == "Buenas tardes. ¿En qué le puedo ayudar?"


def test_buildup_crosses_sentences_at_phrase_boundaries():
    frags = buildup_fragments("Para mañana, por favor. ¿Cuánto cuesta?")
    assert frags == ["¿Cuánto cuesta?", "por favor. ¿Cuánto cuesta?", frags[-1]]


def test_sections_are_signposted(script):
    narr = [s.text for s in build_timeline(script).segments if s.voice == "narrator"]
    for sign in (
        "Dialog. Listen.",
        "Dialog. Repeat to learn.",
        "Dialog. Repeat for mastery.",
        "Substitution drills.",
        "Translation drills.",
    ):
        assert sign in narr
    quiet = LessonConfig(signposts=False)
    assert "Substitution drills." not in [s.text for s in build_timeline(script, quiet).segments]


def test_pause_before_next_buildup_step(script):
    cfg = LessonConfig()
    segs = [s for s in build_timeline(script, cfg).segments if s.kind == "buildup"]
    # Pairs: (model + learner pause), (model again + step gap)
    assert all(segs[i].pause_after_s >= 1.0 for i in range(0, len(segs), 2))
    assert all(segs[i].pause_after_s == cfg.step_gap_s for i in range(1, len(segs), 2))


def test_split_sentences():
    assert split_sentences("Buenas tardes. ¿En qué le puedo ayudar?") == [
        "Buenas tardes.",
        "¿En qué le puedo ayudar?",
    ]


def test_timeline_structure(script):
    t = build_timeline(script)
    titles = [c.title for c in t.chapters]
    assert titles == [
        "Introduction",
        "Listen",
        "Repeat to learn",
        "Repeat for mastery",
        "Substitution drills",
        "Translation drills",
        "Review",
        "Listen again",
    ]
    # Segments are in time order and don't overlap
    for a, b in zip(t.segments, t.segments[1:], strict=False):
        assert b.start_s >= a.start_s + a.duration_s + a.pause_after_s - 1e-6
    # Narrator speaks English only
    assert all(s.lang == "en" for s in t.segments if s.voice == "narrator")


def test_no_in_lesson_recall_by_default(script):
    assert not [s for s in build_timeline(script).segments if s.kind == "recall"]


def test_every_line_is_recalled_and_never_twice_in_a_row(script):
    t = build_timeline(script, LessonConfig(in_lesson_recall=True))
    recalls = [s for s in t.segments if s.kind == "recall"]
    recalled_answers = []
    for i, s in enumerate(t.segments):
        if s.kind == "recall":
            recalled_answers.append(t.segments[i + 1].text)
    for line in script.dialog:
        assert line.text in recalled_answers
    assert len(recalls) >= len(script.dialog)
    assert all(a != b for a, b in zip(recalled_answers, recalled_answers[1:], strict=False))


def test_pause_multiplier_scales_learner_pauses(script):
    base = build_timeline(script, LessonConfig())
    slow = build_timeline(script, LessonConfig(pause_multiplier=1.5))
    assert slow.learner_pause_s() > 1.4 * base.learner_pause_s()


def test_pause_fits_answer_duration():
    cfg = LessonConfig()
    assert cfg.learner_pause(2.0) == 4.0  # 1.5 * 2 + 1


def _golden_text(script) -> str:
    t = build_timeline(script, LessonConfig(), estimate_duration)
    return (
        "\n".join(
            f"{s.start_s:8.2f} {s.kind:<12} {s.voice:<9} {s.pause_after_s:5.2f} {s.text}"
            for s in t.segments
        )
        + "\n"
    )


def test_golden_timeline(script):
    """Sequencer changes show up as a diff. Regenerate with UPDATE_GOLDEN=1."""
    text = _golden_text(script)
    if os.environ.get("UPDATE_GOLDEN") or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_default_narration_is_minimal(script):
    narr = [s.text for s in build_timeline(script).segments if s.voice == "narrator"]
    # Only the intro and English recall/translation cues remain
    assert not any(t.startswith(("Now", "Listen", "The word for", "Here's")) for t in narr)
    assert not any(" says: " in t for t in narr)
    assert narr[:2] == [f"{script.title}.", script.setting]


def test_narration_flags_restore_english(script):
    cfg = LessonConfig(
        line_translations=True, introduce_new_words=True, guideposts=True, in_lesson_recall=True
    )
    narr = [s.text for s in build_timeline(script, cfg).segments if s.voice == "narrator"]
    assert any(" says: " in t for t in narr)
    assert any(t.startswith("The word for") for t in narr)
    assert any(t.startswith("Now I'll give you a word") for t in narr)
    assert any(t.startswith("How do you say") for t in narr)
