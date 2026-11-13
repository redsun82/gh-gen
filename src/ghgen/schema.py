"""Schema-backed expression references; payloads are never instantiated locally."""

from __future__ import annotations

import functools
import importlib
import json
import keyword
from pathlib import Path
import typing
import warnings

from .expr import DotExpr, ErrorExpr, Expr, ItemExpr, RefExpr


@functools.cache
def catalog() -> dict:
    return json.loads(Path(__file__).with_name("_context_data.json").read_text())


def supports(node_id: int, path: tuple[str, ...]) -> bool:
    nodes = catalog()["nodes"]
    for segment in path:
        node = nodes[node_id]
        if node.get("open"):
            return True
        child = node.get("fields", {}).get(segment)
        if child is None:
            child = (
                node.get("item", node.get("map")) if segment == "*" else node.get("map")
            )
        if child is None:
            return False
        node_id = child
    return True


class SchemaRef(RefExpr):
    _schema_id: typing.ClassVar[int]

    def _child(self, key: str, source: Expr | None = None) -> RefExpr:
        node = catalog()["nodes"][self._schema_id]
        fields = node.get("fields", {})
        if (
            key == "result"
            and self._segments[:1] == ("steps",)
            and len(self._segments) == 2
        ):
            warnings.warn(
                "step.result is deprecated; use step.conclusion",
                DeprecationWarning,
                stacklevel=3,
            )
            key = "conclusion"
            if source is not None:
                source = ItemExpr(self, Expr._coerce(key))
        child = fields.get(key)
        if child is None and key == "*":
            child = node.get("item", node.get("map"))
        if child is None:
            child = node.get("map")
        if child is None and node.get("open"):
            child = catalog()["dynamic"]
        if child is None:
            return ~ErrorExpr(f"`{key}` not available in `{self._path}`")
        module = importlib.import_module("._context_types", __package__)
        cls = module.TYPES[child]
        if source is not None or self._source is not None:
            ref = object.__new__(cls)
            RefExpr.__init__(ref, *self._segments, key)
            object.__setattr__(
                ref, "_source", source if source is not None else DotExpr(self, key)
            )
            return ref
        return cls(*self._segments, key)

    def _attribute(self, key: str) -> RefExpr:
        fields = catalog()["nodes"][self._schema_id].get("fields", {})
        if key == "_":
            return self._wildcard()
        original = key
        if key.endswith("_") and keyword.iskeyword(key[:-1]):
            key = key[:-1]
        elif key not in fields:
            key = next((name for name in fields if name.replace("-", "_") == key), key)
        if original.startswith("_") and key not in fields:
            raise AttributeError(original)
        return self._child(key)

    if not typing.TYPE_CHECKING:
        __getattr__ = _attribute

    def _wildcard(self) -> RefExpr:
        return self._child("*")

    def __getitem__(self, key: str | int | Expr) -> RefExpr:
        node = catalog()["nodes"][self._schema_id]
        if isinstance(key, Expr):
            segment = "*"
            if not any(k in node for k in ("item", "map", "open")):
                return ItemExpr(self, key)
        elif "item" in node:
            if not isinstance(key, int):
                return ~ErrorExpr(f"array index for `{self._path}` must be an integer")
            segment = "*"
        else:
            segment = str(key)
        return self._child(segment, ItemExpr(self, Expr._coerce(key)))


class OpenRef(SchemaRef):
    def __getattr__(self, key: str) -> OpenRef:
        return typing.cast(OpenRef, self._attribute(key))

    def __getitem__(self, key: str | int | Expr) -> OpenRef:
        return typing.cast(OpenRef, super().__getitem__(key))
