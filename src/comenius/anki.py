"""Anki deck export (genanki).

Note GUIDs are derived from language + content, so re-exporting a lesson, or
meeting the same word in a later lesson, updates notes instead of duplicating.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import genanki

from .audio import clip_to_mp3
from .schema import LANG_NAMES, NO_CONTEXT, LessonScript, SpeechContext, VoiceRole
from .tts import ClipCache

CSS = """
.card { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 22px;
        text-align: center; color: #1d1d1f; background: #fff; }
.target { font-size: 30px; font-weight: 600; }
.meaning { color: #444; }
.meta { font-size: 13px; color: #888; margin-top: 12px; }
.nightMode .card, .card.nightMode { color: #eee; background: #1e1e1e; }
.nightMode .meaning { color: #bbb; }
"""

VOCAB_MODEL = genanki.Model(
    1_607_392_011,
    "Comenius Vocab",
    fields=[{"name": n} for n in ("Word", "Form", "Meaning", "POS", "Audio", "Lesson")],
    templates=[
        {
            "name": "Recognition",
            "qfmt": '<div class="target">{{Word}}</div>{{Audio}}',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="meaning">{{Meaning}}</div>'
            '<div class="meta">{{#Form}}{{Form}} · {{/Form}}{{POS}}</div>',
        },
        {
            "name": "Production",
            "qfmt": '<div class="meaning">{{Meaning}}</div>',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="target">{{Word}}</div>{{Audio}}'
            '<div class="meta">{{#Form}}{{Form}} · {{/Form}}{{POS}}</div>',
        },
    ],
    css=CSS,
)

SENTENCE_MODEL = genanki.Model(
    1_607_392_012,
    "Comenius Sentence",
    fields=[{"name": n} for n in ("Text", "Translation", "Audio", "Lesson")],
    templates=[
        {
            "name": "Listening",
            "qfmt": '{{Audio}}<div class="meta">Listen. What does it mean?</div>',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="target">{{Text}}</div>'
            '<div class="meaning">{{Translation}}</div>',
        },
        {
            "name": "Production",
            "qfmt": '<div class="meaning">{{Translation}}</div>',
            "afmt": '{{FrontSide}}<hr id="answer"><div class="target">{{Text}}</div>{{Audio}}',
        },
    ],
    css=CSS,
)


def _stable_id(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest()[:8], 16)


def deck_name(lang: str) -> str:
    return f"Comenius::{LANG_NAMES.get(lang, lang)}"


def render_anki(script: LessonScript, cache: ClipCache, out: Path, media_dir: Path) -> Path:
    deck = genanki.Deck(_stable_id("deck", script.lang), deck_name(script.lang))
    media: list[str] = []
    tags = [f"comenius::{script.id}", script.dialect.replace(" ", "_")]

    def audio(role: VoiceRole, text: str, context: SpeechContext = NO_CONTEXT) -> str:
        clip = cache.clip(role, script.lang, text, 1.0, context=context)
        name = f"comenius_{clip.stem}.mp3"
        target = media_dir / name
        clip_to_mp3(clip, target)
        media.append(str(target))
        return f"[sound:{name}]"

    default_voice = next(
        (s.voice_role for s in script.speakers if s.voice_role != "narrator"), "female_1"
    )
    lines = [line.text for line in script.dialog]
    for word in script.new_words:
        # A lone word is voiced after a line that uses it, so it sounds like Spanish.
        forms = {f.lower() for f in (word.lemma, word.form) if f}
        example = next(
            (t for t in lines if forms & {w.strip("¿?¡!.,;:").lower() for w in t.split()}),
            lines[0],
        )
        deck.add_note(
            genanki.Note(
                model=VOCAB_MODEL,
                fields=[
                    word.lemma,
                    word.form or "",
                    word.gloss,
                    word.pos,
                    audio(default_voice, word.lemma, SpeechContext(previous=example)),
                    script.id,
                ],
                guid=genanki.guid_for("comenius", script.lang, "vocab", word.lemma.lower()),
                tags=tags,
            )
        )
    for i, line in enumerate(script.dialog):
        voice = script.speaker(line.speaker).voice_role
        # The same context the lesson uses, so the clip is shared with the audio.
        around = SpeechContext(
            previous=lines[i - 1] if i else "", next=lines[i + 1] if i + 1 < len(lines) else ""
        )
        deck.add_note(
            genanki.Note(
                model=SENTENCE_MODEL,
                fields=[line.text, line.translation, audio(voice, line.text, around), script.id],
                guid=genanki.guid_for("comenius", script.lang, "sentence", line.text),
                tags=tags,
            )
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    genanki.Package(deck, media_files=sorted(set(media))).write_to_file(str(out))
    return out
