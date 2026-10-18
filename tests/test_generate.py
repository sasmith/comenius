"""Generation loop tested against a fake Anthropic client (no network)."""

from types import SimpleNamespace

from comenius.config import LLMConfig
from comenius.generate import LESSON_TOOL, REVIEW_TOOL, Generator, lesson_tool_schema
from comenius.schema import LessonSpec


def tool_response(name, data, block_id="toolu_1"):
    block = SimpleNamespace(type="tool_use", name=name, id=block_id, input=data)
    return SimpleNamespace(content=[block])


class FakeMessages:
    def __init__(self, lesson_outputs, review_outputs):
        self.lesson_outputs = list(lesson_outputs)
        self.review_outputs = list(review_outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        tool = kwargs["tool_choice"]["name"]
        if tool == LESSON_TOOL:
            return tool_response(LESSON_TOOL, self.lesson_outputs.pop(0))
        return tool_response(REVIEW_TOOL, {"issues": self.review_outputs.pop(0)})


class FakeClient:
    def __init__(self, lesson_outputs, review_outputs):
        self.messages = FakeMessages(lesson_outputs, review_outputs)


def lesson_data(script):
    data = script.model_dump(mode="json")
    for key in ("id", "review"):
        data.pop(key)
    return data


def make_spec():
    return LessonSpec(id="es-0009", topic="bike shop", new_word_budget=(6, 10))


def test_tool_schema_omits_pipeline_fields():
    schema = lesson_tool_schema()
    assert "id" not in schema["properties"] and "review" not in schema["properties"]
    assert "dialog" in schema["required"]


def test_accepts_clean_lesson_first_try(script, tmp_path):
    client = FakeClient([lesson_data(script)], [[]])
    gen = Generator(LLMConfig(cache_dir=tmp_path), client=client)
    out, report = gen.generate(make_spec())
    assert report.accepted and report.attempts == 1
    assert out.id == "es-0009"  # id comes from the spec


def test_regenerates_on_validation_and_review_errors(script, tmp_path):
    too_many = lesson_data(script)
    too_many["new_words"] = too_many["new_words"] * 2  # 16 > budget of 10
    clean = lesson_data(script)
    review_error = [{"location": "dialog[4]", "problem": "unnatural", "severity": "error"}]
    client = FakeClient([too_many, clean, clean], [review_error, []])
    gen = Generator(LLMConfig(cache_dir=tmp_path), client=client)

    out, report = gen.generate(make_spec())

    assert report.accepted and report.attempts == 3
    lesson_calls = [c for c in client.messages.calls if c["tool_choice"]["name"] == LESSON_TOOL]
    feedback = lesson_calls[1]["messages"][-1]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["is_error"]
    assert "new_word_budget" in feedback["content"]
    assert "unnatural" in lesson_calls[2]["messages"][-1]["content"][0]["content"]


def test_cache_hit_skips_api(script, tmp_path):
    client = FakeClient([lesson_data(script)], [[]])
    gen = Generator(LLMConfig(cache_dir=tmp_path), client=client)
    gen.generate(make_spec())
    calls = len(client.messages.calls)
    out, report = gen.generate(make_spec())
    assert len(client.messages.calls) == calls and report.accepted


def test_gives_up_with_needs_review(script, tmp_path):
    bad = lesson_data(script)
    bad["new_words"] = bad["new_words"] * 2
    client = FakeClient([bad, bad, bad], [])
    gen = Generator(LLMConfig(cache_dir=tmp_path, max_attempts=3), client=client)
    out, report = gen.generate(make_spec())
    assert not report.accepted and report.attempts == 3
    assert any("new_word_budget" in i for i in report.issues)
