import pathlib

from ..element import ConfigElement


class UsesClause(ConfigElement):
    uses: str
    name: str
    pin: bool = True


class Config(ConfigElement):
    includes: list[pathlib.Path]
    output_directory: str
    uses: dict[str, UsesClause | str]
