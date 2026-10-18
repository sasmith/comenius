# Comenius — Design

*Status: draft · Author: Alex · Last updated: 2026-10-17*

Comenius generates personalized, audio-first language lessons for listening
while running, driving, or commuting. Each lesson starts from a short written
dialog in the target language and becomes a spoken lesson in the tradition of
FSI and Pimsleur: the dialog is taught by backward build-up, then drilled with
substitution and translation practice, with earlier material recalled at
increasing intervals. Every lesson also produces a PDF companion and Anki
flashcards from the same source.

Named for Jan Amos Comenius (1592–1670), the "father of modern education,"
whose *Janua Linguarum* was one of the most widely used language textbooks
in history.

---

## 1. Goals

1. **Audio lessons that work hands- and eyes-free.** A lesson is a single
   audio file (with chapters) that can be followed start to finish while
   running or driving, with no screen interaction.
2. **Better than the fixed courses it borrows from.** FSI audio is effective
   but formulaic and dated; Pimsleur is polished but fixed. Comenius lessons
   are generated for the learner: modern language, chosen topics, and
   vocabulary sequenced against what the learner already knows.
3. **Anchored to a real syllabus.** Lessons follow an explicit grammar
   sequence and vocabulary progression, enforced in code, so generated content
   doesn't drift.
4. **One source, many outputs.** A structured lesson script is the single
   source of truth for the audio, PDF companion, and Anki deck.
5. **Cheap to try.** The first useful version is a command-line pipeline that
   emits MP3s and a podcast feed — no app to build before learning whether the
   format works.

## 2. Non-goals (for now)

- **Pronunciation scoring.** Tools exist (phoneme-level assessment engines),
  but they're unreliable on noisy, breathless audio and only middling in
  general. Wishlist item; see §11.
- **Graded spoken responses during lessons.** The learner answers aloud into
  the pauses; nothing listens. In-lesson grading is a later "player app" item.
- **A mobile app.** Delivery is via any podcast app (private RSS feed).
- **Multi-user product concerns.** Accounts, billing, content moderation at
  scale. Single learner first.
- **Reproducing copyrighted course material.** Syllabus references (e.g. the
  Plan Curricular del Instituto Cervantes) are used as checklists for our own
  curriculum, never copied into outputs.

## 3. Background and prior art

| Source | What we take from it | Limitation |
|---|---|---|
| FSI courses (public domain, 1960s–80s) | Dialog-first units; backward build-up; substitution and transformation drills; unit order as a grammar backbone | Dated content; fixed; long monotone audio; no text/audio alignment |
| Pimsleur | Graduated interval recall inside a lesson; English narrator prompting recall; strict new-item budget per lesson | Fixed script; narrow vocabulary; expensive |
| Anki / FSRS | Spaced repetition scheduling across days | Not hands-free; grading requires the screen |
| Dicendi's FSI Spanish e-books | Proof that dialog + per-line audio + drills with hidden answers works well as a companion | Manual, single course |

The core insight: Pimsleur's in-lesson recall plus FSI's dialog-and-drill
structure, generated per learner against an explicit syllabus and their known
vocabulary.

## 4. Learner experience

### 4.1 During a run or drive (audio)

A typical 10–20 minute lesson. Each section is announced with a short English
signpost ("Dialog. Listen.", "Dialog. Repeat to learn.", "Dialog. Repeat for
mastery.", "Substitution drills.", "Translation drills."):

1. **Intro** (narrator, English): the situation and who's speaking.
2. **Dialog at natural speed**, once, in two distinct voices.
3. **Repeat to learn**: backward build-up of each whole line, with no English:
   > *puedo ayudar* … *qué le puedo ayudar* … *¿En qué le puedo ayudar?* …
   > *Buenas tardes. ¿En qué le puedo ayudar?*

   Build-up runs across the whole line, reaching into earlier sentences at
   phrase boundaries. Each fragment: target audio → pause to repeat → target
   audio again → short pause before the next, longer fragment.
   Meaning comes from context, the intro, and the PDF; there are no English
   line translations, new-word glosses, or instructions like "now the whole
   line" (each can be turned back on in config).
4. **Repeat for mastery**: the full dialog again, with a pause after each line
   to say it yourself.
5. **Substitution drills**: a model sentence, then cue words to swap in;
   learner produces the new sentence in the pause; correct answer follows.
6. **Translation drills**: English prompt → pause → target-language answer.
7. **No separate in-lesson recall by default.** Build-up, repeat-for-mastery,
   and the drills already retrieve each line several times at growing gaps;
   the spacing that matters more is across days (replaying a lesson, and
   review items in later lessons). Pimsleur-style recall (English cue at
   ~5 s, 25 s, 2 min, 10 min) is available with `in_lesson_recall = true`.
8. **Review block**: items from earlier lessons that are due, prompted
   English → target.
9. **Dialog a final time**, natural speed, no pauses.

Design constraints for this setting:

- **No screen needed, ever.** Driving with kids in the car is a primary
  context. The audio is linear; skipping uses the podcast app's normal
  controls.
- **Pauses are generous and tunable.** Default pause =
  `1.5 × answer_audio_duration + 1.0 s`, with a global multiplier per
  learner (running needs more).
- **Chapters** for each section, so "jump to drills" works from a car's
  head unit or the lock screen.

### 4.2 Afterward (screen)

- **PDF companion**: dialog with translations, vocabulary table, grammar
  note, drills with answers (answers on a separate page or folded), and a
  list of review items.
- **Anki deck**: new vocabulary and full dialog lines as cards, with the
  same rendered audio clips. Card types: target→English (recognition),
  English→target (production), and audio-only listening cards.

## 5. Architecture

```
                 ┌──────────────────────────┐
  learner state ─┤                          │
  syllabus ──────┤   Lesson planner         │  picks grammar points, new
  vocab bands ───┤                          │  words, review items, topic
  target book ───┤                          │
                 └────────────┬─────────────┘
                              │ LessonSpec
                 ┌────────────▼─────────────┐
                 │   Generator (LLM)        │  writes dialog + drills
                 └────────────┬─────────────┘
                              │ draft LessonScript
                 ┌────────────▼─────────────┐
                 │   Validator              │  schema, lemmas vs budget,
                 │   (code + LLM review)    │  grammar tags, naturalness
                 └───┬──────────────────┬───┘
            reject + │                  │ accept
            feedback └──► regenerate    │
                              ┌─────────▼─────────┐
                              │  Sequencer        │  expands script into a
                              │                   │  timeline of segments
                              └─────────┬─────────┘
                                        │ Timeline
            ┌───────────────┬───────────┼────────────┬──────────────┐
            ▼               ▼           ▼            ▼              ▼
       TTS + audio      PDF render   Anki deck   Podcast feed   State update
       assembly (MP3)   (Typst)      (genanki)   (RSS + MP3)    (SQLite)
```

### 5.1 Components

**Lesson planner** — deterministic code. Given learner state, the syllabus,
and config, produces a `LessonSpec`: target grammar point(s), allowed new
words (a budget, e.g. 8–12), known vocabulary, due review items, topic and
setting, and dialect.

**Generator** — an LLM call (Claude, via the Anthropic API) with the
`LessonSpec`, returning a `LessonScript` as JSON conforming to a schema.
The model writes the dialog, glosses, grammar note, and drill content.
It does **not** decide timing or recall scheduling — that's the sequencer's
job, in code.

**Validator** — rejects drafts that break constraints and feeds the
specific violations back into a regeneration prompt (max N attempts, then
flag for human review):

- JSON schema validity.
- **Lexical budget**: lemmatize all target-language text (spaCy); every lemma
  must be known, in the lesson's declared new words, a proper noun, or on an
  allowlist (function words, transparent cognates). New-word count within
  budget.
- **Grammar budget**: the generator tags each line with syllabus grammar IDs;
  code checks the IDs are introduced-or-current. (Imperfect — tags are
  self-reported — so a second LLM pass spot-checks the tags.)
- **Naturalness review**: a separate LLM pass, prompted as a native-speaker
  editor for the pinned dialect, flags stilted or unidiomatic lines.
- **Drill correctness**: substitution outputs re-derived and checked for
  agreement (gender, number, verb form) by the review pass.

**Sequencer** — deterministic code. Expands a `LessonScript` into a
`Timeline`: an ordered list of segments, each `{voice, lang, text,
pause_after, chapter}`. Implements backward build-up splitting, recall
scheduling, review-block placement, and pause computation. Keeping this in
code makes pacing tunable without regenerating content.

**Renderers**
- *Audio*: TTS per unique `(voice, text, rate)` segment, cached by content
  hash; silences inserted; concatenated and loudness-normalized (ffmpeg /
  pydub); chapter markers written (ID3 CHAP or M4B).
- *PDF*: Typst template fed the `LessonScript` JSON.
- *Anki*: genanki package; reuses cached segment audio; stable note GUIDs
  so re-exports update rather than duplicate.
- *Feed*: static RSS file plus MP3s uploaded to a private, unguessable URL
  (S3/R2 with a long random path). Subscribe once in Overcast / Apple
  Podcasts; new lessons appear automatically, including in CarPlay.

**Learner state** — SQLite. See §7.

### 5.2 Data model

`LessonScript` (generator output, abbreviated):

```json
{
  "id": "es-0007",
  "lang": "es",
  "dialect": "es-MX",
  "title": "At the bike shop",
  "setting": "Ana brings her bike in for a flat tire.",
  "speakers": [
    {"id": "A", "name": "Ana", "voice_role": "female_1"},
    {"id": "B", "name": "Mecánico", "voice_role": "male_1"}
  ],
  "grammar_points": ["pcic.a1.presente.regulares", "pcic.a1.necesitar+inf"],
  "new_words": [
    {"lemma": "llanta", "pos": "NOUN", "gloss": "tire", "example_line": 2}
  ],
  "dialog": [
    {"speaker": "A", "text": "Buenas tardes. Necesito arreglar mi bici.",
     "translation": "Good afternoon. I need to fix my bike.",
     "grammar_tags": ["pcic.a1.necesitar+inf"]}
  ],
  "grammar_note": "…",
  "drills": [
    {"type": "substitution",
     "model": "Necesito arreglar mi bici.",
     "items": [{"cue": "coche", "answer": "Necesito arreglar mi coche."}]},
    {"type": "translation",
     "items": [{"prompt_en": "I need to fix my tire.",
                "answers": ["Necesito arreglar mi llanta."]}]}
  ]
}
```

`Timeline` segment:

```json
{"seq": 41, "chapter": "Build-up", "voice": "female_1", "lang": "es",
 "text": "dónde está la estación", "rate": 0.9, "pause_after_s": 3.4,
 "source": {"kind": "buildup", "line": 3, "step": 2}}
```

## 6. Curriculum anchoring

Generated lessons drift without anchors: too many new words, grammar out of
order, the same structures reused. Comenius anchors three dimensions and
enforces them in the validator rather than trusting the prompt.

**Grammar sequence.** A syllabus file (`syllabus/es.yaml`) lists grammar
points in order with stable IDs, levels, and short descriptions. Sources:

- *Plan Curricular del Instituto Cervantes (PCIC)* — the reference-level
  description of Spanish for CEFR A1–C2, with grammar, functions, notions, and
  vocabulary by level. Used as a checklist to author our own syllabus file;
  not copied.
- *FSI Spanish Basic Course* unit order (public domain) as a cross-check and
  fallback backbone.

CEFR itself is too language-neutral to sequence grammar; the PCIC is the
Spanish-specific layer on top of it. Equivalents exist for other languages
(Profile Deutsch, Niveau A1/A2 for French).

**Vocabulary progression.** New words are drawn mostly from the next
frequency band above the learner's known set, plus a small number of topic
words the dialog needs. Frequency data: `wordfreq` (multilingual) and
SUBTLEX-ESP (subtitle-based, better for conversation).

**Functions and topics.** Each lesson pairs a communicative function (ask
for directions, make plans, complain politely) with a topic. Topics can come
from the learner ("running," "the school run," "fixing a bike") or rotate
through a default list.

**Target-book mode (later).** The learner names a book. Comenius lemmatizes
it, computes token coverage against known vocabulary, and ranks unknown
lemmas by in-book frequency weighted by general frequency. Those lemmas
feed the new-word budget. Progress is reported as coverage (e.g. "88% →
96%"); roughly 95% coverage supports reading with effort and 98% comfortable
unassisted reading (Hu & Nation 2000; Laufer).

## 7. Learner state

SQLite, single learner initially.

| Table | Contents |
|---|---|
| `items` | lemma or phrase or grammar ID; kind; first lesson; status (`introduced`, `reviewing`, `known`) |
| `exposures` | item × lesson × kind (taught, recalled, reviewed) |
| `lessons` | id, spec, script path, rendered artifacts, date |
| `reviews` | per-item SRS state (FSRS parameters), last review, due date |

**Sources of review signal**, in order of implementation:

1. *Assumed*: an item introduced in a lesson is scheduled for review in
   later lessons on a fixed expanding schedule (lesson n+1, n+3, n+7, …).
   No grading; mirrors Pimsleur.
2. *Anki sync*: read review history from Anki (AnkiConnect on desktop) and
   use real recall data to set due items and mark items known.
3. *Self-report*: quick post-lesson check-off of items that felt shaky.

## 8. Technology choices

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12, `uv` | Ecosystem fit (spaCy, genanki, audio) |
| CLI | Typer | `comenius plan / generate / render / publish` |
| LLM | Anthropic API (Claude) | Structured JSON output; separate prompts for generate and review |
| TTS | Provider interface; start with one of Azure Neural, ElevenLabs, or OpenAI | Pick by Spanish quality and SSML rate control; evaluate in M1 |
| NLP | spaCy `es_core_news_md` | Lemmatization, POS, proper nouns |
| Frequency | `wordfreq`, SUBTLEX-ESP | |
| Audio | ffmpeg + pydub | Concatenation, silence, loudness normalization, chapters |
| PDF | Typst | Fast, scriptable, good typography |
| Anki | genanki | Stable GUIDs |
| Feed hosting | S3 or Cloudflare R2, private random path | Static files only |
| State | SQLite | |
| Tests | pytest; golden-file tests for sequencer and renderers | |

## 9. Quality and evaluation

- **Listening is the main eval.** Each milestone ends with lessons used on
  real runs and drives; pacing and pause lengths get tuned from that.
- **Native-speaker spot checks** of the first ~10 generated lessons to
  calibrate the naturalness reviewer, then periodic sampling.
- **Automatic metrics** logged per lesson: new-word count, out-of-budget
  lemma count before/after regeneration, regeneration attempts, review-pass
  flags, lesson duration, % of time spent in learner pauses.
- **Golden tests**: a hand-written fixture lesson whose timeline and audio
  segment list are checked into the repo; sequencer changes show up as diffs.

## 10. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Curriculum drift (too many new words, grammar out of order) | High | Code-enforced budgets; syllabus IDs; regeneration with explicit violations |
| Stilted or regionally mixed Spanish | Medium | Pinned dialect; naturalness review pass; native-speaker sampling |
| TTS prosody on short build-up fragments | Medium | Compare per-fragment synthesis vs. slicing a full-sentence rendering at word boundaries; choose per provider |
| Pacing feels wrong while running | Medium | Pause model in code with per-learner multiplier; iterate from real use |
| Lessons too long or too repetitive | Medium | Duration budget in planner; recall scheduler caps callbacks |
| LLM and TTS cost | Low | Cache TTS by segment hash; cost likely cents to low dollars per lesson |
| Copyright of reference materials | Low | Syllabus authored by us from references; FSI text only from public-domain originals |

## 11. Future work (wishlist)

- **Player app with hands-free grading**: headphone next/previous mapped to
  "got it"/"missed" for binary FSRS grading; optionally speech recognition
  against the known expected answer.
- **Pronunciation feedback**: record responses during lessons; batch
  phoneme-level assessment afterward (Azure Pronunciation Assessment,
  SpeechSuper); review screen with side-by-side playback. Expect useful
  detection of gross errors, not fine nuance.
- **FSI import**: OCR + WhisperX alignment of public-domain FSI courses to
  extract drills as additional content and as Anki cards.
- **More languages**: syllabus file + dialect + voices per language.
- **Listening-only "story" episodes** using only known vocabulary, for
  extensive listening.

## 12. Open questions

1. Which TTS provider has the best Spanish voices and rate control for
   build-up fragments? (Decide in M1 with a bake-off.)
2. Target dialect: Mexican or Peninsular Spanish?
3. Starting level: true A1, or seed the known-vocabulary set from an
   existing level (e.g. a placement self-assessment or prior Anki deck)?
4. Ideal lesson length for runs vs. commutes — one length, or two variants?
5. Should review items be standalone prompts, or woven into new dialogs
   (more natural, harder to control)?
