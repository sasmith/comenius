"""comenius command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from .config import Config, load_config
from .schema import LessonScript, LessonSpec

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", "-c", help="Path to comenius.toml.", show_default=False)
]


def _cfg(path: Path | None) -> Config:
    return load_config(path)


def _load_script(path: Path) -> LessonScript:
    return LessonScript.model_validate_json(path.read_text(encoding="utf-8"))


def _load_spec(path: Path) -> LessonSpec:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    return LessonSpec.model_validate(data)


def _mmss(s: float) -> str:
    return f"{int(s // 60)}:{int(s % 60):02d}"


@app.command()
def render(
    lessons: Annotated[list[Path], typer.Argument(help="Lesson script JSON file(s).")],
    provider: Annotated[
        str | None, typer.Option(help="TTS provider: espeak, azure, openai, elevenlabs.")
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


@app.command()
def feed(
    site: Annotated[Path, typer.Option(help="Output directory for the static site.")] = Path(
        "site"
    ),
    out: Annotated[Path | None, typer.Option(help="Rendered lessons directory.")] = None,
    base_url: Annotated[str | None, typer.Option(help="Public URL of the site directory.")] = None,
    config: ConfigOpt = None,
) -> None:
    """Build a podcast feed (feed.xml + MP3/PDF) from rendered lessons."""
    from .feed import build_feed

    cfg = _cfg(config)
    feed_cfg = cfg.feed.model_copy(update={"base_url": base_url} if base_url else {})
    path = build_feed(out or cfg.out_dir, site, feed_cfg)
    typer.echo(f"Wrote {path}. Upload {site}/ so that {feed_cfg.base_url}/feed.xml resolves.")


@app.command()
def schema(
    kind: Annotated[str, typer.Argument(help="script or spec")] = "script",
) -> None:
    """Print the JSON Schema for lesson scripts or specs."""
    model = {"script": LessonScript, "spec": LessonSpec}[kind]
    typer.echo(json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    app()
