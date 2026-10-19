"""YAML loading with no implicit typing.

YAML 1.1 (PyYAML's default) guesses types from bare scalars: `no`/`yes`/`on`/`off`
become booleans, `3:30` becomes 210, `0123` can become octal, `2026-10-18` a date.
That silently corrupts word lists and Spanish text. Here every scalar is read as a
string, and the pydantic model that validates the data converts to int, float, or
bool where a field declares that type. A key with an empty value is dropped, so the
model's default applies.
"""

from __future__ import annotations

from typing import Any

import yaml


class _StringScalars(yaml.SafeLoader):
    pass


_StringScalars.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag == "tag:yaml.org,2002:null"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _drop_empty(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _drop_empty(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [_drop_empty(v) for v in node]
    return node


def load_yaml(text: str) -> Any:
    return _drop_empty(yaml.load(text, _StringScalars))
