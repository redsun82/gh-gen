import dataclasses
import typing
import types

from ruamel.yaml import CommentedMap

from .expr import Expr, instantiate


@dataclasses.dataclass
class Element:
    _preserve_underscores: typing.ClassVar[bool] = False

    @classmethod
    def _key(cls, key: str) -> str:
        key = key.rstrip("_")
        if not cls._preserve_underscores:
            key = key.replace("_", "-").replace("--", "_")
        return key

    def asdict(self) -> typing.Any:
        return {
            self._key(k): asobj(v)
            for k, v in (
                (f.name, getattr(self, f.name)) for f in dataclasses.fields(self)
            )
            if v is not None
        }

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for f, a in cls.__annotations__.items():
            # add `None` as default value for all fields not having a default already
            if a is not dataclasses.KW_ONLY and not hasattr(cls, f):
                ty = cls.__annotations__[f]
                cls.__annotations__[f] |= None
                setattr(
                    cls,
                    f,
                    None,
                )
        if dataclasses.KW_ONLY not in cls.__annotations__.values():
            cls.__annotations__ = {"_": dataclasses.KW_ONLY} | cls.__annotations__

        def __repr__(self):
            args = ", ".join(
                f"{f}={v!r}"
                for f, v in (
                    (f.name, getattr(self, f.name))
                    for f in dataclasses.fields(self)
                    if f.repr
                )
                if v is not None
            )
            return f"{type(self).__name__}({args})"

        cls.__repr__ = __repr__
        dataclasses.dataclass(cls)


def asobj(o: typing.Any):
    match o:
        case Element() as e:
            return e.asdict()
        case Expr() | str():
            return instantiate(o)
        case dict() as d:
            return {instantiate(k): asobj(v) for k, v in d.items() if v is not None}
        case list() as l:
            return [asobj(x) for x in l]
        case _:
            return o


class ConfigElement(Element):
    yaml: CommentedMap = dataclasses.field(default_factory=CommentedMap, repr=False)

    @classmethod
    def fromdict(cls, d: dict[str, typing.Any]) -> typing.Self:
        return fromobj(d, cls)

    def asdict(self) -> typing.Any:
        ret = super().asdict()
        ret.pop("yaml", None)
        return ret

    def reload(self):
        self.__dict__.update(self.fromdict(self.yaml).__dict__)


def fromobj[T](x: typing.Any, t: type[T]) -> T:
    if typing.get_origin(t) is list:
        if not isinstance(x, list):
            raise ValueError(f"expected list, got {type(x)}")
        item_type = typing.get_args(t)[0]
        return [fromobj(v, item_type) for v in x]
    if typing.get_origin(t) is dict:
        if not isinstance(x, dict):
            raise ValueError(f"expected dict, got {type(x)}")
        key_type, value_type = typing.get_args(t)
        return {key_type(k): fromobj(v, value_type) for k, v in x.items()}
    if typing.get_origin(t) in (types.UnionType, typing.Union):
        for arg in typing.get_args(t):
            try:
                return fromobj(x, arg)
            except ValueError:
                continue
        raise ValueError(f"could not convert {x} to {t}")
    if t is types.NoneType or t is None:
        if x is not None:
            raise ValueError(f"expected None, got {type(x)}")
        return None
    if issubclass(t, ConfigElement):
        if not isinstance(x, dict):
            raise ValueError(f"expected dict, got {type(x)}")
        if t.__subclasses__():
            return fromobj(x, typing.Union[*t.__subclasses__()])
        fields = dataclasses.fields(t)
        args = {"yaml": x}
        for k, v in x.items():
            f = next((f for f in fields if t._key(f.name) == k), None)
            if f is None:
                raise ValueError(f"unknown configuration field {k} in {t.__name__}")
            args[f.name] = fromobj(v, f.type)
        return t(**args)
    return t(x)
