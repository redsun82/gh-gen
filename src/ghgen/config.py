import pathlib

from .element import ConfigElement


class UsesClause(ConfigElement):
    uses: str
    title: str


class Config(ConfigElement):
    includes: list[pathlib.Path]
    output_directory: str
    uses: dict[str, UsesClause | str]
