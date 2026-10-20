"""Structured LLM calls through either the Anthropic API or Claude Code.

Generation needs one operation: given a system prompt, a JSON schema, and a short
conversation, return an object matching the schema. Two backends provide it:

- `api`: the Anthropic Messages API with a forced tool call (ANTHROPIC_API_KEY).
- `claude-code`: the unmodified `claude` CLI in print mode with `--json-schema`,
  signed in however Claude Code is signed in (for example a Claude subscription).
  Tools, CLAUDE.md, hooks, plugins, and MCP servers are turned off, so the call is a
  plain prompt-in, JSON-out request.

A conversation is a list of turns. A "user" turn is text. An "assistant" turn is an
object the model returned earlier; the user turn after it is feedback on that object.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol

from .config import LLMConfig

Turn = tuple[Literal["user"], str] | tuple[Literal["assistant"], dict[str, Any]]


class LLMError(RuntimeError):
    pass


class Backend(Protocol):
    def call(
        self,
        *,
        model: str,
        system: str,
        name: str,
        schema: dict[str, Any],
        turns: list[Turn],
        max_tokens: int,
    ) -> dict[str, Any]: ...


class APIBackend:
    """Anthropic Messages API; the schema is a tool the model is forced to call."""

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def call(
        self,
        *,
        model: str,
        system: str,
        name: str,
        schema: dict[str, Any],
        turns: list[Turn],
        max_tokens: int,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for i, (role, content) in enumerate(turns):
            if role == "assistant":
                block = {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": content}
                messages.append({"role": "assistant", "content": [block]})
            elif messages and messages[-1]["role"] == "assistant":
                result = {
                    "type": "tool_result",
                    "tool_use_id": f"toolu_{i - 1}",
                    "is_error": True,
                    "content": content,
                }
                messages.append({"role": "user", "content": [result]})
            else:
                messages.append({"role": "user", "content": content})
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=[{"name": name, "description": "Submit the result.", "input_schema": schema}],
            tool_choice={"type": "tool", "name": name},
            messages=messages,
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == name:
                return dict(block.input)
        raise LLMError(f"Model did not call {name}")


class ClaudeCodeBackend:
    """`claude -p --json-schema`, using Claude Code's own sign-in."""

    def __init__(self, binary: str = "claude") -> None:
        found = shutil.which(binary)
        if not found:
            raise LLMError(
                f"{binary!r} not found. Install Claude Code and sign in once by running `claude`."
            )
        self.binary = found

    def call(
        self,
        *,
        model: str,
        system: str,
        name: str,
        schema: dict[str, Any],
        turns: list[Turn],
        max_tokens: int,
    ) -> dict[str, Any]:
        parts = []
        for role, content in turns:
            if role == "assistant":
                parts.append(f"Your previous answer:\n{json.dumps(content, ensure_ascii=False)}")
            else:
                parts.append(content)
        with tempfile.TemporaryDirectory() as tmp:
            system_file = Path(tmp) / "system.txt"
            system_file.write_text(system, encoding="utf-8")
            result = subprocess.run(
                [
                    self.binary, "-p",
                    "--output-format", "json",
                    "--json-schema", json.dumps(schema),
                    "--system-prompt-file", str(system_file),
                    "--model", model,
                    "--tools", "",
                    "--disallowedTools", "mcp__*",
                    "--strict-mcp-config",
                    "--safe-mode",
                    "--no-session-persistence",
                ],
                input="\n\n".join(parts),
                capture_output=True,
                text=True,
                cwd=tmp,  # an empty directory: nothing project-specific to discover
                check=False,
            )  # fmt: skip
        if not result.stdout.strip():
            raise LLMError(f"claude exited {result.returncode}: {result.stderr.strip()[:500]}")
        out = json.loads(result.stdout)
        if out.get("is_error") or result.returncode != 0 or "structured_output" not in out:
            raise LLMError(f"claude failed ({out.get('subtype')}): {str(out.get('result'))[:500]}")
        return dict(out["structured_output"])


def make_backend(cfg: LLMConfig) -> Backend:
    if cfg.backend == "claude-code":
        return ClaudeCodeBackend(cfg.claude_binary)
    return APIBackend()
