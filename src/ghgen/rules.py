import typing

from .types import RefTree
from .expr import RefExpr, reftree


def rule(e: RefExpr = RefExpr()):
    def decorator(f):
        assert callable(f)
        f.rule = e._segments
        f.rules = [*getattr(f, "rules", ()), e._segments]
        return f

    return decorator


class _RulesDict(dict):
    def __init__(self):
        super().__init__()
        self.rules = []

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        for path in getattr(value, "rules", ()):
            self.rules.append((path, value))


class _RuleSetMetaclass(type):
    _rules: dict[int, list[tuple[tuple[str, ...], typing.Callable]]]

    @classmethod
    def __prepare__(metacls, name, bases):
        return _RulesDict()

    def __new__(cls, name, bases, classdict):
        ret = super().__new__(cls, name, bases, dict(classdict))
        ret._rules = {}
        for base in bases:
            if isinstance(base, cls):
                ret._rules |= base._rules
        for r, func in classdict.rules:
            ret._rules.setdefault(len(r), []).append((r, func))
        return ret


class RuleSet(metaclass=_RuleSetMetaclass):
    @staticmethod
    def _match(lhs: tuple[str, ...], rhs: tuple[str, ...]) -> tuple[str, ...] | None:
        ret = ()
        for l, r in zip(lhs, rhs):
            if r == "*":
                ret += (l,)
            elif l != r:
                return None
        return ret

    @staticmethod
    def _traverse_reftree(
        reftree: RefTree, prefix: tuple[str, ...] = ()
    ) -> typing.Generator[tuple[str, ...], None, None]:
        yield prefix
        for k, rest in reftree.items():
            if k != "*":
                yield from RuleSet._traverse_reftree(rest, prefix + (k,))

    def validate(self, value: typing.Any, **kwargs: typing.Any) -> bool:
        tree = reftree(value)
        if not tree:
            return True
        for path in self._traverse_reftree(tree):
            for rule, func in self._rules.get(len(path), ()):
                m = self._match(path, rule)
                if m is not None:
                    if not func(self, *m, **kwargs):
                        return False
        return True
