# Comenius

Generated, audio-first language lessons for running, driving, and commuting.

Each lesson starts from a short dialog in the target language and becomes a
spoken lesson in the FSI/Pimsleur tradition: backward build-up of each line,
substitution and translation drills, and recall of earlier material at
increasing intervals. Every lesson also produces a PDF companion and Anki
flashcards, and lessons can be published as a private podcast feed.

- [Design](docs/DESIGN.md)
- [Implementation plan](docs/PLAN.md)
- [Roadmap](docs/ROADMAP.md)

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). ffmpeg comes bundled
(a system ffmpeg is used if present). The default offline voice needs espeak-ng;
the Kokoro voices below don't.

```sh
brew install espeak-ng               # or: apt install espeak-ng (only for --provider espeak)
uv sync
```

## Usage

Render the hand-written sample lesson with offline speech:

```sh
uv run comenius render fixtures/lessons/es-0001.json
# -> out/es-0001/lesson.mp3, lesson.pdf, lesson.apkg, transcript.txt, timeline.json
```

For good free voices, use Kokoro, an open-weights neural model that runs
locally (about 350 MB; Spanish voices Dora and Alex):

```sh
uv sync --extra kokoro
mkdir -p ~/.cache/comenius/kokoro && cd ~/.cache/comenius/kokoro && \
  curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx \
       -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
uv run comenius render fixtures/lessons/es-0001.json --provider kokoro
```

Or a paid voice provider (see `comenius.example.toml` for voices):

```sh
uv sync --extra aws && uv run comenius render ... --provider polly   # AWS credentials as usual
AZURE_SPEECH_KEY=... uv run comenius render fixtures/lessons/es-0001.json --provider azure
OPENAI_API_KEY=...   uv run comenius render fixtures/lessons/es-0001.json --provider openai
ELEVENLABS_API_KEY=... uv run comenius render ... --provider elevenlabs  # set voice ids in config
```

To hear many native accents rather than one, use the voice pool:

```sh
uv run comenius render fixtures/lessons/es-0001.json --provider pool   # or provider = "pool" in config
```

Each lesson casts its speakers from a pool of native Latin American voices
across Polly and Azure (`tts.pool`), using only providers you have
credentials for. The choice is seeded by the lesson id, so re-rendering a
lesson keeps its voices. The English narrator stays the same across lessons:
the first available entry in `tts.narrators`. `meta.json` records who spoke.

Generate a new lesson from a spec with Claude, then render it:

```sh
ANTHROPIC_API_KEY=... uv run comenius generate examples/specs/es-0002.yaml --render
```

Or use a Claude subscription instead of an API key. Install
[Claude Code](https://code.claude.com/docs/en/overview), sign in once by running
`claude`, and set the backend in `comenius.toml`:

```toml
[llm]
backend = "claude-code"
```

Comenius then runs `claude -p` with tools, plugins, hooks, and project files turned
off, and generation counts against your plan's usage limits.

Generation runs code checks (new-word budget, required words, English-only
narrator text, drill sanity) and a native-speaker review pass, regenerating up
to three times. A `<id>.report.json` next to the script records what happened.

### A course that builds on itself

Learner state lives in a small SQLite database (`~/.local/share/comenius/state.db`
by default). Record lessons as you do them, and plan the next one from it:

```sh
uv run comenius state assume --through es.a1.questions   # grammar you already know (optional)
uv run comenius state add fixtures/lessons/es-0001.json  # record lessons you've done
uv run comenius queue add "Navigating the airport" --lessons 3   # things you want to learn
uv run comenius queue add --words "pasaporte, equipaje, maleta"
uv run comenius plan                                      # -> specs/es-0002.yaml
uv run comenius generate specs/es-0002.yaml --render --record --publish
uv run comenius state show                                # progress and what's due
```

Or do it all in one step: `comenius next` plans, generates, renders, and records lessons
one after another, each planned from the state the previous one left:

```sh
uv run comenius next --count 3 --provider pool --publish --anki
```

It stops at the first lesson that fails its checks (fix it, `comenius state add` it, and
run again).

A planned lesson teaches the next syllabus point, treats words from earlier lessons
as known, and reviews lines and words that are due. Its topic is `--topic` if given, else
the first unfinished situation in the queue, else the next unused built-in topic
([`topics.yaml`](src/comenius/data/topics.yaml): 47 everyday situations, A1→B1, not tied
to one language). Up to three queued words are worked in.
`comenius queue list` shows progress. Items come back for review
after 1, 2, 4, 8, then 16 more lessons; each review moves an item to the next interval.

### Publishing to your podcast app

`comenius publish` builds the podcast site (feed, MP3s, PDFs, decks) and uploads
it to an S3 bucket, sending only files that changed and the feed last.

1. Pick a private prefix: `python -c "import secrets; print(secrets.token_urlsafe(16))"`.
2. Create a bucket used only for this (e.g. `comenius-<you>`). Under Permissions, turn
   off "Block public access", then add a bucket policy that makes only the prefix readable:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Principal": "*",
       "Action": "s3:GetObject",
       "Resource": "arn:aws:s3:::comenius-<you>/<random prefix>/*"
     }]
   }
   ```

3. Configure `comenius.toml`:

   ```toml
   [feed]
   bucket = "comenius-<you>"
   prefix = "<random prefix>"
   ```

   The public URL is built from these as
   `https://<bucket>.s3.<region>.amazonaws.com/<prefix>`, with the region looked up from
   the bucket (or set `region` to skip the lookup).

4. `uv sync --extra aws`, then `uv run comenius publish` with your usual AWS credentials
   (`AWS_PROFILE` or access keys; the IAM user needs `s3:PutObject`, `s3:ListBucket`, and
   `s3:GetBucketLocation` on the bucket). Subscribe to the printed feed URL in your podcast app (Overcast and
   Pocket Casts accept a URL; in Apple Podcasts use "Follow a Show by URL").

Anyone with the URL can download the lessons, so keep the prefix private. To keep the
bucket fully private instead, put CloudFront in front of it with origin access control
and use the CloudFront domain as `base_url`.

S3-compatible stores work too. For Cloudflare R2, set `endpoint_url =
"https://<account-id>.r2.cloudflarestorage.com"`, turn on the bucket's public `r2.dev`
subdomain, and use `https://pub-<id>.r2.dev/<random prefix>` as `base_url`.

### Getting the cards into Anki

Each lesson's deck goes into one `Comenius::Spanish` deck, and card IDs are stable, so
importing a lesson again updates its cards instead of duplicating them. You can open
`out/<lesson>/lesson.apkg` by hand, or tap the "Anki deck" link in the episode notes.
With Anki desktop open and the [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
add-on installed:

```sh
uv run comenius anki push            # import every rendered lesson, then sync to AnkiWeb
uv run comenius anki push --no-sync  # skip the AnkiWeb sync
```

Other commands:

```sh
uv run comenius timeline fixtures/lessons/es-0001.json --full   # preview pacing, no TTS
uv run comenius validate lessons/es-0002.json --spec examples/specs/es-0002.yaml
uv run comenius feed                                          # build the podcast site locally only
uv run comenius schema script                                 # JSON Schema for lesson scripts
uv run comenius syllabus --level A1 --full                    # grammar syllabus
uv run comenius syllabus --known es.a1.greetings,es.a1.present_ar  # what to teach next
```

## Configuration

Copy `comenius.example.toml` to `comenius.toml`. The main pacing knob is
`lesson.pause_multiplier` (raise it for running). Synthesized clips are cached
in `.cache/tts/` by content hash, so re-rendering after a pacing change is fast
and free.

## Development

```sh
uv run pytest           # includes an end-to-end espeak render if espeak-ng is installed
uv run ruff check src tests && uv run ruff format src tests
UPDATE_GOLDEN=1 uv run pytest tests/test_sequence.py   # accept sequencer changes
```

Named for Jan Amos Comenius (1592–1670), author of *Janua Linguarum* and often
called the father of modern education.
