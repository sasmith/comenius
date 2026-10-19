"""Upload the podcast site (feed.xml, MP3s, PDFs, decks) to S3 or an S3-compatible store.

Only files whose content changed are uploaded (compared by MD5 against the
object's ETag), and feed.xml goes last so podcast apps never see an episode
before its audio exists.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import FeedConfig
from .feed import build_feed

CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".xml": "application/rss+xml; charset=utf-8",
    ".pdf": "application/pdf",
    ".apkg": "application/octet-stream",
    ".txt": "text/plain; charset=utf-8",
}


class PublishError(RuntimeError):
    pass


@dataclass
class PublishResult:
    feed_url: str
    uploaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _client(cfg: FeedConfig) -> Any:
    try:
        import boto3
    except ImportError:
        raise PublishError("Install the aws extra: uv sync --extra aws") from None
    return boto3.client("s3", endpoint_url=cfg.endpoint_url, region_name=cfg.region)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def publish(out_dir: Path, cfg: FeedConfig, client: Any | None = None) -> PublishResult:
    if not cfg.bucket:
        raise PublishError("Set [feed] bucket (and base_url, prefix) in comenius.toml")
    feed = build_feed(out_dir, cfg.site_dir, cfg)
    s3 = client or _client(cfg)
    prefix = cfg.prefix.strip("/")

    remote: dict[str, str] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=cfg.bucket, Prefix=f"{prefix}/" if prefix else ""
    ):
        for obj in page.get("Contents", []):
            remote[obj["Key"]] = obj["ETag"].strip('"')

    result = PublishResult(feed_url=f"{cfg.base_url.rstrip('/')}/feed.xml")
    files = sorted(p for p in cfg.site_dir.iterdir() if p.is_file() and p != feed) + [feed]
    for path in files:
        key = f"{prefix}/{path.name}" if prefix else path.name
        if remote.get(key) == _md5(path):
            result.unchanged.append(key)
            continue
        content_type = CONTENT_TYPES.get(path.suffix) or (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        # put_object (not multipart) keeps the ETag equal to the MD5 for change detection.
        s3.put_object(Bucket=cfg.bucket, Key=key, Body=path.read_bytes(), ContentType=content_type)
        result.uploaded.append(key)
    return result
