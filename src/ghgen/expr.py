import dataclasses
import abc
import functools
import re
import textwrap
import types
import typing
import warnings
import weakref
import contextlib

try:
    from string.templatelib import Template, Interpolation
except ImportError:  # Python < 3.14: t-strings unavailable, f-strings only
    Template = None
    Interpolation = None

from .types import RefTree

_fstring_deprecation_emitted = False


def _warn_fstring_deprecated() -> None:
    """Warn once per process when contexts are interpolated via f-strings.

    Only fires on 3.14+ where t-strings (the clean replacement) exist.
    """
    global _fstring_deprecation_emitted
    if _fstring_deprecation_emitted or Template is None:
        return
    _fstring_deprecation_emitted = True
    warnings.warn(
        'interpolating contexts with f-strings is deprecated; use t-strings (t"...") instead',
        DeprecationWarning,
        stacklevel=3,
    )


class Expr(abc.ABC):
    _precedence: int = 0

    @property
    def _syntax(self) -> str: ...

    @property
    def _formula(self) -> str:
        """Returns the syntax of this `Expr` with ref markers removed"""
        return self._syntax.replace("\0", "")

    @property
    def _access(self) -> "Expr":
        return self

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from ()

    @staticmethod
    def _instantiate(x: typing.Any) -> typing.Any:
        if Template is not None and isinstance(x, Template):
            return Expr._render_template(x)
        match x:
            case Expr():
                return str(x).replace("\0", "")
            case str():
                if "\0" in x:
                    _warn_fstring_deprecated()
                return x.replace("\0", "")
            case dict():
                return {
                    Expr._instantiate(k): Expr._instantiate(v) for k, v in x.items()
                }
            case list():
                return [Expr._instantiate(i) for i in x]
            case _:
                return x

    @staticmethod
    def _render_template(template: "Template") -> str:
        """Render a t-string into GHA syntax, e.g. `${{ matrix.x }}, ${{ matrix.y }}`."""
        parts = []
        for item in template:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item.value, Expr):
                parts.append(str(item.value).replace("\0", ""))
            else:
                parts.append(str(Expr._instantiate(item.value)))
        return "".join(parts)

    @staticmethod
    def _dedent_template(template: "Template") -> "Template":
        """`textwrap.dedent` equivalent for t-strings, keeping interpolations intact.

        Leading whitespace only lives in the static string parts, so dedent the
        parts as a whole (interpolations stand in as opaque, whitespace-free
        sentinels) and rebuild the template.
        """
        sentinel = "\0"
        shadow = textwrap.dedent(sentinel.join(template.strings).strip("\n"))
        new_strings = shadow.split(sentinel)
        parts: list[typing.Any] = []
        interpolations = template.interpolations
        for i, s in enumerate(new_strings):
            parts.append(s)
            if i < len(interpolations):
                parts.append(interpolations[i])
        return Template(*parts)

    @staticmethod
    def _paths(x: typing.Any) -> typing.Generator[tuple[str, ...], None, None]:
        if Template is not None and isinstance(x, Template):
            for interpolation in x.interpolations:
                yield from Expr._paths(interpolation.value)
            return
        match x:
            case Expr() as e:
                yield from e._get_paths()
            case str() as s:
                if "\0" in s:
                    _warn_fstring_deprecated()
                for match in re.finditer(
                    r"\0([\w.*-]+)((?:\['(?:[^']|'')*'\]|\[\d+\]|\.[\w*-]+)*)",
                    s,
                ):
                    path = tuple(match[1].split("."))
                    for segment in re.finditer(
                        r"\.([\w*-]+)|\['((?:[^']|'')*)'\]|\[(\d+)\]",
                        match[2],
                    ):
                        path += (
                            (
                                segment[1]
                                if segment[1] is not None
                                else (
                                    segment[2].replace("''", "'")
                                    if segment[2] is not None
                                    else "*"
                                )
                            ),
                        )
                    yield path
            case dict():
                for k, v in x.items():
                    yield from Expr._paths(k)
                    yield from Expr._paths(v)
            case list():
                for i in x:
                    yield from Expr._paths(i)
            case _:
                try:
                    fields = dataclasses.fields(x)
                except TypeError:
                    return
                for f in fields:
                    yield from Expr._paths(getattr(x, f.name))

    @staticmethod
    def _functions(value: typing.Any) -> typing.Generator[str, None, None]:
        if Template is not None and isinstance(value, Template):
            for item in value.interpolations:
                yield from Expr._functions(item.value)
        elif isinstance(value, CallExpr):
            yield value._function
            yield from Expr._functions(value._args)
        elif isinstance(value, RefExpr) and value._source is not None:
            yield from Expr._functions(value._source)
        elif isinstance(value, Expr):
            accessed = value._access
            if accessed is not value:
                yield from Expr._functions(accessed)
            elif dataclasses.is_dataclass(value):
                for field in dataclasses.fields(value):
                    yield from Expr._functions(getattr(value, field.name))
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from Expr._functions(key)
                yield from Expr._functions(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                yield from Expr._functions(item)
        elif isinstance(value, str):
            for formula in re.findall(
                r"\$\{\{((?:'(?:[^']|'')*'|[^'])*?)\}\}", value, re.S
            ):
                unquoted = re.sub(r"'(?:[^']|'')*'", "''", formula)
                yield from re.findall(r"\b(\w+)\s*\(", unquoted)

    def __str__(self) -> str:
        return f"${{{{ {self._syntax} }}}}"

    def __repr__(self) -> str:
        return self._instantiate(self._syntax)

    def _as_operand(self, op_precedence: int) -> str:
        if self._precedence > op_precedence:
            return f"({self._syntax})"
        return self._syntax

    def _operand_from(self, e: "Expr") -> str:
        return e._as_operand(self._precedence)

    @staticmethod
    def _coerce(x: typing.Any) -> "Expr":
        if isinstance(x, Expr):
            return x._access
        return LiteralExpr(x)

    def __and__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "&&")

    def __rand__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._coerce(other), self._access, "&&")

    def __or__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "||")

    def __ror__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._coerce(other), self._access, "||")

    def __invert__(self) -> "_access":
        return NotExpr(self._access)

    def __eq__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "==")

    def __ne__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "!=")

    def __le__(self, other) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "<=")

    def __lt__(self, other) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), "<")

    def __ge__(self, other) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), ">=")

    def __gt__(self, other) -> "Expr":
        return BinOpExpr(self._access, self._coerce(other), ">")

    def __getitem__(self, key: typing.Any) -> "Expr":
        return ItemExpr(self._access, self._coerce(key))

    def _attribute(self, key: str) -> "Expr":
        if key == "_":
            return DotExpr(self._access, "*")
        if key.startswith("_"):
            raise AttributeError(key)
        return DotExpr(self._access, key)

    if not typing.TYPE_CHECKING:
        __getattr__ = _attribute

    def __bool__(self):
        _ = self._access
        _current_on_error(
            f"expression {self._syntax} cannot be coerced to bool: did you mean to use `&` for `and` or `|` for `or`?",
        )
        return True


Value = str | bool | int | float | Expr


class DynamicExpr(Expr):
    def __getattr__(self, key: str) -> "DynamicExpr":
        return typing.cast(DynamicExpr, self._attribute(key))

    def __getitem__(self, key: typing.Any) -> "DynamicExpr":
        return ItemExpr(self._access, self._coerce(key))


instantiate = Expr._instantiate
dedent_template = Expr._dedent_template


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class _RootRewrite(Expr):
    """`Expr` wrapper that rewrites the leading segment of each ref it wraps.

    Stays an `Expr` (rather than collapsing to a marker string) so rendering
    keeps flowing through the clean object path.
    """

    _inner: Expr
    _from_root: str
    _to_root: str

    @property
    def _syntax(self) -> str:
        return self._inner._syntax.replace(f"\0{self._from_root}", f"\0{self._to_root}")

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        for path in self._inner._get_paths():
            if path[:1] == (self._from_root,):
                yield (self._to_root, *path[1:])
            else:
                yield path


def rewrite_ref_root(value: typing.Any, from_root: str, to_root: str) -> typing.Any:
    """Rewrite the leading ref segment of every context in `value`.

    E.g. turn `needs.<id>` into `jobs.<id>` for workflow-call outputs. The
    rewrite is type-preserving: `Expr` and `Template` values keep their type so
    they never masquerade as (deprecated) f-string marker strings. Only genuine
    f-string values (already plain `str` with markers) take the string path.
    """
    if isinstance(value, Expr):
        return _RootRewrite(value, from_root, to_root)
    if Template is not None and isinstance(value, Template):
        parts: list[typing.Any] = []
        for item in value:
            if isinstance(item, str) or not isinstance(item.value, Expr):
                parts.append(item)
            else:
                parts.append(
                    Interpolation(
                        _RootRewrite(item.value, from_root, to_root),
                        item.expression,
                        item.conversion,
                        item.format_spec,
                    )
                )
        return Template(*parts)
    if isinstance(value, str):
        return value.replace(f"\0{from_root}", f"\0{to_root}")
    return value


def reftree(x: typing.Any) -> RefTree:
    ret = {}
    for path in Expr._paths(x):
        r = ret
        for segment in path:
            r = r.setdefault(segment, {})
    return ret


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class RefExpr(Expr):
    _segments: tuple[str, ...]
    _child_factory: typing.Callable[[str], typing.Self] | None = None
    _source: Expr | None = None

    _store: typing.ClassVar[dict[tuple[str, ...], weakref.ReferenceType["RefExpr"]]] = (
        {}
    )

    @classmethod
    def _get(cls, *args: str) -> typing.Optional["RefExpr"]:
        return cls._store.get(args, lambda: None)()

    def __new__(cls, *args: str, **kwargs: typing.Any):
        # for some reason local variables here pollute PyCharm's autocomplete, use `_` prefix to
        # avoid that
        _ref = cls._get(*args)
        if _ref is not None:
            assert isinstance(
                _ref, cls
            ), f"{type(_ref).__name__}({", ".join(map(repr, args))}) was created before this {cls.__name__}"
            return _ref
        cls._store.pop(args, None)
        _instance = super().__new__(cls)
        cls._store[args] = weakref.ref(_instance)
        return _instance

    def __init__(self, *args: str):
        super().__init__()
        object.__setattr__(self, "_segments", args)

    @property
    def _path(self) -> str:
        return ".".join(self._segments)

    @property
    def _syntax(self) -> str:
        if self._source is not None:
            return self._source._syntax
        return f"\0{self._path}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield self._segments
        source = self._source
        while source is not None:
            match source:
                case ItemExpr():
                    yield from source._index._get_paths()
                    source = source._expr
                case DotExpr():
                    source = source._expr
                case RefExpr():
                    source = source._source
                case _:
                    break

    def _attribute(self, name: str) -> "Expr":
        if name == "_":
            if self._child_factory:
                return self._child_factory("*")
            return DotExpr(self, "*")
        if name.startswith("_"):
            raise AttributeError(name)
        if self._child_factory:
            return self._child_factory(name)
        return ~ErrorExpr(f"`{name}` not available in `{self._path}`")

    if not typing.TYPE_CHECKING:
        __getattr__ = _attribute


def contexts[T](cls: type[T]) -> type[T]:
    def process(ref, annotation):
        if annotation is RefExpr:
            return
        if isinstance(annotation, type) and hasattr(annotation, "_schema_id"):
            return

        use_dashes = getattr(annotation, "_use_dashes", False)

        def child_factory(key: str, a: type) -> RefExpr:
            key = key.replace("_", "-") if use_dashes else key
            cls = a if isinstance(a, type) and hasattr(a, "_schema_id") else RefExpr
            ret = cls(*ref._segments, key)
            process(ret, a)
            return ret

        if annotation is typing.Any:
            object.__setattr__(
                ref, "_child_factory", functools.partial(child_factory, a=typing.Any)
            )
            return

        annotations = annotation.__annotations__
        if any(isinstance(a, str) for a in annotations.values()):
            annotations = typing.get_type_hints(annotation)
        for f, a in annotations.items():
            # for mappings we use a `Map` annotation to `__getattr__` which will be picked up by some type checkers
            # (notably pylance in VSCode, while PyCharm doesn't seem to pick that up yet)
            if f == "__getattr__":
                assert typing.get_origin(a) is Map
                (child_annotation,) = typing.get_args(a)
                object.__setattr__(
                    ref,
                    "_child_factory",
                    functools.partial(child_factory, a=child_annotation),
                )
            else:
                object.__setattr__(ref, f, child_factory(f, a))

    class Root:
        _segments = ()

    root = Root()
    process(root, cls)
    for f, v in root.__dict__.items():
        setattr(cls, f, v)
    return cls


type Map[T] = typing.Callable[[str], T]


class FlatMap(RefExpr):
    __getattr__: Map[RefExpr]


_op_precedence = (
    ("[]",),
    ("!",),
    ("<", "<=", ">", ">=", "==", "!="),
    ("&&",),
    ("||",),
)
_op_precedence = {op: i for i, ops in enumerate(_op_precedence) for op in ops}


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class LiteralExpr[T](Expr):
    _value: T

    @property
    def _syntax(self) -> str:
        if isinstance(self._value, str):
            return f"'{self._value.replace("'", "''")}'"
        return repr(self._value)


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class BinOpExpr(Expr):
    _left: Expr
    _right: Expr
    _op: str

    @property
    def _precedence(self) -> int:
        return _op_precedence[self._op]

    @property
    def _syntax(self) -> str:
        return f"{self._operand_from(self._left)} {self._op} {self._operand_from(self._right)}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from self._left._get_paths()
        yield from self._right._get_paths()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class NotExpr(Expr):
    _expr: Expr

    @property
    def _precedence(self) -> int:
        return _op_precedence["!"]

    @property
    def _syntax(self) -> str:
        return f"!{self._operand_from(self._expr)}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from self._expr._get_paths()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class ItemExpr(DynamicExpr):
    _expr: Expr
    _index: Expr

    @property
    def _precedence(self) -> int:
        return _op_precedence["[]"]

    @property
    def _syntax(self) -> str:
        return f"{self._operand_from(self._expr)}[{self._index._syntax}]"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from self._expr._get_paths()
        yield from self._index._get_paths()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class DotExpr(DynamicExpr):
    _expr: Expr
    _attr: str

    @property
    def _syntax(self) -> str:
        return f"{self._operand_from(self._expr)}.{self._attr}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from self._expr._get_paths()


@dataclasses.dataclass(frozen=True, eq=False, repr=False)
class CallExpr(Expr):
    _function: str
    _args: tuple[Expr, ...]

    def __init__(self, function: str, *args: Expr):
        super().__init__()
        object.__setattr__(self, "_function", function)
        object.__setattr__(self, "_args", args)

    @property
    def _syntax(self) -> str:
        return f"{self._function}({', '.join(a._syntax for a in self._args)})"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        for a in self._args:
            yield from a._get_paths()


class DynamicCallExpr(CallExpr, DynamicExpr):
    pass


@dataclasses.dataclass(eq=False)
class ProxyExpr(Expr):
    _filled_expr: Expr | None = None

    def _get_expr(self) -> Expr: ...

    @property
    def _access(self) -> Expr:
        if self._filled_expr is None:
            self._filled_expr = self._get_expr()
            assert self._filled_expr is not None
        return self._filled_expr

    @property
    def _syntax(self) -> str:
        return self._access._syntax

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        return self._access._get_paths()

    def _attribute(self, item: str) -> typing.Any:
        if item in ("_access", "_syntax"):  # why is this necessary?
            return getattr(ProxyExpr, item).__get__(self)
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._access, item)

    if not typing.TYPE_CHECKING:
        __getattr__ = _attribute

        def __getitem__(self, key: typing.Any) -> Expr:
            return self._access[key]


@dataclasses.dataclass(eq=False)
class ErrorExpr(Expr):
    _error: str | typing.Callable[[], str]
    _emitted: bool = False

    def _emit(self) -> typing.Self:
        if not self._emitted:
            if callable(self._error):
                self._error = self._error()
            _current_on_error(self._error)
            self._emitted = True
        return self

    @property
    def _syntax(self) -> str:
        self._emit()
        e: Expr = CallExpr("error", self._coerce(self._error))
        return e._syntax

    def __and__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __rand__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __or__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __ror__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __invert__(self) -> Expr:
        return self._emit()

    def __eq__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __ne__(self, other: typing.Any) -> Expr:
        return self._emit()

    def __le__(self, other) -> Expr:
        return self._emit()

    def __lt__(self, other) -> Expr:
        return self._emit()

    def __ge__(self, other) -> Expr:
        return self._emit()

    def __gt__(self, other) -> Expr:
        return self._emit()

    def __getitem__(self, key: typing.Any) -> Expr:
        return self._emit()

    def __getattr__(self, key: str) -> Expr:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._emit()


def function(
    name: str, nargs: int = 1, vararg: types.EllipsisType | None = None
) -> typing.Callable[..., Expr]:
    def ret(*args: Expr, **kwargs: typing.Any) -> Expr:
        if kwargs:
            return ~ErrorExpr(
                f"unexpected keyword arguments to `{name}`, expected {nargs} positional arguments",
            )
        if vararg is None and len(args) != nargs:
            return ~ErrorExpr(
                f"wrong number of arguments to `{name}`, expected {nargs}, got {len(args)}"
            )
        if vararg is not None and len(args) < nargs:
            return ~ErrorExpr(
                f"not enough arguments to `{name}`, expected at least {nargs}, got {len(args)}"
            )
        return CallExpr(name, *(Expr._coerce(a) for a in args))

    return ret


def _current_on_error(message: str) -> None:
    raise ValueError(message)


@contextlib.contextmanager
def on_error(handler: typing.Callable[[str], typing.Any]):
    global _current_on_error
    old_error = _current_on_error
    _current_on_error = handler
    try:
        yield
    finally:
        _current_on_error = old_error
