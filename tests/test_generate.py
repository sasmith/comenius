"""Generation loop tested against a fake Anthropic client (no network)."""

import json
from types import SimpleNamespace

from comenius.config import LLMConfig
from comenius.generate import (
    LESSON_TOOL,
    REVIEW_SCHEMA,
    REVIEW_TOOL,
    Generator,
    lesson_tool_schema,
)
from comenius.llm import APIBackend
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


SCHEMA_KEYWORDS = {
    "$defs", "$ref", "type", "properties", "required", "items", "enum", "const", "default",
    "title", "description", "anyOf", "oneOf", "allOf", "minItems", "maxItems", "minimum",
    "maximum", "additionalProperties", "prefixItems",
}  # fmt: skip


def _unknown_keywords(node, found):
    for key, value in node.items():
        if key not in SCHEMA_KEYWORDS:
            found.add(key)
        if key in ("properties", "$defs"):
            for sub in value.values():
                _unknown_keywords(sub, found)
        elif key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for sub in value:
                _unknown_keywords(sub, found)
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            _unknown_keywords(value, found)
    return found


def test_schemas_use_only_standard_keywords():
    # Claude Code's --json-schema validates strictly; pydantic adds OpenAPI extras.
    assert _unknown_keywords(lesson_tool_schema(), set()) == set()
    assert _unknown_keywords(REVIEW_SCHEMA, set()) == set()


def test_tool_schema_omits_pipeline_fields():
    schema = lesson_tool_schema()
    assert "id" not in schema["properties"] and "review" not in schema["properties"]
    assert "dialog" in schema["required"]


def test_accepts_clean_lesson_first_try(script, tmp_path):
    client = FakeClient([lesson_data(script)], [[]])
    gen = Generator(LLMConfig(cache_dir=tmp_path), backend=APIBackend(client))
    out, report = gen.generate(make_spec())
    assert report.accepted and report.attempts == 1
    assert out.id == "es-0009"  # id comes from the spec


def test_regenerates_on_validation_and_review_errors(script, tmp_path):
    too_many = lesson_data(script)
    too_many["new_words"] = too_many["new_words"] * 2  # 16 > budget of 10
    clean = lesson_data(script)
    review_error = [{"location": "dialog[4]", "problem": "unnatural", "severity": "error"}]
    client = FakeClient([too_many, clean, clean], [review_error, []])
    gen = Generator(LLMConfig(cache_dir=tmp_path), backend=APIBackend(client))

    out, report = gen.generate(make_spec())

    assert report.accepted and report.attempts == 3
    lesson_calls = [c for c in client.messages.calls if c["tool_choice"]["name"] == LESSON_TOOL]
    feedback = lesson_calls[1]["messages"][-1]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["is_error"]
    assert "new_word_budget" in feedback["content"]
    assert "unnatural" in lesson_calls[2]["messages"][-1]["content"][0]["content"]


def test_cache_hit_skips_api(script, tmp_path):
    client = FakeClient([lesson_data(script)], [[]])
    gen = Generator(LLMConfig(cache_dir=tmp_path), backend=APIBackend(client))
    gen.generate(make_spec())
    calls = len(client.messages.calls)
    out, report = gen.generate(make_spec())
    assert len(client.messages.calls) == calls and report.accepted


def test_gives_up_with_needs_review(script, tmp_path):
    bad = lesson_data(script)
    bad["new_words"] = bad["new_words"] * 2
    client = FakeClient([bad, bad, bad], [])
    gen = Generator(LLMConfig(cache_dir=tmp_path, max_attempts=3), backend=APIBackend(client))
    out, report = gen.generate(make_spec())
    assert not report.accepted and report.attempts == 3
    assert any("new_word_budget" in i for i in report.issues)


FAKE_CLAUDE = r"""#!/usr/bin/env python3
# Stands in for the `claude` CLI: checks the flags, logs the call, answers from files.
import json, os, sys
args = sys.argv[1:]
assert args[0] == "-p" and "--json-schema" in args and "--safe-mode" in args
assert args[args.index("--tools") + 1] == "" and "--bare" not in args  # keep the user's login
schema = json.loads(args[args.index("--json-schema") + 1])
assert "discriminator" not in json.dumps(schema)  # strict validators reject it
review = "issues" in schema["properties"]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"prompt": sys.stdin.read(), "review": review}) + "\n")
if review:
    out = {"issues": []}
else:
    out = json.load(open(os.environ["FAKE_CLAUDE_LESSON"]))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "", "structured_output": out}))
"""


def test_claude_code_backend_runs_the_cli(script, tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    lesson = tmp_path / "lesson.json"
    lesson.write_text(json.dumps(lesson_data(script)))
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LESSON", str(lesson))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    cfg = LLMConfig(cache_dir=tmp_path / "cache", backend="claude-code", claude_binary=str(fake))
    out, report = Generator(cfg).generate(make_spec())

    assert report.accepted and out.id == "es-0009"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [c["review"] for c in calls] == [False, True]
    assert "Topic: bike shop" in calls[0]["prompt"]
