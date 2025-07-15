import dataclasses
import difflib
import os
import sys
import textwrap

import pytest

from src.ghgen.ctx import workflow, GenerationError, _get_var_name
from src.ghgen.commands.generate import generate_workflow
import pathlib
import inspect
import dis
import itertools
import subprocess


def pytest_addoption(parser):
    parser.addoption("--learn", action="store_true")


@dataclasses.dataclass(frozen=True)
class _Call:
    name: str
    file: pathlib.Path
    position: dis.Positions

    @classmethod
    def get(cls, name=None):
        frame = inspect.getframeinfo(inspect.currentframe().f_back)
        call_frame = inspect.getframeinfo(inspect.currentframe().f_back.f_back)
        return cls(
            name or frame.function,
            pathlib.Path(call_frame.filename),
            call_frame.positions,
        )

    def __str__(self):
        return (
            f"{self.name}@{self.file}:{self.position.lineno}:{self.position.end_lineno}"
        )


_learn = pytest.StashKey[list[tuple[_Call, str | None]]]()


def pytest_configure(config: pytest.Config):
    config.stash[_learn] = []


def expect(expected: str | None = None):
    assert not callable(expected), "replace @expect with @expect()"
    expected = expected and textwrap.dedent(expected)
    call = _Call.get()

    def decorator(f):
        def wrapper(pytestconfig: pytest.Config):
            wf = workflow(f)
            output = generate_workflow(wf, pathlib.Path(inspect.getfile(f)).parent)
            with open(output) as out:
                actual = [l.rstrip("\n") for l in out]
            if expected is None or pytestconfig.getoption("--learn"):
                pytestconfig.stash[_learn].append((call, "\n".join(actual)))
            else:
                assert actual == expected.splitlines()
            output.unlink()

        return wrapper

    return decorator


@pytest.fixture
def error(request):
    expected_errors = []

    class Wrapper:
        id = None
        err = None

        @property
        def is_primed(self) -> bool:
            try:
                _ = self.actual
                return True
            except (AttributeError, AssertionError):
                return False

        @property
        def actual(self):
            return self.err.value

        def __call__(self, expected=None):
            expected_errors.append((_Call.get("error"), expected))

        def __enter__(self):
            self.ctx_manager = pytest.raises(GenerationError)
            self.err = self.ctx_manager.__enter__()
            return self.err

        def __exit__(self, *args):
            ret = self.ctx_manager.__exit__(*args)
            return ret

    e = Wrapper()
    yield e

    assert e.err, "`error` was not updated as a context manager"
    if not e.is_primed:
        return
    this_file = request.node.path
    actual = {}
    for err in e.actual.errors:
        assert pathlib.Path(err.filename) == this_file, f"unexpected filename: {err}"
        assert e.id == err.workflow_id, f"unexpected workflow id: {err.workflow_id}"
        assert (
            err.lineno not in actual
        ), f"multiple errors on the same line, that's not yet supported:\n* {actual[err.lineno]}\n* {err.message}"
        actual[err.lineno] = err.message
    if request.config.getoption("--learn"):
        for call, expected in expected_errors:
            request.config.stash[_learn].append((call, None))
        for lineno, message in actual.items():
            request.config.stash[_learn].append(
                (
                    _Call("error", this_file, dis.Positions(lineno)),
                    message,
                )
            )
    else:
        expected = {}
        for call, e in expected_errors:
            if e is None:
                actual_error = actual.pop(call.position.end_lineno + 1, None)
                assert (
                    actual_error
                ), f"missing error at line {call.position.end_lineno + 1}"
                request.config.stash[_learn].append((call, actual_error))
            else:
                expected[call.position.end_lineno + 1] = e
        assert actual == expected, f"errors do not match"


def expect_errors(func):
    def wrapper(error):
        error.id = func.__name__
        with error:
            wf = workflow(lambda: func(error), id=func.__name__)
            _ = wf.worfklow

    return wrapper


class TestRepo:
    class File:
        def __init__(
            self,
            config: pytest.Config,
            path: str | os.PathLike,
            contents: str | None = None,
        ):
            self._config = config
            path = pathlib.Path(path)
            assert not path.is_absolute()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.path = path
            contents = contents and textwrap.dedent(contents)
            self.contents = contents or ""
            if contents is not None:
                path.write_text(contents)

        def expect_unchanged(self):
            if self.contents is None:
                assert not self.path.exists(), f"{self.path} should not exist"
            else:
                assert self.path.exists(), f"{self.path} is not present"
                assert self.path.read_text() == self.contents, f"{self.path} changed"

        def expect_diff(self, diff: str = ""):
            def split(s):
                return s.splitlines(keepends=True)

            diff = textwrap.dedent(diff)
            up = inspect.currentframe().f_back
            name = next(var for var, value in up.f_locals.items() if value is self)
            call = _Call.get(f"{name}.expect_diff")
            assert self.path.exists(), f"{self.path} was not created"
            new_contents = self.path.read_text()
            actual_diff = difflib.unified_diff(
                split(self.contents),
                split(new_contents),
            )
            assert next(
                actual_diff, None
            ), f"{self.path} remained unchanged"  # skip ---
            next(actual_diff)  # skip +++
            actual_diff = list(actual_diff)
            expected_diff = split(diff.lstrip())
            if not self._config.getoption("--learn"):
                assert (
                    actual_diff == expected_diff
                ), f"diff for {self.path} does not match expected one"
            elif actual_diff != expected_diff:
                self._config.stash[_learn].append((call, "".join(actual_diff).rstrip()))
            self.contents = new_contents

    def __init__(self, config: pytest.Config, path: pathlib.Path):
        self._config = config
        self.path = path
        self.files = {}

    def __enter__(self):
        self._cwd = pathlib.Path.cwd()
        subprocess.run(["git", "init"], cwd=self.path, check=True)
        os.chdir(self.path)
        return self

    def __exit__(self, *args):
        os.chdir(self._cwd)

    def file(self, path: str | os.PathLike, contents: str | None = None) -> File:
        return self.File(self._config, path, contents)

    def config(self, contents: str | None = None) -> File:
        return self.file("gh-gen.yml", contents)

    def lock(self, contents: str | None = None) -> File:
        return self.file("gh-gen.lock", contents)


@pytest.fixture
def repo(pytestconfig: pytest.Config, tmp_path: pathlib.Path):
    """
    Fixture to create a temporary git repository for testing.
    """
    with TestRepo(pytestconfig, tmp_path) as repo:
        yield repo


def pytest_unconfigure(config):
    changes = {}
    for call, expected in config.stash[_learn]:
        changes.setdefault(call.file, []).append((call.position, call.name, expected))
    for v in changes.values():
        v.sort()
    for f, v in changes.items():
        bkp = f.with_suffix(f"{f.suffix}.bkp")
        f.rename(bkp)
        with open(bkp) as input, open(f, "w") as output:
            input = iter(input)
            current = 1
            for position, name, expected in v:
                for _ in range(position.lineno - current):
                    output.write(next(input))
                peek = next(input)
                input = itertools.chain([peek], input)
                offset = position.col_offset
                if offset is None:
                    offset = len(peek) - len(peek.lstrip())
                if position.end_lineno:
                    if expected:
                        output.write(peek[:offset])
                    for _ in range(position.end_lineno - position.lineno + 1):
                        next(input)
                elif expected:
                    output.write(offset * " ")
                if expected and "\n" in expected:
                    # cover case where offset == 1 because of `@expect`
                    offset = offset // 4 * 4 + 4
                    expected = expected.replace("\n", "\n" + " " * offset)
                    print(
                        f'{name}(\n    """\\\n{"":{offset}}{expected}\n{"":{offset}}"""\n)',
                        file=output,
                    )
                elif expected:
                    print(f"{name}({expected!r})", file=output)
                current = (
                    position.end_lineno + 1 if position.end_lineno else position.lineno
                )
            for line in input:
                output.write(line)
    if changes:
        subprocess.run([sys.executable, "-m", "black"] + [f for f in changes])
