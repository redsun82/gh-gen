import io
import pathlib
import typing
from dataclasses import dataclass
import dataclasses
import functools
import subprocess
import re

from ruamel.yaml import YAML, CommentedMap, yaml_object

yaml = YAML()
yaml.default_flow_style = False


@functools.cache
def project_dir() -> pathlib.Path:
    path = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(path)


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


yaml.register_class(DiffError)


@dataclasses.dataclass
@yaml_object(yaml)
@dataclass(kw_only=True)
class ActionInput:
    yaml_tag = "!input"
    id: str
    required: bool


def _make_id(s: str) -> str:
    return re.sub(r"[^a-z_]", "_", s.lower())


@dataclass(kw_only=True)
class Action:
    inputs: dict[str, ActionInput] = dataclasses.field(default_factory=dict)

    @staticmethod
    def from_spec(action: str) -> tuple[str, "Action"]:
        """Parse an action string into its name and optional version."""
        name = None
        version = None
        spec = action
        if "=" in spec:
            name, _, spec = spec.partition("=")
            if not name or re.search(f"[^a-z_]", name):
                raise ValueError(
                    f"Invalid name {name}, must be a valid lowercase identifier"
                )
        if "@" in spec:
            spec, _, version = spec.partition("@")
            if not version:
                raise ValueError(
                    f"Invalid action specification: {action} (version required after @)"
                )
        match spec.rstrip("/").split("/"):
            case [".", *_] if version:
                raise ValueError(
                    f"Invalid action specification: {action} (local action cannot have a reference)"
                )
            case ["."]:
                raise ValueError(
                    f"Invalid action specification: {action} (path required)"
                )
            case [".", head, *tail] if version is None:
                return name or _make_id(tail[-1]), LocalAction(
                    path=f"{head}/{'/'.join(tail)}"
                )
            case ["", *_] | [_, "", *_]:
                raise ValueError(
                    f"Invalid action specification: {action} (both owner and repo required)"
                )
            case [owner, repo, *path]:
                return name or _make_id("_".join([repo, *path])), RemoteAction(
                    owner=owner,
                    repo=repo,
                    path="/".join(path),
                    ref=version or None,
                )
            case _:
                raise ValueError(
                    f"Invalid action specification: {action} (expected 'owner/repo[@version]')"
                )

    def fetch(self): ...


@yaml_object(yaml)
@dataclass(kw_only=True)
class RemoteAction(Action):
    yaml_tag = "!remote"
    owner: str
    repo: str
    path: str
    ref: str | None = None
    sha: str | None = None

    def fetch(self):
        """Fetch inputs from the remote action repository."""
        contents_path = (
            pathlib.PurePosixPath("repos", self.owner, self.repo, "contents")
            / self.path
            / "action.yml"
        )
        with subprocess.Popen(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github.v3.raw",
                f"{contents_path}?ref={self.ref or 'main'}",
            ],
            text=True,
            stdout=subprocess.PIPE,
        ) as p:
            self.inputs = _parse_inputs(p.stdout)
        if p.returncode != 0:
            raise subprocess.CalledProcessError(
                p.returncode,
                "gh api",
            )


@yaml_object(yaml)
@dataclass(kw_only=True)
class LocalAction(Action):
    yaml_tag = "!local"
    path: str

    def fetch(self):
        """Fetch inputs from the local action directory."""
        source = project_dir().joinpath(self.path, "action.yml")
        if not source.exists():
            raise FileNotFoundError(f"Action inputs file not found: {source}")

        with source.open() as f:
            self.inputs = _parse_inputs(f)


def _parse_inputs(f: typing.IO[str]):
    action_data = yaml.load(f)
    return {
        id.replace("-", "_"): ActionInput(
            id=id, required=input_data.get("required", False)
        )
        for id, input_data in action_data.get("inputs", {}).items()
    }
