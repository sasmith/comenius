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
AZURE_SPEECH_KEY=... uv run comenius render fixtures/lessons/es-0001.json --provider azure
OPENAI_API_KEY=...   uv run comenius render fixtures/lessons/es-0001.json --provider openai
ELEVENLABS_API_KEY=... uv run comenius render ... --provider elevenlabs  # set voice ids in config
```

Generate a new lesson from a spec with Claude, then render it:

```sh
ANTHROPIC_API_KEY=... uv run comenius generate examples/specs/es-0002.yaml --render
```

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
uv run comenius generate specs/es-0002.yaml --render --record
uv run comenius state show                                # progress and what's due
```

A planned lesson teaches the next syllabus point, treats words from earlier lessons
as known, and reviews lines and words that are due. Its topic is the first unfinished
situation in the queue (or `--topic`), and up to three queued words are worked in.
`comenius queue list` shows progress. Items come back for review
after 1, 2, 4, 8, then 16 more lessons; each review moves an item to the next interval.

Other commands:

```sh
uv run comenius timeline fixtures/lessons/es-0001.json --full   # preview pacing, no TTS
uv run comenius validate lessons/es-0002.json --spec examples/specs/es-0002.yaml
uv run comenius feed --base-url https://<bucket>/<long-random-path>
aws s3 sync site/ s3://<bucket>/<long-random-path>/          # then subscribe to .../feed.xml
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
