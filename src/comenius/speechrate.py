"""Measure how fast each dialog voice speaks, in syllables per second.

Native Spanish runs about 7.8 syllables/s in read speech (Pellegrino, Coupé &
Marsico 2011). Comparing rendered voices against that tells us how far a
lesson's "natural speed" dialog is from real speech.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .schema import Segment

STRONG = set("aeoáéóíú")  # accented i/u break a diphthong, so they count as strong
WEAK = set("iuü")


def spanish_syllables(text: str) -> int:
    """Canonical syllable count (word by word, no merging across word boundaries)."""
    total = 0
    for word in re.findall(r"[^\W\d_]+", unicodedata.normalize("NFC", text.lower())):
        word = re.sub(r"([qg])u([eéií])", r"\1\2", word)  # silent u in que/qui/gue/gui
        if word.endswith("y"):
            word = word[:-1] + "i"  # hoy, muy, y: final y is a vowel
        prev = ""
        for ch in word:
            if ch in STRONG or ch in WEAK:
                # A new syllable starts unless this vowel forms a diphthong with the last.
                if not prev or (prev in STRONG and ch in STRONG):
                    total += 1
                prev = ch
            else:
                prev = ""
    return total


def dialog_rates(segments: Iterable[Segment], lang: str) -> dict[str, float]:
    """Syllables per second of speech for each voice's full-speed dialog lines.

    Clips are trimmed of leading and trailing silence, so this is articulation rate:
    pauses between lines are excluded.
    """
    if lang.split("-")[0] != "es":
        return {}
    syllables: dict[str, int] = {}
    seconds: dict[str, float] = {}
    for seg in segments:
        if seg.kind == "dialog" and seg.rate == 1.0 and seg.tempo == 1.0:
            syllables[seg.voice] = syllables.get(seg.voice, 0) + spanish_syllables(seg.text)
            seconds[seg.voice] = seconds.get(seg.voice, 0.0) + seg.duration_s
    return {v: round(syllables[v] / seconds[v], 2) for v in syllables}
