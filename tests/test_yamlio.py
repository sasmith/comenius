from comenius.schema import LessonSpec


def test_bare_words_and_times_stay_strings(tmp_path):
    spec_file = tmp_path / "spec.yaml"
    spec_file.write_text(
        "id: es-0099\n"
        "topic: test\n"
        "known_words: [no, sí, on, yes, off, 3:30, 0123, 2026-10-18, 1e3]\n"
        "new_word_budget: [4, 8]\n"
        "gender_drills: 2\n"
        "required_words:\n",
        encoding="utf-8",
    )
    spec = LessonSpec.from_file(spec_file)
    assert spec.known_words == ["no", "sí", "on", "yes", "off", "3:30", "0123", "2026-10-18", "1e3"]
    # Typed fields are still converted by the model
    assert spec.new_word_budget == (4, 8) and spec.gender_drills == 2
