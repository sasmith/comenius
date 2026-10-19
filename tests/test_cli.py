"""The command-line loop end to end: state, queue, plan, feed, publish (mock S3)."""

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from comenius.cli import app
from comenius.config import FeedConfig
from comenius.publish import publish
from comenius.render import render_lesson

from .conftest import FIXTURE


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "comenius.toml"
    path.write_text(
        f'out_dir = "{tmp_path / "out"}"\n'
        f'[state]\npath = "{tmp_path / "state.db"}"\n'
        f'[feed]\nsite_dir = "{tmp_path / "site"}"\n',
        encoding="utf-8",
    )
    return path


def run(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_all_commands_are_registered():
    out = run("--help")
    for name in ("render", "generate", "plan", "state", "queue", "syllabus", "feed", "publish"):
        assert f" {name} " in out, name


def test_state_queue_plan_loop(config, tmp_path):
    c = str(config)
    run("state", "assume", "--through", "es.a1.questions", "-c", c)
    assert "lesson #1" in run("state", "add", str(FIXTURE), "-c", c)
    run("queue", "add", "Navigating the airport", "--lessons", "2", "-c", c)
    spec_path = tmp_path / "next.yaml"
    out = run("plan", "-o", str(spec_path), "-c", c)
    assert "Topic: Navigating the airport" in out and spec_path.exists()
    assert "es-0002" in spec_path.read_text()
    shown = run("state", "show", "-c", c)
    assert "Lessons: 1" in shown and "Due for review" in shown
    assert "0/2 lessons" in run("queue", "list", "-c", c)


@mock_aws
def test_publish_uploads_changed_files_and_feed_last(script, tmp_path):
    from comenius.config import Config, TTSConfig

    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    render_lesson(script, cfg, provider="espeak", anki=False)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="lessons")
    feed_cfg = FeedConfig(
        bucket="lessons", prefix="abc123", base_url="https://cdn.example/abc123",
        site_dir=tmp_path / "site",
    )  # fmt: skip

    first = publish(cfg.out_dir, feed_cfg, client=s3)
    assert first.uploaded[-1] == "abc123/feed.xml"
    assert "abc123/es-0001.mp3" in first.uploaded
    head = s3.head_object(Bucket="lessons", Key="abc123/es-0001.mp3")
    assert head["ContentType"] == "audio/mpeg"
    assert first.feed_url == "https://cdn.example/abc123/feed.xml"

    second = publish(cfg.out_dir, feed_cfg, client=s3)
    assert "abc123/es-0001.mp3" in second.unchanged
