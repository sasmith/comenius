# Comenius — Roadmap

*Last updated: 2026-10-18. Companion to [DESIGN.md](DESIGN.md) and [PLAN.md](PLAN.md).*

Where things stand: a single lesson can be generated from a hand-written spec and rendered
to MP3 (with chapters), PDF, and an Anki deck. Lesson format: listen → line by line
(backward build-up) → your turn → noun genders → swap a word → say it in Spanish → review →
listen again. Nothing yet connects one lesson to the next.

This document lists the next areas of work, what each involves, how they depend on each
other, and a suggested order.

## Dependency map

```
 (7) Learning queue ─────┐
                         ▼
 Syllabus ──────► (1) Lessons that build on what you know ◄──── (2) Anki review signal
                         │   needs: learner state store
                         ▼
                 (3) Auto-delivery ──► podcast · reMarkable · Anki
                         │
                         ▼
                 (8) In-context feedback ──► back into (1)

 (4) Productionizing: scheduled runs, durable state, secrets, costs. Grows alongside 1–3.
 (5) Grammar lessons, (6) more languages, (9) non-language topics: new lesson types on the
     same engine. Easier once (1) defines what a "lesson type" plugs into.
```

---

## 1. Lessons that build on what you've learned

**Goal.** Each new lesson is planned from what you've already seen and how well you know it:
a few new words and one grammar step, plus review of items that are due.

**Pieces.**

- **Long-term syllabus.** `syllabus/es.yaml`: ordered grammar points A1→B1 with stable IDs.
  Built from the Instituto Cervantes curriculum (PCIC) as a checklist, cross-checked against
  the FSI Spanish Basic unit order. Our own wording, not copied text.
- **Vocabulary model.** Frequency bands (wordfreq, SUBTLEX-ESP) and a lemmatizer (spaCy), so
  "cuesta" counts as "costar".
- **Learner state store.** Items you've met (lemmas, phrases, grammar IDs), where and when,
  and current strength. SQLite to start.
- **Planner.** Picks the next grammar point, a new-word budget from the next frequency band
  and your queue (7), and due review items. Emits a `LessonSpec`, so generation stays the
  same.
- **Review in the lesson.** Due items go into the Review section, and the generator is asked
  to reuse them in the dialog and drills.

**Signal on what needs review**, from weakest to strongest:

1. *Exposure only:* items are scheduled on an expanding schedule (lesson n+1, n+3, n+7…).
   Works with no input from you.
2. *Anki performance (2):* real recall data per item.
3. *Your feedback (8):* "I keep missing *llanta*."

**Open questions.** Is a lesson a unit of the syllabus, or a topic that pulls syllabus
items in? How much review per lesson? (Initial guess: 20–30% of drill items.)

**Size.** 3–4 weekends for syllabus, state, and planner on signal 1. Signal 2 depends on (2).

## 2. Anki

**Goal.** Cards for each lesson appear in your Anki collection automatically, and your
review results flow back to the planner.

**Pieces.**

- **Export (exists).** `.apkg` per lesson with stable note IDs and audio.
- **Import without clicking.** AnkiConnect (an add-on that exposes a local API) can import
  decks and read review history. It needs Anki desktop running on some always-on machine.
  AnkiWeb has no public API.
- **Read back.** Per note: due date, ease, lapses. Map notes to learner-state items through
  the stable IDs.
- **Card design.** Which cards earn their keep: listening (audio→meaning), production
  (meaning→say it), gender (noun→article), cloze from the dialog.

**Open questions.** Which machine runs Anki desktop? Is a review loop that only syncs when
the laptop is open good enough?

**Size.** 1–2 weekends.

## 3. Auto-delivery

**Goal.** A new lesson shows up everywhere it's used, with no manual steps.

| Output | Destination | Mechanism | Notes |
|---|---|---|---|
| MP3 | Podcast app | `comenius feed` + upload to S3/R2 under a private path | Feed builder exists; add the upload and a stable URL |
| PDF | reMarkable | `rmapi` (open-source CLI for the reMarkable cloud) | Unofficial API; may break. Put lessons in a "Comenius" folder |
| Anki deck | Anki | AnkiConnect `importPackage` | See (2) |

**Trigger.** A scheduled job (for example nightly or on demand) that plans, generates,
renders, and delivers. Where it runs is part of (4).

**Size.** 1 weekend for the podcast and reMarkable, more if Anki import needs an always-on
host.

## 4. Productionizing

**Goal.** Comenius runs on its own, reliably and cheaply, without a laptop session.

**Stages.**

1. **Personal production.** A scheduled job (GitHub Actions cron or a small VM),
   learner state in a durable store (SQLite on persistent disk, or Postgres), secrets in a
   secret manager, the TTS cache in object storage, and run logs with cost per lesson
   (LLM and TTS).
2. **Robustness.** Retries on provider errors. A "needs review" queue for lessons that fail
   validation, instead of publishing them. Alerts when a scheduled run fails.
3. **Multi-user** (only if wanted). Accounts, per-user state and feeds, and a web UI for
   the queue and feedback. A different scale of work. Don't do this before the personal
   loop has proven itself.

**Open questions.** Where does Anki desktop live if it must be always on? Is a monthly
budget cap needed?

## 5. Other lesson types: explicit grammar

**Goal.** Some lessons teach a structure directly, in addition to dialog lessons.

**Ideas.**

- A short English explanation (30–60 s) with examples, then drills that isolate the point.
- **Transformation drills** (FSI-style): present→past, affirmative→negative,
  statement→question, tú→usted.
- **Conjugation drills**: verb + subject → form, cycling through persons.
- **Response drills**: a question in Spanish, answered in Spanish with a given cue.
- **Contrast drills** for confusable pairs: ser/estar, por/para, preterite/imperfect.

**Design.** A lesson type is a schema plus a sequencer. Dialog lessons are the first type;
grammar lessons add new drill modes and skip the build-up section. Grammar lessons come
from the syllabus (1) when a point needs more than the dialog can show.

**Size.** 1–2 weekends for the first grammar lesson type.

## 6. More languages

**Generic per language:** syllabus file, frequency data, lemmatizer, TTS voices, dialect
choice, and the language name in signposts.

**Language-specific work:**

- **Russian:** case drills (noun + preposition → case form), verb aspect pairs, and stress
  marks in the PDF (TTS stress errors are common; may need stress-marked input). Gender
  drills extend to three genders.
- **Chinese (Mandarin):** tones (tone-pair drills, minimal pairs), characters and writing
  (PDF worksheets with stroke-order grids for reMarkable; stroke data from open sources such
  as Make Me a Hanzi), pinyin alongside characters, measure-word drills in place of gender.
- **Japanese:** script progression (kana→kanji), politeness levels, particle drills.
- **French, Italian, Portuguese:** close to Spanish. Mostly syllabus, voices, and gender
  rules.

**Size.** A Romance language: 1–2 weekends. Russian or Chinese: several weekends each,
mostly for language-specific drills.

## 7. Learning queue

**Goal.** You keep a stack of things you want to work toward, and the planner draws lesson
topics and vocabulary from it.

**Kinds of entries.**

- **Situations:** "navigating the airport", "a parent-teacher conference". These become a
  short series of dialog lessons.
- **Books:** "read *[title]*". Coverage-driven vocabulary (the target-book mode in
  PLAN M7).
- **Grammar wishes:** "I want to get the subjunctive". These bias the syllabus order.
- **Words or phrases** you ran into in real life.

**Interface.** Start with a `queue.yaml` in the repo, or a CLI (`comenius queue add …`).
Later, entries could come from (8), such as a voice note saying "add: ordering at a
pharmacy".

**Size.** 1 weekend on top of (1).

## 8. In-context feedback

**Goal.** Give feedback where you are, while running, driving, or reading the PDF, and have
it change future lessons.

**Channels.**

- **Voice.** A phone shortcut records a short note ("lesson 7, the line about the tire was
  too fast") and uploads it. Whisper transcribes it, and an LLM maps it to a lesson and
  item. Podcast apps don't report playback position, so notes name the lesson or are
  matched by time of day.
- **Handwriting on the PDF.** Pull annotated PDFs back from reMarkable (`rmapi`). A vision
  model reads marks: circled words, "?" next to a drill, corrections.
- **Explicit buttons** later (web or phone): "too easy", "too hard", "wrong".

**What feedback changes:**

- Item strength (marks a word weak)
- Pacing (`pause_multiplier`)
- Difficulty (new-word budget)
- Content errors (sent to the reviewer prompt, and the lesson regenerated)
- Interests (added to the queue)

**Size.** 2+ weekends. Voice is the easier start.

## 9. Non-language topics

**Goal.** Use the same engine (audio, retrieval practice, spaced review, PDF, Anki) for
other material that rewards recall.

**Candidates:**

- Memorizing a speech or poem (build-up works directly)
- Technical vocabulary for a field
- Morse code (audio-native)
- History or science told as short narratives with retrieval questions
- Exam-style fact sets

**Design.** Generalize "lesson type" from (5): content schema, sequencer, validator,
renderers. Language lessons become one plugin. Learner state is keyed by
topic-specific item IDs.

**Size.** The refactor is about 1 weekend once (5) has forced the abstraction. Each new
topic type is its own work.

---

## Suggested order

| Phase | Work | Outcome |
|---|---|---|
| **A. The personal loop** | Syllabus + learner state + planner (1, exposure signal) · podcast upload (3) · minimal queue (7) | A new lesson each day, building on the last, in your podcast app |
| **B. Real signal** | Anki import and readback (2) · reMarkable delivery (3) · voice feedback (8) · scheduled job (4.1) | Review is driven by how you actually did. Runs without you |
| **C. Breadth** | Grammar lessons (5) · PDF feedback (8) · second language (6) | More lesson variety; the lesson-type abstraction proves out |
| **D. Beyond** | Russian/Chinese specifics (6) · non-language topics (9) · multi-user (4.3) | Only after A–C are in daily use |

Phase A is the next useful step: it turns single lessons into a course. Start with the
syllabus file and the learner state store, since everything else reads from them.
