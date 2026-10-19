# Comenius — Implementation Plan

*Companion to [DESIGN.md](DESIGN.md). Last updated: 2026-10-17.*

The plan is ordered so that the riskiest question — *does a generated,
build-up-style audio lesson actually work on a run?* — is answered first,
with the least code. Each milestone ends with something to listen to.

## Status

| Milestone | State | Notes |
|---|---|---|
| M0 Scaffold | Done | uv, Typer CLI, ruff, pytest, CI |
| M1 Lesson → MP3 | Done, pending real-voice bake-off | Sequencer, TTS providers (espeak, Kokoro, Azure, OpenAI, ElevenLabs), clip cache, MP3 with chapters. espeak and Kokoro are tested end to end; the paid providers need keys to verify. |
| M2 LLM generation | Done, untested against the live API | Forced tool-call JSON, code checks, native-speaker review pass, regeneration loop, cache. Loop is tested with a fake client. |
| M3 Curriculum constraints | Partial | Syllabus done (`src/comenius/data/syllabus/es.yaml`, 59 points A1→B1, `comenius syllabus`), used by generation and validation. Still to do: spaCy lemmatization, frequency bands, planner. |
| M4 PDF + Anki | Done | Typst PDF with answer page; genanki deck with stable GUIDs and audio. |
| M5 Podcast feed | Done | `comenius publish` uploads changed files to S3/R2 (feed last); `generate --publish`. Tested against a mock S3; not yet against a real bucket. |
| M6 Learner state | Partial | SQLite state (`comenius state add/show/assume`), lesson-count review schedule, and `comenius plan` (next syllabus point + known words + due reviews). Still to do: Anki review signal, lemmatized word tracking. |
| M7 Target-book mode | Not started | |

## Principles

- **Listen early.** Every milestone exits with lessons used on real runs or
  drives, not just passing tests.
- **Content in JSON, pacing in code.** The LLM writes content; deterministic
  code decides timing, recall, and pauses, so tuning never requires
  regenerating content.
- **Cache everything expensive.** TTS segments and LLM outputs are keyed by
  content hash and stored on disk.
- **One learner first.** No accounts, no server beyond static file hosting.

## Repository layout (target)

```
comenius/
  README.md
  pyproject.toml
  docs/
    DESIGN.md
    PLAN.md
  src/comenius/
    cli.py              # Typer entry point
    config.py           # learner + project config (TOML)
    schema/             # LessonSpec, LessonScript, Timeline (pydantic) + JSON Schema export
    planner/            # builds LessonSpec from state, syllabus, vocab bands
    generate/           # LLM prompts, generation, regeneration loop
    validate/           # schema, lexical, grammar, naturalness checks
    sequence/           # build-up, recall scheduler, pause model → Timeline
    render/
      tts/              # provider interface + implementations + cache
      audio.py          # assembly, normalization, chapters
      pdf/              # Typst templates
      anki.py           # genanki export
      feed.py           # RSS generation + upload
    state/              # SQLite models, FSRS, Anki sync
    nlp/                # spaCy lemmatizer wrapper, frequency bands, coverage
  syllabus/
    es.yaml             # grammar points with stable IDs
  data/                 # frequency lists, allowlists (gitignored if large)
  fixtures/
    lessons/es-0001.json   # hand-written golden lesson
  tests/
  out/                  # generated lessons (gitignored)
```

## Milestones

### M0 — Scaffold (½ day)

- `uv` project, Python 3.12, ruff, pytest, pre-commit.
- Typer CLI skeleton with `comenius --help`.
- Config file (`comenius.toml`): target language, dialect, pause multiplier,
  output dir, provider keys via env vars.
- GitHub Actions: lint + tests.

**Exit:** `uv run comenius --help` works; CI green.

### M1 — Hand-written lesson → MP3 (1–2 weekends)

The core experiment. No LLM yet.

1. Pydantic models for `LessonScript` and `Timeline`; export JSON Schema.
2. Hand-write `fixtures/lessons/es-0001.json`: a short (6–8 line) dialog,
   new words, two substitution drills, translation drills.
3. **Sequencer**:
   - Backward build-up splitter (split on word/phrase boundaries from the
     end; configurable max fragment growth).
   - Recall scheduler: re-prompt phrases at ~5 s, 25 s, 2 min, 10 min after
     introduction, inserted at section boundaries nearest those targets.
   - Pause model: `pause = mult × (1.5 × answer_duration + 1.0 s)`; needs
     answer audio duration, so the sequencer runs after TTS duration lookup.
   - Chapter assignment.
4. **TTS bake-off**: implement the provider interface for 2–3 candidates
   (Azure Neural, ElevenLabs, OpenAI). Render the fixture with each; compare
   naturalness, fragment prosody, slow-rate quality, and cost. Also try
   "render full sentence, slice at word timestamps" for build-up fragments.
5. **Audio assembly**: concatenate segments with silences, loudness-normalize
   (ffmpeg `loudnorm`), write chapter markers.
6. Golden test: fixture → timeline JSON snapshot.

**Exit:** a 15–25 min MP3 from the fixture, listened to on at least two runs
and one drive. Decide TTS provider and default pause multiplier.

### M2 — LLM generation (1 weekend)

1. `LessonSpec` model; for now hand-specified via CLI flags or a YAML file
   (topic, grammar points, allowed new words, known words).
2. Generation prompt producing `LessonScript` JSON (structured output);
   few-shot with the M1 fixture.
3. Naturalness/correctness review prompt (pinned dialect); returns flagged
   lines with reasons.
4. Regeneration loop: feed schema errors and review flags back; max 3
   attempts; otherwise write the draft with a `needs_review` flag.
5. Cache LLM responses by spec hash.
6. `comenius generate --spec spec.yaml` → script JSON; `comenius render` →
   MP3.

**Exit:** five generated lessons on different topics; each listened to;
note quality issues in a running log (`docs/notes/quality-log.md`).

### M3 — Curriculum constraints (1–2 weekends)

1. Author `syllabus/es.yaml`: A1 → A2 grammar points with stable IDs, using
   the PCIC as a checklist and FSI Spanish Basic unit order as a cross-check.
   (Our own wording; don't copy PCIC text.)
2. NLP: spaCy lemmatizer wrapper; function-word and cognate allowlists;
   proper-noun handling.
3. Frequency bands from `wordfreq` (and SUBTLEX-ESP if obtained).
4. Validator checks: lexical budget (unknown lemmas ⊆ declared new words),
   new-word count, grammar tags ⊆ introduced + current IDs; second-pass
   LLM spot-check of grammar tags.
5. Planner v1: given a "known" word set and the next syllabus point, choose
   the grammar target and a new-word budget from the next frequency band
   plus topic words.

**Exit:** a sequence of 10 lessons generated by `comenius plan && comenius
generate` with zero out-of-budget lemmas after validation; metrics logged
per lesson.

### M4 — Companion outputs (1 weekend)

1. Typst PDF template: dialog + translations, vocabulary table, grammar
   note, drills with answers on a separate page, review list.
2. Anki export via genanki: recognition, production, and audio-only cards;
   reuse cached TTS clips; stable GUIDs per item.
3. `comenius render --all` produces `lesson.mp3`, `lesson.pdf`,
   `lesson.apkg`.

**Exit:** PDF reads well on a phone and printed; deck imports into Anki
desktop and AnkiMobile with working audio.

### M5 — Podcast feed delivery (½–1 weekend)

1. RSS generator: one episode per lesson, show notes linking the PDF,
   chapter support.
2. Publish step: upload MP3/PDF/feed to S3 or R2 under a long random path.
3. Subscribe in a podcast app; verify CarPlay and lock-screen chapters.

**Exit:** new lessons appear automatically in the podcast app on the phone.

### M6 — Learner state and review recycling (1–2 weekends)

1. SQLite schema (`items`, `exposures`, `lessons`, `reviews`).
2. Record items introduced per lesson; assumed expanding review schedule
   (lesson n+1, n+3, n+7, …).
3. Planner pulls due review items into the spec; sequencer places a review
   block plus scattered callbacks.
4. Anki sync (AnkiConnect, desktop): read review history for Comenius
   notes; update item SRS state and "known" status.
5. `comenius status`: known-word count, due items, lesson history.

**Exit:** a week of daily lessons where review items are visibly drawn from
earlier lessons, and Anki performance changes what gets reviewed.

### M7 — Target-book mode (1 weekend)

1. Ingest a book (EPUB/plain text); lemmatize; build lemma frequency table.
2. Coverage report against known items; unknown lemmas ranked by in-book
   frequency × general frequency, excluding names.
3. Planner option: steer new-word budget toward the book's top unknowns.
4. `comenius book report` shows coverage over time.

**Exit:** coverage report for one real book, and lessons whose new words
measurably raise its coverage.

## Later (unscheduled)

See [ROADMAP.md](ROADMAP.md) for the fuller list and suggested order.


- Player app with headphone-button binary grading (FSRS) and optional speech
  recognition against expected answers.
- Pronunciation assessment on recorded responses, reviewed after the session.
- FSI import pipeline (OCR + WhisperX alignment) for drills and Anki cards.
- Second language (new syllabus file, dialect, voices).
- Known-vocabulary-only listening stories.

## Decisions needed before / during M1

| Decision | When | Default if undecided |
|---|---|---|
| Dialect (es-MX vs es-ES) | Before fixture | es-MX |
| TTS provider | End of M1 bake-off | Best Spanish quality under ~$1/lesson |
| Starting level / seed vocabulary | Before M3 | True A1, empty known set plus function words |
| Lesson length | End of M1 | ~20 min; revisit after runs |
| PDF tooling | M4 | Typst |

## Effort summary

Roughly 8–12 weekends to M7 for one person working with coding agents, with
M1 (the listening experiment) reachable in the first two weekends. The long
tail is content quality — pacing, naturalness, curriculum fit — which is
tuned by listening, not by code volume.
