"""Put lesson PDFs on a reMarkable tablet through its cloud, using rmapi.

rmapi (https://github.com/ddvk/rmapi) is an unofficial open-source client for the
reMarkable cloud; run it once by hand to sign in. Each lesson's PDF is uploaded once,
as "<id> <title>" in one folder, and the tablet downloads it the next time it syncs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


class RemarkableError(RuntimeError):
    pass


class Rmapi:
    def __init__(self, binary: str) -> None:
        found = shutil.which(binary)
        if not found:
            raise RemarkableError(
                f"{binary!r} not found. Install rmapi (github.com/ddvk/rmapi) and run it once "
                "to sign in."
            )
        self.binary = found

    def run(self, *args: str) -> str:
        # No stdin: if rmapi isn't signed in it would wait for a code, so fail instead.
        result = subprocess.run(
            [self.binary, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:500]
            raise RemarkableError(
                f"rmapi {' '.join(args)} failed: {detail}\n"
                "If it asks for a code, run `rmapi` once by hand to sign in."
            )
        return result.stdout

    def names(self, folder: str) -> set[str]:
        """Entry names in `folder` (rmapi ls prints "[f]\\tname" / "[d]\\tname")."""
        return {
            line.split("\t", 1)[1] for line in self.run("ls", folder).splitlines() if "\t" in line
        }


def push_pdfs(out_dir: Path, rm: Rmapi, folder: str) -> list[str]:
    """Upload every rendered lesson PDF not already in the top-level `folder`; return the
    names uploaded."""
    path = "/" + folder.strip("/")
    if folder.strip("/") not in rm.names("/"):
        rm.run("mkdir", path)
    have = rm.names(path)
    uploaded = []
    with tempfile.TemporaryDirectory() as tmp:
        for pdf in sorted(out_dir.glob("*/lesson.pdf")):
            meta = json.loads((pdf.parent / "meta.json").read_text(encoding="utf-8"))
            title = f"{meta['id']} {meta['title']}".replace("/", "-")
            if title in have:
                continue
            named = Path(tmp) / f"{title}.pdf"  # rmapi names the document after the file
            shutil.copyfile(pdf, named)
            rm.run("put", str(named), path)
            uploaded.append(title)
    return uploaded
