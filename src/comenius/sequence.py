"""Expand a LessonScript into a Timeline of timed audio segments.

All pacing lives here, in code: backward build-up, graduated interval recall,
learner pauses, and chapter structure. The generator only supplies content.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .config import LessonConfig
from .schema import (
    LANG_NAMES,
    Chapter,
    LessonScript,
    Segment,
    SegmentKind,
    SubstitutionDrill,
    Timeline,
    TranslationDrill,
    VoiceRole,
)
from .tts import terms_in

# (voice, lang, text, rate, native terms, term lang) -> duration in seconds
DurationFn = Callable[[str, str, str, float, Sequence[str], str], float]

NARRATOR: VoiceRole = "narrator"
NARRATOR_LANG = "en"

_SENTENCE_RE = re.compile(r"[^.?!…]+[.?!…]+|[^.?!…]+$")
_STRIP_RE = re.compile(r"[¿¡?!.,;:…\"“”«»()]")


def estimate_duration(
    voice: str, lang: str, text: str, rate: float, terms: Sequence[str] = (), term_lang: str = ""
) -> float:
    """Rough speech-duration estimate (no TTS): ~14 chars/second at rate 1.0."""
    return round(max(0.4, len(text) / 14.0) / max(rate, 0.1), 3)


# ---------------------------------------------------------------------------
# Text helpers


def split_sentences(text: str) -> list[str]:
    parts = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text)]
    return [p for p in parts if p]


def strip_punct(text: str) -> str:
    return " ".join(_STRIP_RE.sub("", text).split())


def normalize_token(token: str) -> str:
    return strip_punct(token).lower()


def tokens(text: str) -> list[str]:
    return [t for t in (normalize_token(w) for w in text.split()) if t]


def fold_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _sentence_starts(words: list[str]) -> list[int]:
    starts = [0]
    for i, w in enumerate(words[:-1]):
        if w.rstrip('"”»)').endswith((".", "?", "!", "…")):
            starts.append(i + 1)
    return starts


def buildup_fragments(line: str, max_steps: int = 4) -> list[str]:
    """Backward build-up of a whole dialog line.

    Fragments grow from the end of the line toward its beginning; the last
    element is the full line. A fragment inside the final sentence is stripped
    of punctuation (so TTS doesn't give it question intonation). A fragment that
    reaches back into an earlier sentence starts at a phrase boundary (sentence
    start or after a comma) and keeps its punctuation.

    >>> buildup_fragments("Buenas tardes. ¿En qué le puedo ayudar?")
    ['puedo ayudar', 'qué le puedo ayudar', '¿En qué le puedo ayudar?', \
'Buenas tardes. ¿En qué le puedo ayudar?']
    """
    words = line.split()
    n = len(words)
    if n <= 3 or max_steps <= 1:
        return [line]
    sentence_starts = _sentence_starts(words)
    last = sentence_starts[-1]
    after_comma = [i + 1 for i, w in enumerate(words[:last]) if w.endswith(",")]
    # Allowed start positions: anywhere in the final sentence, or a phrase
    # boundary in an earlier one.
    allowed = sorted(set(range(last, n)) | set(sentence_starts) | set(after_comma))

    steps = min(max_steps, n - 1)
    starts: set[int] = set()
    for i in range(1, steps):
        target = n - max(2, round(n * i / steps))
        if target <= 0:
            continue
        snapped = min(allowed, key=lambda c: (abs(c - target), -c))
        if 0 < snapped < n - 1 or snapped in sentence_starts[1:]:
            starts.add(snapped)
    if last > 0:
        starts.add(last)  # always teach the final sentence on its own

    frags = []
    for start in sorted(starts, reverse=True):
        if start == 0:
            continue
        if start > last:
            frags.append(strip_punct(" ".join(words[start:])))
        else:
            frags.append(" ".join(words[start:]))
    return [*frags, line]


# ---------------------------------------------------------------------------
# Builder


@dataclass
class _Recall:
    due_s: float
    line_index: int
    prompt_en: str
    answer: str
    voice: VoiceRole


@dataclass
class _Builder:
    script: LessonScript
    cfg: LessonConfig
    duration_of: DurationFn
    segments: list[Segment] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)
    clock: float = 0.0
    current_chapter: str = ""
    pending: list[_Recall] = field(default_factory=list)
    recalled_lines: set[int] = field(default_factory=set)
    last_recalled: int | None = None
    _phrase_i: int = 0

    @property
    def lang(self) -> str:
        return self.script.lang

    def chapter(self, title: str) -> None:
        self.current_chapter = title
        self.chapters.append(Chapter(title=title, start_s=round(self.clock, 3)))

    def native(self, lang: str, text: str) -> list[str]:
        """Names and terms in English narration to pronounce in the lesson's language."""
        if lang != NARRATOR_LANG:
            return []
        return terms_in(text, self.script.native_terms())

    def dur(self, voice: str, lang: str, text: str, rate: float = 1.0) -> float:
        terms = self.native(lang, text)
        return self.duration_of(voice, lang, text, rate, terms, self.script.dialect)

    def say(
        self,
        kind: SegmentKind,
        voice: VoiceRole,
        lang: str,
        text: str,
        *,
        rate: float = 1.0,
        pause_after: float = 0.0,
    ) -> Segment:
        d = self.dur(voice, lang, text, rate)
        terms = self.native(lang, text)
        seg = Segment(
            chapter=self.current_chapter,
            kind=kind,
            voice=voice,
            lang=lang,
            text=text,
            rate=rate,
            terms=terms,
            term_lang=self.script.dialect if terms else "",
            pause_after_s=round(pause_after, 3),
            start_s=round(self.clock, 3),
            duration_s=d,
        )
        self.segments.append(seg)
        self.clock += d + pause_after
        return seg

    def narrate(self, text: str, pause_after: float | None = None) -> None:
        gap = self.cfg.gap_s if pause_after is None else pause_after
        self.say("narration", NARRATOR, NARRATOR_LANG, text, pause_after=gap)

    def guide(self, text: str, pause_after: float | None = None) -> None:
        """Instructional narration, only when guideposts are enabled."""
        if self.cfg.guideposts:
            self.narrate(text, pause_after)

    def section(self, chapter: str, signpost: str) -> None:
        """Start a chapter and, if enabled, announce it."""
        self.chapter(chapter)
        if self.cfg.signposts:
            self.narrate(signpost, pause_after=self.cfg.step_gap_s)

    def learner_pause_for(self, voice: str, text: str, rate: float = 1.0) -> float:
        return self.cfg.learner_pause(self.dur(voice, self.lang, text, rate))

    def prompt_then_answer(
        self,
        kind: SegmentKind,
        prompt_voice: VoiceRole,
        prompt_lang: str,
        prompt: str,
        answer_voice: VoiceRole,
        answer: str,
    ) -> None:
        """Cue, learner pause sized to the answer, then the answer."""
        pause = self.learner_pause_for(answer_voice, answer)
        self.say(kind, prompt_voice, prompt_lang, prompt, pause_after=pause)
        self.say("drill_answer", answer_voice, self.lang, answer, pause_after=self.cfg.step_gap_s)

    def repeat_after_me(self, voice: VoiceRole, text: str, rate: float = 1.0) -> None:
        """Target audio and a pause to repeat, twice, with identical pauses."""
        pause = self.learner_pause_for(voice, text, rate)
        self.say("buildup", voice, self.lang, text, rate=rate, pause_after=pause)
        self.say("buildup", voice, self.lang, text, rate=rate, pause_after=pause)

    # -- recall scheduling ---------------------------------------------------

    def schedule_recalls(self, line_index: int) -> None:
        if not self.cfg.in_lesson_recall:
            return
        line = self.script.dialog[line_index]
        voice = self.script.speaker(line.speaker).voice_role
        for interval in self.cfg.recall_intervals_s:
            self.pending.append(
                _Recall(self.clock + interval, line_index, line.translation, line.text, voice)
            )
        self.pending.sort(key=lambda r: r.due_s)

    def maybe_recall(self, limit: int = 1) -> None:
        """At an insertion point, fire up to `limit` recalls that are due.

        A line is never recalled twice at one insertion point; after a recall,
        any of that line's other entries that have already come due are dropped
        (the recall just served them).
        """
        fired: set[int] = set()
        while len(fired) < limit:
            due = [
                p
                for p in self.pending
                if p.due_s <= self.clock
                and p.line_index not in fired
                and p.line_index != self.last_recalled  # never the same line twice in a row
            ]
            if not due:
                break
            r = due[0]
            self.pending.remove(r)
            self.do_recall(r)
            fired.add(r.line_index)
            self.pending = [
                p
                for p in self.pending
                if not (p.line_index == r.line_index and p.due_s <= self.clock)
            ]

    def do_recall(self, r: _Recall) -> None:
        self.prompt_then_answer(
            "recall", NARRATOR, NARRATOR_LANG, self.recall_phrase(r.prompt_en), r.voice, r.answer
        )
        self.recalled_lines.add(r.line_index)
        self.last_recalled = r.line_index

    def recall_phrase(self, english: str) -> str:
        if not self.cfg.guideposts:
            return english
        templates = (
            "How do you say: {}",
            "Say: {}",
            "Do you remember how to say: {}",
            "How would you say: {}",
        )
        t = templates[self._phrase_i % len(templates)]
        self._phrase_i += 1
        return t.format(english)

    def flush_recalls(self) -> None:
        """Final consolidation: one more recall of each line still pending."""
        seen: set[int] = set()
        remaining = sorted(
            self.pending, key=lambda r: (r.line_index == self.last_recalled, r.line_index)
        )
        self.pending = []
        for r in remaining:
            if r.line_index in seen:
                continue
            seen.add(r.line_index)
            self.do_recall(r)


# ---------------------------------------------------------------------------
# Lesson structure


def _practice_voices(script: LessonScript) -> list[VoiceRole]:
    voices: list[VoiceRole] = []
    for s in script.speakers:
        if s.voice_role != NARRATOR and s.voice_role not in voices:
            voices.append(s.voice_role)
    return voices or ["female_1"]


def _word_in_line(word_forms: list[str], line_text: str) -> bool:
    toks = {fold_accents(t) for t in tokens(line_text)}
    return any(fold_accents(normalize_token(f)) in toks for f in word_forms if f)


def build_timeline(
    script: LessonScript,
    cfg: LessonConfig | None = None,
    duration_of: DurationFn = estimate_duration,
) -> Timeline:
    cfg = cfg or LessonConfig()
    b = _Builder(script=script, cfg=cfg, duration_of=duration_of)
    voices = _practice_voices(script)
    lang = script.lang

    def line_voice(i: int) -> VoiceRole:
        return script.speaker(script.dialog[i].speaker).voice_role

    # 1. Introduction
    b.chapter("Introduction")
    b.narrate(f"{script.title}.")
    b.narrate(script.setting)
    b.clock += cfg.section_gap_s / 2

    # 2. Dialog at natural speed
    b.chapter("Listen")  # no signpost: the English intro leads straight into the dialog
    for i, line in enumerate(script.dialog):
        b.say("dialog", line_voice(i), lang, line.text, pause_after=cfg.line_gap_s)
    b.clock += cfg.section_gap_s

    # 3. Build-up, line by line, with recall woven in
    b.section(
        "Line by line",
        "Now let's build it up, line by line. Repeat each phrase after the speaker.",
    )
    introduced: set[int] = set()
    for i, line in enumerate(script.dialog):
        voice = line_voice(i)
        name = script.speaker(line.speaker).name
        if cfg.line_translations:
            b.narrate(f"{name} says: {line.translation}")
        elif i:
            b.clock += cfg.line_gap_s  # audible break between lines instead

        for w_i, word in enumerate(script.new_words):
            if w_i in introduced or not cfg.introduce_new_words:
                continue
            forms = [word.form or "", word.lemma]
            if _word_in_line(forms, line.text):
                introduced.add(w_i)
                b.narrate(f"The word for '{word.gloss}' is")
                b.repeat_after_me(voice, word.form or word.lemma, cfg.buildup_rate)

        frags = buildup_fragments(line.text, cfg.max_buildup_steps)
        if len(frags) > 1:
            # Hear the whole line once, with a pause to try it, before building it up.
            pause = b.learner_pause_for(voice, line.text)
            b.say("preview", voice, lang, line.text, pause_after=pause)
        for frag in frags[:-1]:
            b.repeat_after_me(voice, frag, cfg.buildup_rate)
        if len(frags) > 1:
            b.guide("Now the whole line.")
        b.repeat_after_me(voice, line.text)

        b.maybe_recall(limit=2)
        b.schedule_recalls(i)

    leftover = [w for w_i, w in enumerate(script.new_words) if w_i not in introduced]
    if leftover and cfg.introduce_new_words:
        b.guide("A few more words.")
        for word in leftover:
            b.narrate(f"The word for '{word.gloss}' is")
            b.repeat_after_me(voices[0], word.form or word.lemma, cfg.buildup_rate)
    b.maybe_recall(limit=2)
    b.clock += cfg.section_gap_s

    # 4. Dialog with pauses (shadowing)
    b.section("Your turn", "Now the whole conversation again. Say each line after you hear it.")
    for i, line in enumerate(script.dialog):
        pause = b.learner_pause_for(line_voice(i), line.text)
        b.say("shadow", line_voice(i), lang, line.text, pause_after=pause)
    b.maybe_recall(limit=2)
    b.clock += cfg.section_gap_s

    # 5. Substitution drills: noun genders, then swap-a-word (simple and progressive)
    subs = [d for d in script.drills if isinstance(d, SubstitutionDrill)]
    sections = [
        (
            "Noun genders",
            "Now swap in a new noun. Say the new sentence, with the right article, "
            "before you hear it.",
            [d for d in subs if d.mode == "gender"],
        ),
        (
            "Swap a word",
            "Now swap in a new word. Each word changes the sentence you just said. "
            "Say the new sentence before you hear it.",
            [d for d in subs if d.mode != "gender"],
        ),
    ]
    d_i = 0
    for chapter, signpost, drills in sections:
        if not drills:
            continue
        b.section(chapter, signpost)
        for drill in drills:
            voice = voices[d_i % len(voices)]
            d_i += 1
            b.guide("Practice this sentence.")
            b.repeat_after_me(voice, drill.model)
            for item in drill.items:
                b.prompt_then_answer("drill_cue", voice, lang, item.cue, voice, item.answer)
                b.maybe_recall(limit=1)
            b.clock += cfg.line_gap_s
        b.clock += cfg.section_gap_s

    # 6. Translation drills: groups of prompts sharing one structure
    trans = [d for d in script.drills if isinstance(d, TranslationDrill)]
    if trans:
        lang_name = LANG_NAMES.get(lang, lang)
        b.section(f"Say it in {lang_name}", f"Now say these in {lang_name}.")
        n = 0
        for drill in trans:
            for item in drill.items:
                voice = voices[n % len(voices)]
                n += 1
                b.prompt_then_answer(
                    "drill_cue", NARRATOR, NARRATOR_LANG, item.prompt_en, voice, item.answers[0]
                )
                b.maybe_recall(limit=1)
            b.clock += cfg.line_gap_s
        b.clock += cfg.section_gap_s

    # 7. Review: earlier lessons, then a final pass over today's lines
    if script.review or b.pending:
        b.section("Review", "A few phrases from earlier lessons.")
        if script.review:
            for r_i, item in enumerate(script.review):
                b.prompt_then_answer(
                    "review",
                    NARRATOR,
                    NARRATOR_LANG,
                    b.recall_phrase(item.prompt_en),
                    voices[r_i % len(voices)],
                    item.answer,
                )
        if b.pending:
            b.guide("And from today's conversation.")
            b.flush_recalls()
        b.clock += cfg.section_gap_s

    # 8. Final listening
    b.section("Listen again", "One last listen. Notice how much more you catch now.")
    for i, line in enumerate(script.dialog):
        b.say("dialog", line_voice(i), lang, line.text, pause_after=cfg.line_gap_s)
    b.guide(f"That's the end of this lesson: {script.title}.", pause_after=0.0)

    return Timeline(
        lesson_id=script.id,
        segments=b.segments,
        chapters=b.chapters,
        duration_s=round(b.clock, 3),
    )
