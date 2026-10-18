"""Deterministic checks on a LessonScript.

Errors are fed back to the generator for regeneration; warnings are reported.
Lexical checks here are token-level (no lemmatizer yet); M3 adds spaCy-based
lemma checks against the learner's known vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .schema import LessonScript, LessonSpec, SubstitutionDrill, TranslationDrill
from .sequence import fold_accents, normalize_token, tokens

Severity = Literal["error", "warning"]

# English-only fields are read by the English narrator voice.
_NON_ENGLISH_MARKS = set("¿¡ñÑ")

# A small allowlist of Spanish function words assumed known from lesson one.
FUNCTION_WORDS_ES = set(
    """
    a al con de del el en la las lo los mi mis no o para pero por que qué quién su sus
    te tu tú un una unos unas y ya yo me se le les nos es son está están hay muy sí bien
    """.split()
)


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


def _folded_tokens(text: str) -> set[str]:
    return {fold_accents(t) for t in tokens(text)}


def _contains(text: str, phrase: str) -> bool:
    """Accent- and case-insensitive whole-token phrase containment."""
    hay = " " + " ".join(fold_accents(t) for t in tokens(text)) + " "
    needle = " " + " ".join(fold_accents(t) for t in tokens(phrase)) + " "
    return needle.strip() != "" and needle in hay


def validate_script(script: LessonScript, spec: LessonSpec | None = None) -> list[Issue]:
    issues: list[Issue] = []

    def err(code: str, msg: str) -> None:
        issues.append(Issue("error", code, msg))

    def warn(code: str, msg: str) -> None:
        issues.append(Issue("warning", code, msg))

    # English-only fields
    english_fields: list[tuple[str, str]] = [("title", script.title), ("setting", script.setting)]
    english_fields += [
        (f"dialog[{i}].translation", d.translation) for i, d in enumerate(script.dialog)
    ]
    english_fields += [(f"new_words[{w.lemma}].gloss", w.gloss) for w in script.new_words]
    for drill in script.drills:
        if isinstance(drill, TranslationDrill):
            english_fields += [("translation.prompt_en", it.prompt_en) for it in drill.items]
    for where, text in english_fields:
        if _NON_ENGLISH_MARKS & set(text):
            err("non_english_text", f"{where} must be English only (read by narrator): {text!r}")

    # New words appear in the dialog
    dialog_text = " ".join(d.text for d in script.dialog)
    dialog_tokens = _folded_tokens(dialog_text)
    for w in script.new_words:
        forms = [w.form, w.lemma]
        if not any(f and fold_accents(normalize_token(f)) in dialog_tokens for f in forms):
            warn(
                "new_word_not_in_dialog",
                f"{w.lemma!r} (form {w.form!r}) not found in dialog; set 'form' to the dialog form",
            )

    # Drills
    for d_i, drill in enumerate(script.drills):
        if isinstance(drill, SubstitutionDrill):
            if len(drill.items) < 2:
                warn("short_drill", f"substitution drill {d_i} has fewer than 2 items")
            for item in drill.items:
                if fold_accents(item.answer.lower()) == fold_accents(drill.model.lower()):
                    err("substitution_unchanged", f"answer equals model: {item.answer!r}")
                if not _contains(item.answer, item.cue):
                    warn(
                        "cue_not_in_answer",
                        f"cue {item.cue!r} not found in answer {item.answer!r}",
                    )
        elif isinstance(drill, TranslationDrill):
            for item in drill.items:
                if not item.answers[0].strip():
                    err("empty_answer", f"translation {item.prompt_en!r} has an empty answer")

    if spec is None:
        return issues

    # Spec constraints
    lo, hi = spec.dialog_lines
    if not lo <= len(script.dialog) <= hi:
        err("dialog_length", f"dialog has {len(script.dialog)} lines; need {lo}-{hi}")
    for i, line in enumerate(script.dialog):
        n = len(line.text.split())
        if n > spec.max_words_per_line:
            err("line_too_long", f"dialog[{i}] has {n} words (max {spec.max_words_per_line})")
    lo, hi = spec.new_word_budget
    if not lo <= len(script.new_words) <= hi:
        err("new_word_budget", f"{len(script.new_words)} new words; budget is {lo}-{hi}")
    lemmas = {fold_accents(w.lemma.lower()) for w in script.new_words}
    for req in spec.required_words:
        if fold_accents(req.lower()) not in lemmas and not _contains(dialog_text, req):
            err("missing_required_word", f"required word {req!r} is not used")
    subs = sum(isinstance(d, SubstitutionDrill) for d in script.drills)
    if subs < spec.substitution_drills:
        err(
            "too_few_substitution_drills",
            f"{subs} substitution drills; need {spec.substitution_drills}",
        )
    n_trans = sum(len(d.items) for d in script.drills if isinstance(d, TranslationDrill))
    if n_trans < spec.translation_items:
        err(
            "too_few_translation_items",
            f"{n_trans} translation items; need {spec.translation_items}",
        )
    allowed = set(spec.grammar_points)
    for i, line in enumerate(script.dialog):
        extra = set(line.grammar_tags) - allowed
        if allowed and extra:
            warn("unplanned_grammar_tag", f"dialog[{i}] tags {sorted(extra)} not in spec")

    # Known-vocabulary check (token-level until lemmatization lands; warnings only)
    if spec.known_words:
        known = {fold_accents(w.lower()) for w in spec.known_words}
        known |= {fold_accents(w) for w in FUNCTION_WORDS_ES}
        known |= lemmas | {fold_accents((w.form or "").lower()) for w in script.new_words}
        names = {fold_accents(s.name.lower()) for s in script.speakers}
        names |= {fold_accents(p.lower()) for s in script.speakers for p in s.name.split()}
        unknown = sorted(t for t in dialog_tokens - known - names if not t.isdigit())
        if unknown:
            warn("possibly_unknown_tokens", f"tokens not in known/new words: {unknown}")

    return issues
