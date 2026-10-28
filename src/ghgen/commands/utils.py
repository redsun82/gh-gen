import contextlib
import difflib
import io
import os
import pathlib
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


def dumps[T](data: T) -> str:
    match data:
        case Element():
            data = data.asdict()
        case dict():
            pass
        case _:
            assert False, f"Cannot dump {type(data)} to YAML"
    out = io.StringIO()
    yaml.dump(data, out)
    return out.getvalue()


def write(file: pathlib.Path, contents: str):
    """Atomically replace `file` with `contents`.

    The temporary file lives next to `file`, as renaming across filesystems fails.
    """
    tmp = file.with_name(f"{file.name}.tmp")
    tmp.write_text(contents)
    try:
        os.replace(tmp, file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def diff(file: pathlib.Path, contents: str) -> list[str]:
    """Unified diff from the current `file` to `contents` (empty if they match)."""
    try:
        current = file.read_text()
    except FileNotFoundError:
        current = ""
    return [
        l.rstrip("\n")
        for l in difflib.unified_diff(
            current.splitlines(keepends=True),
            contents.splitlines(keepends=True),
            str(file),
            f"{file} (expected)",
        )
    ]


def write_or_diff(file: pathlib.Path, contents: str, check: bool) -> list[str]:
    """In check mode, return the diff `write` would cause without writing; otherwise write."""
    if check:
        return diff(file, contents)
    write(file, contents)
    return []


def dump[T](data: T, file: pathlib.Path):
    write(file, dumps(data))
