"""comenius command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from .config import Config, ConfigError, load_config
from .schema import LessonScript, LessonSpec
from .state import LearnerState

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", "-c", help="Path to comenius.toml.", show_default=False)
]


def _cfg(path: Path | None) -> Config:
    return load_config(path)


def _load_script(path: Path) -> LessonScript:
    return LessonScript.model_validate_json(path.read_text(encoding="utf-8"))


def _load_spec(path: Path) -> LessonSpec:
    return LessonSpec.from_file(path)


def _mmss(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


@app.command()
def render(
    lessons: Annotated[list[Path], typer.Argument(help="Lesson script JSON file(s).")],
    provider: Annotated[
        str | None,
        typer.Option(
            help="espeak, kokoro, polly, azure, openai, elevenlabs, or pool (mix providers)."
        ),
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Output directory.")] = None,
    mp3: bool = True,
    pdf: bool = True,
    anki: bool = True,
    config: ConfigOpt = None,
) -> None:
    """Render lesson scripts to MP3, PDF, and Anki deck."""
    from .render import render_lesson

    cfg = _cfg(config)
    for path in lessons:
        script = _load_script(path)
        typer.echo(f"Rendering {script.id}: {script.title} ...")
        result = render_lesson(
            script, cfg, provider=provider, mp3=mp3, pdf=pdf, anki=anki, out_dir=out
        )
        t = result.timeline
        typer.echo(
            f"  {_mmss(t.duration_s)} total, "
            f"{round(100 * t.learner_pause_s() / t.duration_s)}% learner pauses, "
            f"{len(t.segments)} segments"
        )
        for name in sorted(result.files):
            typer.echo(f"  {result.files[name]}")


@app.command()
def timeline(
    lesson: Annotated[Path, typer.Argument(help="Lesson script JSON.")],
    full: Annotated[bool, typer.Option(help="Print every segment.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Preview a lesson's structure using estimated durations (no TTS)."""
    from .sequence import build_timeline

    cfg = _cfg(config)
    t = build_timeline(_load_script(lesson), cfg.lesson)
    typer.echo(f"~{_mmss(t.duration_s)} estimated, {len(t.segments)} segments")
    for c in t.chapters:
        typer.echo(f"  {_mmss(c.start_s):>6}  {c.title}")
    if full:
        for s in t.segments:
            pause = f"  [{s.pause_after_s:.1f}s]" if s.pause_after_s >= 1 else ""
            typer.echo(f"{_mmss(s.start_s):>6} {s.kind:<12} {s.voice:<9} {s.text}{pause}")


@app.command()
def validate(
    lesson: Annotated[Path, typer.Argument(help="Lesson script JSON.")],
    spec: Annotated[Path | None, typer.Option(help="Check against a lesson spec.")] = None,
) -> None:
    """Run deterministic checks on a lesson script."""
    from .validate import validate_script

    issues = validate_script(_load_script(lesson), _load_spec(spec) if spec else None)
    for issue in issues:
        typer.echo(str(issue))
    if not issues:
        typer.echo("No issues.")
    if any(i.severity == "error" for i in issues):
        raise typer.Exit(1)


@app.command()
def generate(
    spec: Annotated[Path, typer.Argument(help="Lesson spec (YAML or JSON).")],
    out: Annotated[Path, typer.Option(help="Directory for generated scripts.")] = Path("lessons"),
    then_render: Annotated[bool, typer.Option("--render", help="Render after generating.")] = False,
    provider: Annotated[str | None, typer.Option(help="TTS provider for --render.")] = None,
    cache: Annotated[bool, typer.Option(help="Reuse a cached generation for this spec.")] = True,
    review: Annotated[bool, typer.Option(help="Run the native-speaker review pass.")] = True,
    record: Annotated[
        bool, typer.Option(help="Record the lesson in learner state once accepted.")
    ] = False,
    then_publish: Annotated[
        bool, typer.Option("--publish", help="Upload the podcast site after rendering.")
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Generate a lesson script with Claude (needs ANTHROPIC_API_KEY)."""
    from .generate import Generator

    cfg = _cfg(config)
    lesson_spec = _load_spec(spec)
    typer.echo(f"Generating {lesson_spec.id} ({lesson_spec.topic}) with {cfg.llm.model} ...")
    script, report = Generator(cfg.llm).generate(lesson_spec, use_cache=cache, review=review)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{script.id}.json"
    path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
    (out / f"{script.id}.report.json").write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    status = "accepted" if report.accepted else "NEEDS REVIEW"
    typer.echo(f"  {status} after {report.attempts} attempt(s): {path}")
    for line in report.issues + [f"(minor) {m}" for m in report.review_minor]:
        typer.echo(f"  - {line}")
    if then_render:
        from .render import render_lesson

        result = render_lesson(script, cfg, provider=provider)
        typer.echo(f"  rendered to {result.lesson_dir}")
    if record:
        if not report.accepted:
            typer.echo(
                "  not recorded: lesson needs review first (comenius state add after fixing)"
            )
            raise typer.Exit(1)
        seq = LearnerState(cfg.state.path).record_lesson(script)
        typer.echo(f"  recorded as lesson #{seq} in {cfg.state.path}")
    if then_publish:
        if not then_render:
            raise typer.BadParameter("--publish needs --render")
        _publish(cfg)


@app.command()
def feed(
    out: Annotated[Path | None, typer.Option(help="Rendered lessons directory.")] = None,
    config: ConfigOpt = None,
) -> None:
    """Build the podcast site (feed.xml + MP3/PDF/decks) locally, without uploading."""
    from .feed import build_feed

    cfg = _cfg(config)
    try:
        base = cfg.feed.public_url()
    except ConfigError as e:
        typer.echo(str(e))
        raise typer.Exit(1) from None
    path = build_feed(out or cfg.out_dir, cfg.feed.site_dir, cfg.feed, base)
    typer.echo(f"Wrote {path}")


def _publish(cfg: Config, out: Path | None = None) -> None:
    from .publish import PublishError, publish

    try:
        result = publish(out or cfg.out_dir, cfg.feed)
    except (PublishError, ConfigError) as e:
        typer.echo(str(e))
        raise typer.Exit(1) from None
    typer.echo(f"Uploaded {len(result.uploaded)} file(s), {len(result.unchanged)} unchanged.")
    typer.echo(f"Feed: {result.feed_url}")


@app.command("publish")
def publish_cmd(
    out: Annotated[Path | None, typer.Option(help="Rendered lessons directory.")] = None,
    config: ConfigOpt = None,
) -> None:
    """Build the podcast site and upload it to the bucket set under feed in comenius.toml."""
    _publish(_cfg(config), out)


state_app = typer.Typer(no_args_is_help=True, help="Learner state: lessons done, review schedule.")
app.add_typer(state_app, name="state")


@state_app.command("add")
def state_add(
    lessons: Annotated[list[Path], typer.Argument(help="Lesson script JSON file(s), in order.")],
    config: ConfigOpt = None,
) -> None:
    """Record lessons as done: their grammar, words, and lines become review items."""
    cfg = _cfg(config)
    st = LearnerState(cfg.state.path)
    for path in lessons:
        script = _load_script(path)
        seq = st.record_lesson(script)
        typer.echo(f"Recorded {script.id} ({script.title}) as lesson #{seq}")


@state_app.command("assume")
def state_assume(
    through: Annotated[
        str | None, typer.Option(help="Mark every syllabus point up to and including this id.")
    ] = None,
    ids: Annotated[str | None, typer.Option(help="Comma-separated syllabus ids.")] = None,
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    config: ConfigOpt = None,
) -> None:
    """Mark grammar you already know, so planning starts at the right place."""
    from .syllabus import load_syllabus

    syl = load_syllabus(lang)
    chosen: list[str] = []
    if through:
        order = [p.id for p in syl.points]
        chosen += order[: order.index(syl.get(through).id) + 1]
    if ids:
        chosen += [syl.get(i.strip()).id for i in ids.split(",") if i.strip()]
    if not chosen:
        raise typer.BadParameter("give --through or --ids")
    added = LearnerState(_cfg(config).state.path).assume_grammar(lang, chosen)
    typer.echo(f"Marked {added} new point(s) as known ({len(chosen)} given).")


@state_app.command("show")
def state_show(
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    config: ConfigOpt = None,
) -> None:
    """Summarize what's been learned and what's due."""
    from .syllabus import load_syllabus

    cfg = _cfg(config)
    st = LearnerState(cfg.state.path)
    lessons = st.lessons(lang)
    syl = load_syllabus(lang)
    known = st.known_grammar(lang)
    typer.echo(f"State: {st.path}")
    typer.echo(f"Lessons: {len(lessons)}" + (f" (last: {lessons[-1].id})" if lessons else ""))
    typer.echo(f"Grammar: {len(known & {p.id for p in syl.points})}/{len(syl.points)} points")
    nxt = syl.next_points(known, n=1)
    if nxt:
        typer.echo(f"  next: {nxt[0].id} ({nxt[0].title})")
    typer.echo(f"Words: {len(st.known_words(lang))}")
    due = st.due(lang, limit=100)
    typer.echo(f"Due for review in the next lesson: {len(due)}")
    for item in due[: cfg.state.review_items]:
        typer.echo(f"  {item.prompt_en} -> {item.answer}")


@app.command()
def plan(
    topic: Annotated[
        str | None, typer.Option(help="What the lesson is about (default: next in the queue).")
    ] = None,
    situation: Annotated[str, typer.Option(help="Optional scene for the dialog.")] = "",
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    out: Annotated[Path | None, typer.Option("-o", "--out", help="Write the spec here.")] = None,
    config: ConfigOpt = None,
) -> None:
    """Plan the next lesson spec from learner state, the syllabus, and the queue."""
    import yaml

    from .planner import NothingToPlan, plan_next

    cfg = _cfg(config)
    try:
        spec = plan_next(
            LearnerState(cfg.state.path), cfg.state, lang=lang, topic=topic, situation=situation
        )
    except NothingToPlan as e:
        typer.echo(str(e))
        raise typer.Exit(1) from None
    text = yaml.safe_dump(
        spec.model_dump(mode="json", exclude_defaults=False), allow_unicode=True, sort_keys=False
    )
    path = out or Path("specs") / f"{spec.id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    typer.echo(f"Topic: {spec.topic}")
    if spec.required_words:
        typer.echo(f"Queued words: {', '.join(spec.required_words)}")
    typer.echo(
        f"Wrote {path}: grammar {spec.grammar_points or '(syllabus complete)'}, "
        f"{len(spec.known_words)} known words, {len(spec.review)} review items"
    )
    typer.echo(f"Next: uv run comenius generate {path} --render --record")


queue_app = typer.Typer(no_args_is_help=True, help="Things you want to learn, used by plan.")
app.add_typer(queue_app, name="queue")


@queue_app.command("add")
def queue_add(
    situation: Annotated[
        str | None, typer.Argument(help='A situation, e.g. "Navigating the airport".')
    ] = None,
    lessons: Annotated[int, typer.Option(help="How many lessons on this situation.")] = 1,
    words: Annotated[
        str | None, typer.Option(help="Comma-separated words to work into lessons.")
    ] = None,
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    config: ConfigOpt = None,
) -> None:
    """Add a situation (a short series of lessons) or words to the queue."""
    if (situation is None) == (words is None):
        raise typer.BadParameter("give a situation or --words, not both")
    st = LearnerState(_cfg(config).state.path)
    if situation is not None:
        entry = st.queue_add(lang, "situation", situation, lessons)
    else:
        assert words is not None
        entry = st.queue_add(lang, "words", words, 1)
    typer.echo(f"Added #{entry}")


@queue_app.command("list")
def queue_list(
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    all_: Annotated[bool, typer.Option("--all", help="Include finished entries.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Show the queue with progress."""
    entries = LearnerState(_cfg(config).state.path).queue(lang)
    shown = [e for e in entries if all_ or not e.complete]
    if not shown:
        typer.echo("Queue is empty.")
    for e in shown:
        unit = "lessons" if e.kind == "situation" else "words known"
        mark = "done" if e.complete else f"{e.done}/{e.total} {unit}"
        typer.echo(f"#{e.id:<3} {e.kind:<9} {e.text}  [{mark}]")


@queue_app.command("remove")
def queue_remove(
    entry_id: Annotated[int, typer.Argument(help="Entry number from queue list.")],
    config: ConfigOpt = None,
) -> None:
    """Remove an entry from the queue."""
    LearnerState(_cfg(config).state.path).queue_remove(entry_id)
    typer.echo(f"Removed #{entry_id}")


@app.command()
def syllabus(
    lang: Annotated[str, typer.Option(help="Language code.")] = "es",
    level: Annotated[str | None, typer.Option(help="Only this CEFR level, e.g. A1.")] = None,
    known: Annotated[
        str | None, typer.Option(help="Comma-separated known ids; show what's next.")
    ] = None,
    full: Annotated[bool, typer.Option(help="Show descriptions and examples.")] = False,
) -> None:
    """List the grammar syllabus, or the next teachable points given what's known."""
    from .syllabus import load_syllabus

    syl = load_syllabus(lang)
    if known is not None:
        known_ids = {k.strip() for k in known.split(",") if k.strip()}
        points = syl.next_points(known_ids, n=5)
        typer.echo(f"Next after {len(known_ids)} known point(s):")
    else:
        points = [p for p in syl.points if level is None or p.level == level.upper()]
    for p in points:
        typer.echo(f"{p.level}  {p.id:<34} {p.title}")
        if full:
            typer.echo(f"      {p.description}")
            typer.echo(f"      e.g. {' / '.join(p.examples)}")


@app.command()
def schema(
    kind: Annotated[str, typer.Argument(help="script or spec")] = "script",
) -> None:
    """Print the JSON Schema for lesson scripts or specs."""
    model = {"script": LessonScript, "spec": LessonSpec}[kind]
    typer.echo(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app()
