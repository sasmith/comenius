"""Push rendered lesson decks into Anki desktop through the AnkiConnect add-on.

AnkiConnect (add-on 2055492159) serves a local HTTP API while Anki is open. Each
lesson's .apkg is imported; note GUIDs are stable, so importing a lesson again updates
its cards instead of duplicating them. A final sync sends the cards to AnkiWeb, from
which AnkiMobile and AnkiDroid pick them up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

API_VERSION = 6


class AnkiConnectError(RuntimeError):
    pass


class AnkiConnect:
    def __init__(self, url: str) -> None:
        self.url = url

    def invoke(self, action: str, **params: Any) -> Any:
        try:
            r = httpx.post(
                self.url,
                json={"action": action, "version": API_VERSION, "params": params},
                timeout=300,
            )
        except httpx.ConnectError:
            raise AnkiConnectError(
                f"AnkiConnect isn't answering at {self.url}. Is Anki open with the add-on?"
            ) from None
        body = r.json()
        if body["error"]:
            raise AnkiConnectError(f"{action}: {body['error']}")
        return body["result"]


def push_decks(out_dir: Path, anki: AnkiConnect, *, sync: bool = True) -> list[Path]:
    """Import every rendered lesson deck under `out_dir`, then sync to AnkiWeb."""
    if anki.invoke("version") < API_VERSION:
        raise AnkiConnectError(f"AnkiConnect API version {API_VERSION}+ is needed; update it.")
    decks = sorted(out_dir.glob("*/lesson.apkg"))
    for deck in decks:
        # AnkiConnect resolves the path against Anki's media folder; an absolute path wins.
        anki.invoke("importPackage", path=str(deck.resolve()))
    if sync:
        anki.invoke("sync")
    return decks
