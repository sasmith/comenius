from comenius.schema import LessonSpec, SubstitutionDrill
from comenius.validate import validate_script


def spec(**kw) -> LessonSpec:
    base = dict(id="es-0001", topic="bike shop", new_word_budget=(6, 10), dialog_lines=(6, 10))
    base.update(kw)
    return LessonSpec(**base)


def errors(issues):
    return [i.code for i in issues if i.severity == "error"]


def test_fixture_is_clean(script):
    assert validate_script(script, spec()) == []


def test_non_english_narrator_text_is_an_error(script):
    script.dialog[0].translation = "¿Good afternoon?"
    assert "non_english_text" in errors(validate_script(script))


def test_budget_and_length(script):
    issues = validate_script(script, spec(new_word_budget=(1, 3), dialog_lines=(2, 4)))
    assert {"new_word_budget", "dialog_length"} <= set(errors(issues))


def test_required_words(script):
    issues = validate_script(script, spec(required_words=["escuela"]))
    assert "missing_required_word" in errors(issues)


def test_unchanged_substitution(script):
    drill = next(d for d in script.drills if isinstance(d, SubstitutionDrill))
    drill.items[0].answer = drill.model
    assert "substitution_unchanged" in errors(validate_script(script))


def test_known_words_warning(script):
    issues = validate_script(script, spec(known_words=["buenas", "tardes"]))
    warned = [i for i in issues if i.code == "possibly_unknown_tokens"]
    assert warned and "pesos" in warned[0].message
