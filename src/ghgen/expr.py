import dataclasses
import abc
import functools
import re
import typing
import weakref
import contextlib

from .types import RefTree


class Expr(abc.ABC):
    _precedence: int = 0

    @property
    def _syntax(self) -> str: ...

    @property
    def _formula(self) -> str:
        """Returns the syntax of this `Expr` with ref markers removed"""
        return self._syntax.replace("\0", "")

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from ()

    @staticmethod
    def _instantiate(x: typing.Any) -> typing.Any:
        match x:
            case Expr() | str():
                return str(x).replace("\0", "")
            case dict():
                return {
                    Expr._instantiate(k): Expr._instantiate(v) for k, v in x.items()
                }
            case list():
                return [Expr._instantiate(i) for i in x]
            case _:
                return x

    @staticmethod
    def _paths(x: typing.Any) -> typing.Generator[tuple[str, ...], None, None]:
        match x:
            case Expr() as e:
                yield from e._get_paths()
            case str() as s:
                for m in re.finditer("\0([a-zA-Z0-9\\-_.]+)", s):
                    yield tuple(m[1].split("."))
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

    def __str__(self) -> str:
        return f"${{{{ {self._syntax} }}}}"

    def _as_operand(self, op_precedence: int) -> str:
        if self._precedence > op_precedence:
            return f"({self._syntax})"
        return self._syntax

    def _operand_from(self, e: "Expr") -> str:
        return e._as_operand(self._precedence)

    @staticmethod
    def _coerce(x: typing.Any) -> "Expr":
        if isinstance(x, Expr):
            return x
        return LiteralExpr(x)

    def __and__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "&&")

    def __rand__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._coerce(other), self, "&&")

    def __or__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "||")

    def __ror__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self._coerce(other), self, "||")

    def __invert__(self) -> "Expr":
        return NotExpr(self)

    def __eq__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "==")

    def __ne__(self, other: typing.Any) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "!=")

    def __le__(self, other) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "<=")

    def __lt__(self, other) -> "Expr":
        return BinOpExpr(self, self._coerce(other), "<")

    def __ge__(self, other) -> "Expr":
        return BinOpExpr(self, self._coerce(other), ">=")

    def __gt__(self, other) -> "Expr":
        return BinOpExpr(self, self._coerce(other), ">")

    def __getitem__(self, key: typing.Any) -> "Expr":
        return ItemExpr(self, self._coerce(key))

    def __getattr__(self, key: str) -> typing.Self:
        if key == "_":
            return DotExpr(self, "*")
        if key.startswith("_"):
            raise AttributeError(key)
        return DotExpr(self, key)

    def __bool__(self):
        _current_on_error(
            f"expression {self._syntax} cannot be coerced to bool: did you mean to use `&` for `and` or `|` for `or`?",
        )
        return True


Value = str | bool | int | float | Expr


instantiate = Expr._instantiate


def reftree(x: typing.Any) -> RefTree:
    ret = {}
    for path in Expr._paths(x):
        r = ret
        for segment in path:
            r = r.setdefault(segment, {})
    return ret


@dataclasses.dataclass(frozen=True, eq=False)
class RefExpr(Expr):
    _segments: tuple[str, ...]
    _child_factory: typing.Callable[[str], typing.Self] | None = None

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
        return f"\0{self._path}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield self._segments

    def __getattr__(self, name) -> Expr:
        if name == "_":
            if self._child_factory:
                return self._child_factory("*")
            return DotExpr(self, "*")
        if name.startswith("_"):
            raise AttributeError(name)
        if self._child_factory:
            return self._child_factory(name)
        return ~ErrorExpr(f"`{name}` not available in `{self._path}`")


def contexts[T](cls: type[T]) -> type[T]:
    def process(ref, annotation):
        if annotation is RefExpr:
            return

        use_dashes = getattr(annotation, "_use_dashes", False)

        def child_factory(key: str, a: type) -> RefExpr:
            key = key.replace("_", "-") if use_dashes else key
            ret = RefExpr(*ref._segments, key)
            process(ret, a)
            return ret

        if annotation is typing.Any:
            object.__setattr__(
                ref, "_child_factory", functools.partial(child_factory, a=typing.Any)
            )
            return

        for f, a in annotation.__annotations__.items():
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


@dataclasses.dataclass(frozen=True, eq=False)
class LiteralExpr[T](Expr):
    _value: T

    @property
    def _syntax(self) -> str:
        if isinstance(self._value, str):
            return f"'{self._value.replace("'", "''")}'"
        return repr(self._value)


@dataclasses.dataclass(frozen=True, eq=False)
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


@dataclasses.dataclass(frozen=True, eq=False)
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


@dataclasses.dataclass(frozen=True, eq=False)
class ItemExpr(Expr):
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


@dataclasses.dataclass(frozen=True, eq=False)
class DotExpr(Expr):
    _expr: Expr
    _attr: str

    @property
    def _syntax(self) -> str:
        return f"{self._operand_from(self._expr)}.{self._attr}"

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        yield from self._expr._get_paths()


@dataclasses.dataclass(frozen=True, eq=False)
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


@dataclasses.dataclass
class ProxyExpr(Expr):
    _filled_expr: Expr | None = None

    def _get_expr(self) -> Expr: ...

    @property
    def _expr(self) -> Expr:
        if self._filled_expr is None:
            self._filled_expr = self._get_expr()
            assert self._filled_expr is not None
        return self._filled_expr

    @property
    def _syntax(self) -> str:
        return self._expr._syntax

    def _get_paths(self) -> typing.Generator[tuple[str, ...], None, None]:
        return self._expr._get_paths()

    def __getattr__(self, item: str) -> typing.Any:
        if item in ("_expr", "_syntax"):  # why is this necessary?
            return getattr(ProxyExpr, item).__get__(self)
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._expr, item)


@dataclasses.dataclass
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


def function(name: str, nargs: int = 1) -> typing.Callable[..., Expr]:
    def ret(*args: Expr, **kwargs: typing.Any) -> Expr:
        if kwargs:
            return ~ErrorExpr(
                f"unexpected keyword arguments to `{name}`, expected {nargs} positional arguments",
            )
        if len(args) != nargs:
            return ~ErrorExpr(
                f"wrong number of arguments to `{name}`, expected {nargs}, got {len(args)}"
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
