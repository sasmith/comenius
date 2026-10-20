"""The command-line loop end to end: state, queue, plan, feed, publish (mock S3), anki push."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import boto3
import pytest
import typer
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
    commands = typer.main.get_command(app).commands  # not --help text: CI adds color codes
    names = ("render", "generate", "plan", "state", "queue", "syllabus", "feed", "publish", "anki")
    for name in names:
        assert name in commands, name
    assert {"add", "show", "assume"} <= set(commands["state"].commands)


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


@mock_aws
def test_publish_derives_public_url_from_bucket_region_and_prefix(script, tmp_path):
    from comenius.config import Config, TTSConfig

    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    render_lesson(script, cfg, provider="espeak", pdf=False, anki=False)
    s3 = boto3.client("s3", region_name="us-east-2")
    s3.create_bucket(
        Bucket="lessons", CreateBucketConfiguration={"LocationConstraint": "us-east-2"}
    )
    feed_cfg = FeedConfig(bucket="lessons", prefix="abc123", site_dir=tmp_path / "site")

    result = publish(cfg.out_dir, feed_cfg, client=s3)  # region looked up from the bucket
    base = "https://lessons.s3.us-east-2.amazonaws.com/abc123"
    assert result.feed_url == f"{base}/feed.xml"
    feed = s3.get_object(Bucket="lessons", Key="abc123/feed.xml")["Body"].read().decode()
    assert f'<enclosure url="{base}/es-0001.mp3"' in feed


class FakeAnkiConnect(BaseHTTPRequestHandler):
    """Answers like AnkiConnect and records each action."""

    calls: list[dict] = []

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAnkiConnect.calls.append(req)
        result = {"version": 6, "importPackage": True, "sync": None}[req["action"]]
        body = json.dumps({"result": result, "error": None}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_anki_push_imports_every_lesson_deck_then_syncs(script, config, tmp_path):
    from comenius.config import Config, TTSConfig

    cfg = Config(out_dir=tmp_path / "out", tts=TTSConfig(cache_dir=tmp_path / "cache"))
    render_lesson(script, cfg, provider="espeak", mp3=False, pdf=False)
    server = HTTPServer(("127.0.0.1", 0), FakeAnkiConnect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with config.open("a", encoding="utf-8") as f:
        f.write(f'[anki]\nurl = "http://127.0.0.1:{server.server_port}"\n')

    out = run("anki", "push", "-c", str(config))
    server.shutdown()

    assert "Imported 1 lesson deck(s) and synced" in out
    actions = [c["action"] for c in FakeAnkiConnect.calls]
    assert actions == ["version", "importPackage", "sync"]
    deck = FakeAnkiConnect.calls[1]["params"]["path"]
    assert deck == str((tmp_path / "out" / "es-0001" / "lesson.apkg").resolve())
