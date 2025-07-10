import contextlib
import pathlib
import tempfile
import typing
import dataclasses
import functools
import subprocess
import re
from pathlib import PurePosixPath
from subprocess import CalledProcessError

import inflection
from ruamel.yaml import YAML

from ..element import ConfigElement, Element, fromobj

yaml = YAML()
yaml.default_flow_style = False


@functools.cache
def project_dir() -> pathlib.Path:
    path = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(path)


def config_file() -> pathlib.Path:
    return project_dir() / "gh-gen.yml"


class DiffError(Exception):
    def __init__(self, diff):
        super().__init__("generated code does not match expected")
        self.errors = diff


def relativized_path(p: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(p)
    if not p.is_absolute():
        return p
    cwd = pathlib.Path.cwd()
    try:
        return p.relative_to(cwd)
    except ValueError:
        return p


def load[T](ty: type[T], file: pathlib.Path) -> T:
    assert issubclass(
        ty, ConfigElement
    ), "load() can only be used with ConfigElement subclasses"
    try:
        data = yaml.load(file)
    except FileNotFoundError:
        return ty()
    return ty.fromdict(data)


def dump[T](data: T, file: pathlib.Path):
    match data:
        case Element():
            data = data.asdict()
        case dict():
            pass
        case _:
            assert False, f"Cannot dump {type(data)} to YAML"
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        yaml.dump(data, tmp)
    pathlib.Path(tmp.name).rename(file)
