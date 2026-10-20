"""Generate a LessonScript from a LessonSpec with Claude, then review and regenerate.

Flow per attempt:
  1. Generate (a structured call returning LessonScript JSON; see llm.py).
  2. Schema validation + deterministic checks (validate.py).
  3. LLM review as a native-speaker editor for the pinned dialect.
Any errors are sent back with the previous answer so the model revises in context.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import LLMConfig
from .llm import Backend, Turn, make_backend
from .schema import LessonScript, LessonSpec
from .syllabus import load_syllabus
from .validate import Issue, validate_script

LESSON_TOOL = "submit_lesson"
REVIEW_TOOL = "submit_review"

SYSTEM_PROMPT = """\
You write short audio language lessons in the tradition of the FSI courses and \
Pimsleur, for an adult learner who studies while running or driving. You produce \
content only; separate code decides pacing, build-up, repetition, and recall.

Write:
- A short, natural, everyday dialog between two speakers in the target dialect. \
Spoken register; the kind of exchange a person really has. Prefer short lines.
- An English translation of each line (natural English, not word-for-word).
- The new words: only words the learner is meeting for the first time. Give the \
dictionary lemma, and `form` when the dialog uses an inflected form \
(lemma "costar", form "cuesta").
- A brief grammar note in English (3-5 sentences) on the lesson's grammar points.
- Substitution drills. Each has a short model sentence taken from or close to the \
dialog, and cues in the target language. Every answer is a full sentence with \
everything adjusted to agree (gender, number, articles, adjectives, verb forms). \
Use a different model sentence for each drill. Two kinds:
  - mode "gender" (noun-gender practice): the model has one noun with an article or \
determiner that shows gender. Each cue is a BARE singular noun with no article \
("carro", never "el carro"); the answer puts it into the MODEL sentence with the \
right article and agreement. Mix masculine and feminine about evenly and include a \
few that are easy to get wrong (el problema, el mapa, el día, la mano, la foto) \
when they fit the sentence naturally. Cue nouns may be any common, concrete nouns \
at the learner's level; they don't count toward the new-word budget.
  - mode "progressive": each cue changes ONE element of the PREVIOUS answer (the \
first cue changes the model), so the sentence drifts step by step. Vary the part \
of speech: a verb given as its infinitive (cue "querer" turns "Tengo una \
bicicleta." into "Quiero una bicicleta."), a noun ("gato" -> "Quiero un gato."), a \
determiner or quantifier ("algunos" -> "Quiero algunos gatos."), a subject \
pronoun ("nosotros" -> "Queremos algunos gatos."), an adjective, a possessive, a \
number. Every answer must be a natural sentence.
  - Subject pronouns, in every substitution drill: follow the model sentence. If the \
model leaves the subject implicit ("Somos maestros."), no answer states a subject \
pronoun; a pronoun cue changes only the verb ("ustedes" -> "Son maestros."). If the \
model states its subject ("El señor es maestro."), every answer states one ("yo" -> \
"Yo soy maestro."). Never mix the two styles within a drill; use both styles across \
the lesson's drills.
- Translation groups. Each group drills one structure (`pattern`, in English, e.g. \
"need to + verb") with several English prompts that vary the subject, verb, \
object, and polarity (affirmative, negative, question) while keeping the \
structure. Start from dialog lines, then recombine. The first answer is the most \
natural one; add alternates only if common.

Rules:
- title, setting, translations, glosses, grammar_note and prompt_en are English \
only: an English narrator voice reads them aloud. No ¿ ¡ ñ or target-language \
words in them except unavoidable names.
- Stay inside the vocabulary and grammar described in the request. Use the learner's \
known words freely; introduce new words only within the budget.
- Give speakers voice_role values from: female_1, male_1, female_2, male_2. Never narrator.
- Speaker names in English text are pronounced natively automatically. If English \
text uses any other target-language word (a place, a dish), list it in \
`foreign_terms` exactly as written so it's pronounced natively too.
"""

REVIEW_SYSTEM = """\
You are a native-speaker editor and language teacher reviewing a short lesson \
for learners. Check the target-language text for: naturalness in the stated \
dialect (would a native speaker actually say this?), grammatical correctness, \
agreement errors in drill answers, translation accuracy, register consistency \
(tú vs usted), and level-appropriateness. In "progressive" substitution drills \
each cue changes the PREVIOUS answer (the first changes the model); check every \
step. In "gender" drills check every article. Within each substitution drill, answers \
must handle subject pronouns the same way as the model sentence: all implicit if the \
model's subject is implicit, all stated if it is stated; flag a drill that mixes them as \
an error. Report only real problems. \
Use severity "error" for anything wrong or clearly unnatural that should be \
fixed before a learner memorizes it; "minor" for stylistic nitpicks.
"""

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"},
                    "problem": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "severity": {"type": "string", "enum": ["error", "minor"]},
                },
                "required": ["location", "problem", "severity"],
            },
        }
    },
    "required": ["issues"],
}


class GenerationError(RuntimeError):
    pass


@dataclass
class GenerationReport:
    attempts: int = 0
    accepted: bool = False
    issues: list[str] = field(default_factory=list)
    review_minor: list[str] = field(default_factory=list)
    history: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "accepted": self.accepted,
            "needs_review": not self.accepted,
            "remaining_issues": self.issues,
            "review_minor": self.review_minor,
            "history": self.history,
        }


def _strip_discriminators(node: Any) -> Any:
    """Drop pydantic's OpenAPI `discriminator` keyword, which strict JSON Schema validators
    (Claude Code's --json-schema) reject. The `oneOf` branches already differ by their
    `type` const, so nothing is lost."""
    if isinstance(node, dict):
        return {k: _strip_discriminators(v) for k, v in node.items() if k != "discriminator"}
    if isinstance(node, list):
        return [_strip_discriminators(v) for v in node]
    return node


def lesson_tool_schema() -> dict[str, Any]:
    schema = _strip_discriminators(LessonScript.model_json_schema())
    # The pipeline fills these in from the spec.
    for key in ("id", "review", "topic"):
        schema["properties"].pop(key, None)
        if key in schema.get("required", []):
            schema["required"].remove(key)
    schema["$defs"].pop("ReviewItem", None)  # only used by `review`
    return schema


def spec_prompt(spec: LessonSpec) -> str:
    lo, hi = spec.new_word_budget
    parts = [
        f"Target language: {spec.lang} (dialect: {spec.dialect}). Learner level: {spec.level}.",
        f"Topic: {spec.topic}.",
    ]
    if spec.situation:
        parts.append(f"Situation: {spec.situation}")
    if spec.grammar_points:
        syllabus = load_syllabus(spec.lang)
        lines = []
        for g in spec.grammar_points:
            if g in spec.grammar_descriptions:
                lines.append(f"- {g}: {spec.grammar_descriptions[g]}")
            else:
                p = syllabus.get(g)
                lines.append(f"- {g}: {p.title}. {p.description} e.g. {' / '.join(p.examples)}")
        parts.append(
            "Grammar points to feature (tag dialog lines with these ids in grammar_tags):\n"
            + "\n".join(lines)
        )
    parts.append(f"New words: between {lo} and {hi}.")
    if spec.required_words:
        parts.append(f"Must use these words: {', '.join(spec.required_words)}.")
    if spec.known_words:
        parts.append(
            "The learner already knows these words (use them; do not list them as new):\n"
            + ", ".join(spec.known_words)
        )
    else:
        parts.append(
            "Assume a beginner who knows only greetings and the most common function words."
        )
    parts.append(
        f"Dialog: {spec.dialog_lines[0]}-{spec.dialog_lines[1]} lines, "
        f"at most {spec.max_words_per_line} words per line."
    )
    s_lo, s_hi = spec.substitution_items
    t_lo, t_hi = spec.translation_items_per_pattern
    parts.append(
        f"Drills: {spec.gender_drills} gender drill(s) and {spec.progressive_drills} "
        f"progressive drill(s), each with {s_lo}-{s_hi} cues; then "
        f"{spec.translation_patterns} translation groups of {t_lo}-{t_hi} prompts each."
    )
    parts.append("Submit the complete lesson in the required format.")
    return "\n\n".join(parts)


def _spec_hash(spec: LessonSpec, cfg: LLMConfig) -> str:
    # The prompts are part of the key, so changing the instructions regenerates lessons.
    # The backend isn't: the same model gives an equally good lesson.
    payload = json.dumps(
        {
            "spec": spec.model_dump(mode="json"),
            "model": cfg.model,
            "prompts": [SYSTEM_PROMPT, REVIEW_SYSTEM],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


class Generator:
    def __init__(self, cfg: LLMConfig, backend: Backend | None = None) -> None:
        self.cfg = cfg
        self.backend = backend or make_backend(cfg)

    # -- calls -----------------------------------------------------------------

    def review(self, script: LessonScript, spec: LessonSpec) -> list[dict[str, Any]]:
        prompt = (
            f"Dialect: {spec.dialect}. Level: {spec.level}.\n\n"
            f"Lesson JSON:\n{script.model_dump_json(indent=1, exclude={'review'})}"
        )
        data = self.backend.call(
            model=self.cfg.review_model or self.cfg.model,
            system=REVIEW_SYSTEM,
            name=REVIEW_TOOL,
            schema=REVIEW_SCHEMA,
            turns=[("user", prompt)],
            max_tokens=4000,
        )
        return list(data["issues"])

    # -- main loop ---------------------------------------------------------------

    def generate(
        self, spec: LessonSpec, *, use_cache: bool = True, review: bool = True
    ) -> tuple[LessonScript, GenerationReport]:
        cache_path = Path(self.cfg.cache_dir) / f"{_spec_hash(spec, self.cfg)}.json"
        if use_cache and cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            report = GenerationReport(**{k: cached["report"][k] for k in ("attempts", "accepted")})
            return LessonScript.model_validate(cached["script"]), report

        report = GenerationReport()
        turns: list[Turn] = [("user", spec_prompt(spec))]
        best: LessonScript | None = None

        for attempt in range(1, self.cfg.max_attempts + 1):
            report.attempts = attempt
            raw = self.backend.call(
                model=self.cfg.model,
                system=SYSTEM_PROMPT,
                name=LESSON_TOOL,
                schema=lesson_tool_schema(),
                turns=list(turns),
                max_tokens=self.cfg.max_tokens,
            )
            data = {
                **raw,
                "id": spec.id,
                "lang": spec.lang,
                "dialect": spec.dialect,
                "topic": spec.topic,
            }
            if spec.grammar_points:  # the plan decides what the lesson teaches
                data["grammar_points"] = list(spec.grammar_points)
            data["review"] = [r.model_dump() for r in spec.review]

            problems: list[str] = []
            warnings: list[str] = []
            script: LessonScript | None = None
            try:
                script = LessonScript.model_validate(data)
            except ValidationError as e:
                problems = [f"schema: {err['loc']}: {err['msg']}" for err in e.errors()]

            if script is not None:
                best = script
                checks = validate_script(script, spec)
                problems += [str(i) for i in checks if i.severity == "error"]
                warnings = [str(i) for i in checks if i.severity == "warning"]
                if not problems and review:
                    report.review_minor = []
                    for f in self.review(script, spec):
                        line = f"{f.get('location')}: {f.get('problem')}"
                        if f.get("suggestion"):
                            line += f" (suggest: {f['suggestion']})"
                        if f.get("severity") == "error":
                            problems.append(f"[review] {line}")
                        else:
                            report.review_minor.append(line)

            report.history.append(problems)
            report.issues = problems + warnings
            if not problems and script is not None:
                report.accepted = True
                break

            turns += [
                ("assistant", raw),
                (
                    "user",
                    "Please fix these problems and submit the full corrected lesson again:\n- "
                    + "\n- ".join(problems),
                ),
            ]

        if best is None:
            raise GenerationError(
                f"No schema-valid lesson after {report.attempts} attempts: {report.history[-1]}"
            )
        if report.accepted:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {"script": best.model_dump(mode="json"), "report": report.to_dict()},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
        return best, report


def issues_text(issues: list[Issue]) -> str:
    return "\n".join(str(i) for i in issues) or "No issues."
