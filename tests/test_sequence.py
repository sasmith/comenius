import os
from pathlib import Path

from comenius.config import LessonConfig
from comenius.sequence import (
    build_timeline,
    buildup_fragments,
    estimate_duration,
    split_sentences,
)
from comenius.tts import language_runs

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
    assert frags[0] == "qué le puedo ayudar"  # never "puedo ayudar": "le" stays on its verb
    assert "¿En qué le puedo ayudar?" in frags
    assert frags[-1] == "Buenas tardes. ¿En qué le puedo ayudar?"


def test_buildup_never_cuts_a_word_from_the_word_it_leans_on():
    # A pronoun, article, possessive, or "no" stays with what follows it.
    assert buildup_fragments("Hola, me llamo Laura.")[0] == "me llamo Laura"
    assert buildup_fragments("Tiene una llanta ponchada.")[0] == "una llanta ponchada"
    for line in ["No sé dónde está mi casa nueva.", "Perdón, ¿dónde está la estación?"]:
        for frag in buildup_fragments(line)[:-1]:
            words = line.replace(",", "").replace("¿", "").replace("?", "").rstrip(".").split()
            before = words[len(words) - len(frag.split()) - 1]
            assert before.lower() not in {"no", "mi", "la"}, (line, frag)


def test_buildup_crosses_sentences_at_phrase_boundaries():
    frags = buildup_fragments("Para mañana, por favor. ¿Cuánto cuesta?")
    assert frags == ["¿Cuánto cuesta?", "por favor. ¿Cuánto cuesta?", frags[-1]]


def test_sections_are_signposted(script):
    narr = [s.text for s in build_timeline(script).segments if s.voice == "narrator"]
    for start in (
        "Now let's build it up",
        "Now the whole conversation again",
        "Now swap in a new noun",
        "Now swap in a new word",
        "Now say these in Spanish",
    ):
        assert any(t.startswith(start) for t in narr), start
    # The first dialog follows the English intro directly, with no signpost
    segs = build_timeline(script).segments
    first_dialog = next(i for i, s in enumerate(segs) if s.kind == "dialog")
    assert segs[first_dialog - 1].text == script.setting
    quiet = LessonConfig(signposts=False)
    assert not any(s.text.startswith("Now swap") for s in build_timeline(script, quiet).segments)


def test_both_repetitions_get_the_same_pause(script):
    segs = [s for s in build_timeline(script).segments if s.kind == "buildup"]
    for first, second in zip(segs[::2], segs[1::2], strict=True):
        assert first.text == second.text
        assert first.pause_after_s == second.pause_after_s >= 1.0


def test_full_line_said_once_before_buildup(script):
    segs = build_timeline(script).segments
    for i, s in enumerate(segs):
        if s.kind == "preview":
            nxt = segs[i + 1]
            assert nxt.kind == "buildup" and nxt.text != s.text
            assert s.pause_after_s >= 1.0
    previews = [s.text for s in segs if s.kind == "preview"]
    assert previews[0] == script.dialog[0].text
    # Lines too short to build up are not previewed (they'd just be said three times)
    assert "¿Qué le pasa?" not in previews


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
        "Line by line",
        "Your turn",
        "Noun genders",
        "Swap a word",
        "Say it in Spanish",
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
    cfg = LessonConfig(signposts=False)
    narr = [s.text for s in build_timeline(script, cfg).segments if s.voice == "narrator"]
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
    assert "Practice this sentence." in narr
    assert any(t.startswith("How do you say") for t in narr)


def test_progressive_drill_cues_chain_from_previous_answer(script):
    t = build_timeline(script)
    segs = t.segments
    cues = [(s.text, segs[i + 1].text) for i, s in enumerate(segs) if s.kind == "drill_cue"]
    assert ("querer", "Quiero una bicicleta.") in cues
    i = cues.index(("querer", "Quiero una bicicleta."))
    assert cues[i + 1] == ("gato", "Quiero un gato.")
    assert cues[i + 2] == ("algunos", "Quiero algunos gatos.")


def test_names_in_english_narration_are_marked_for_native_pronunciation(script):
    intro = next(s for s in build_timeline(script).segments if s.text == script.setting)
    assert {"Ana", "Don Luis"} <= set(intro.terms) and intro.term_lang == "es-MX"
    runs = language_runs(script.setting, "en", intro.terms, intro.term_lang)
    assert ("Don Luis", "es-MX") in runs and ("Ana", "es-MX") in runs
    assert language_runs("Banana bread.", "en", ["Ana"], "es-MX") == [("Banana bread.", "en")]


def test_gender_cues_are_shuffled_but_progressive_cues_keep_order(script):
    # The fixture's gender cues alternate feminine/masculine as written.
    gender = next(d for d in script.drills if getattr(d, "mode", "") == "gender")
    progressive = [d for d in script.drills if getattr(d, "mode", "") == "progressive"]
    t = build_timeline(script)
    cues = [s.text for s in t.segments if s.kind == "drill_cue" and s.chapter == "Noun genders"]
    written = [it.cue for it in gender.items]
    assert sorted(cues) == sorted(written) and cues != written
    again = [s.text for s in build_timeline(script).segments if s.kind == "drill_cue"]
    assert again[: len(cues)] == cues  # seeded: the same lesson always gets the same order
    swap = [s.text for s in t.segments if s.kind == "drill_cue" and s.chapter == "Swap a word"]
    assert swap == [it.cue for d in progressive for it in d.items]


def test_pdf_lists_gender_cues_in_the_audio_order(script):
    import json as _json

    from comenius.pdf import _data

    gender = next(d for d in script.drills if getattr(d, "mode", "") == "gender")
    pdf_cues = [it["cue"] for it in _json.loads(_data(script))["substitution"][0]["items"]]
    t = build_timeline(script)
    audio = [s.text for s in t.segments if s.kind == "drill_cue" and s.chapter == "Noun genders"]
    assert pdf_cues == audio and sorted(pdf_cues) == sorted(it.cue for it in gender.items)
