"""PDF companion, rendered with Typst.

Lesson data goes in as JSON via sys.inputs, so lesson text is never parsed as
Typst markup (no escaping issues with *, #, _, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

import typst

from .schema import LessonScript, SubstitutionDrill, TranslationDrill

TEMPLATE = r"""
#let d = json(bytes(sys.inputs.data))
#set page(paper: "us-letter", margin: (x: 0.9in, y: 0.8in),
  footer: context [#set text(8pt, fill: luma(120)); Comenius · #d.id #h(1fr) #counter(page).display()])
#set text(size: 10.5pt, lang: "en")
#set par(justify: false, leading: 0.6em)
#show heading.where(level: 1): set text(size: 20pt)
#show heading.where(level: 2): it => { v(0.8em); text(size: 12.5pt, weight: "bold", it.body); v(0.2em) }

= #d.title
#text(fill: luma(90), style: "italic", d.setting)
#v(0.3em)
#text(size: 8.5pt, fill: luma(110))[#d.dialect #if d.grammar_points.len() > 0 [ · #d.grammar_points.join(", ")]]

== Dialog
#table(
  columns: (auto, 1fr, 1fr), stroke: none, inset: (x: 4pt, y: 5pt),
  fill: (_, row) => if calc.odd(row) { luma(245) } else { none },
  ..d.dialog.map(l => (text(weight: "bold", l.name), text(lang: d.lang, l.text),
    text(fill: luma(70), l.translation))).flatten()
)

#if d.new_words.len() > 0 [
== New words
#table(
  columns: (auto, auto, 1fr), stroke: (x, y) => if y == 0 { (bottom: 0.5pt) } else { none },
  inset: (x: 4pt, y: 4pt),
  [*Word*], [*In the dialog*], [*Meaning*],
  ..d.new_words.map(w => (text(lang: d.lang, weight: "bold", w.lemma),
    text(lang: d.lang, w.form), [#w.gloss #text(size: 8pt, fill: luma(120), w.pos)])).flatten()
)
]

#if d.grammar_note != "" [
== Notes
#block(fill: luma(246), inset: 10pt, radius: 3pt, width: 100%, d.grammar_note)
]

#let drill-label = (mode) => if mode == "gender" [Noun genders: give the article] else if mode == "progressive" [Swap a word: each cue changes the previous sentence] else [Swap a word]

#if d.substitution.len() > 0 [
== Substitution drills
#for (i, s) in d.substitution.enumerate() {
  block(breakable: false)[
    *#(i + 1).* #text(lang: d.lang, s.model) #text(fill: luma(90))[— #s.model_translation] \
    #text(size: 8.5pt, fill: luma(110), drill-label(s.mode))
    #enum(..s.items.map(it => text(lang: d.lang, it.cue)))
  ]
}
]

#if d.translation.len() > 0 [
== Translate
#for g in d.translation {
  block(breakable: false)[
    #if g.pattern != "" [*#g.pattern*]
    #enum(..g.items.map(it => it.prompt_en))
  ]
}
]

#if d.review.len() > 0 [
== Review from earlier lessons
#enum(..d.review.map(it => it.prompt_en))
]

#pagebreak()
= Answers
#if d.substitution.len() > 0 [
== Substitution drills
#for (i, s) in d.substitution.enumerate() {
  block(breakable: false)[
    *#(i + 1).* #text(lang: d.lang, s.model)
    #enum(..s.items.map(it => text(lang: d.lang, it.answer)))
  ]
}
]
#if d.translation.len() > 0 [
== Translate
#for g in d.translation {
  block(breakable: false)[
    #if g.pattern != "" [*#g.pattern*]
    #enum(..g.items.map(it => text(lang: d.lang, it.answers.join(" / "))))
  ]
}
]
#if d.review.len() > 0 [
== Review
#enum(..d.review.map(it => text(lang: d.lang, it.answer)))
]
"""


def _data(script: LessonScript) -> str:
    names = {s.id: s.name for s in script.speakers}
    subs = [d for d in script.drills if isinstance(d, SubstitutionDrill)]
    subs.sort(key=lambda d: d.mode != "gender")  # same order as the audio
    trans = [d for d in script.drills if isinstance(d, TranslationDrill)]
    payload = {
        "id": script.id,
        "lang": script.lang,
        "dialect": script.dialect,
        "title": script.title,
        "setting": script.setting,
        "grammar_points": script.grammar_points,
        "grammar_note": script.grammar_note,
        "dialog": [
            {"name": names[line.speaker], "text": line.text, "translation": line.translation}
            for line in script.dialog
        ],
        "new_words": [
            {"lemma": w.lemma, "form": w.form or "", "pos": w.pos, "gloss": w.gloss}
            for w in script.new_words
        ],
        "substitution": [s.model_dump() for s in subs],
        "translation": [t.model_dump() for t in trans],
        "review": [r.model_dump() for r in script.review],
    }
    return json.dumps(payload, ensure_ascii=False)


def render_pdf(script: LessonScript, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = typst.compile(TEMPLATE.encode(), sys_inputs={"data": _data(script)})
    out.write_bytes(pdf)
    return out
