import dataclasses
import pathlib
import typing

from ..element import ConfigElement


class UsesClause(ConfigElement):
    uses: str
    name: str
    pin: bool


class Config(ConfigElement):
    default_trusted_owners: typing.ClassVar[list[str]] = ["actions"]

    includes: list[pathlib.Path]
    output_directory: str
    trusted_owners: list[str] = dataclasses.field(
        default_factory=lambda: list(Config.default_trusted_owners)
    )
    uses: dict[str, UsesClause | str]

    def asdict(self):
        ret = super().asdict()
        if ret["trusted_owners"] == self.default_trusted_owners:
            del ret["trusted_owners"]
        return ret
