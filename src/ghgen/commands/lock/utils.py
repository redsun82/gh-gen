import dataclasses
import logging
import re
import contextlib
import subprocess
import typing
import argparse
import inflection
import pystache
import pathlib
import keyword

from ...element import ConfigElement
from ..utils import yaml, project_dir, load, dump
from ...config import UsesClause


class ActionInput(ConfigElement):
    name: str
    id: str
    required: bool


def _make_id(s: str) -> str:
    ret = re.sub(r"[^a-z_]", "_", s.lower())
    if keyword.iskeyword(ret):
        ret += "_"
    return ret


def is_valid_id(id: str) -> bool:
    """Check if the name is a valid Python identifier, lowercase, and not a keyword."""
    return id.isidentifier() and id.islower() and not keyword.iskeyword(id)


class Action(ConfigElement):
    id: str
    name: str
    inputs: list[ActionInput]
    outputs: list[str]

    @property
    def spec(self) -> str:
        raise NotImplementedError

    @property
    def resolved_spec(self) -> str:
        return self.spec

    @property
    def unversioned_spec(self) -> str:
        return self.spec

    @property
    def display_spec(self):
        return self.spec

    @property
    def comment(self) -> str | None:
        return None

    @property
    def has_outputs(self) -> bool:
        return bool(self.outputs)

    @staticmethod
    def from_spec(action: str) -> "Action":
        """Parse an action string into its name and optional version."""
        id = None
        version = None
        spec = action
        if "=" in spec:
            id, _, spec = spec.partition("=")
            if not is_valid_id(id):
                raise ValueError(
                    f"Invalid name {id}, must be a valid lowercase python identifier"
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
                return LocalAction(
                    id=id or _make_id(tail[-1]), path=f"{head}/{'/'.join(tail)}"
                )
            case ["", *_] | [_, "", *_]:
                raise ValueError(
                    f"Invalid action specification: {action} (both owner and repo required)"
                )
            case [owner, repo, *path]:
                return RemoteAction(
                    id=id or _make_id("_".join([repo, *path])),
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

    def _load(self, f: typing.IO[str]):
        action_data = yaml.load(f)
        self.inputs = [
            ActionInput(
                name=id.replace("-", "_"), id=id, required=input_data.get("required")
            )
            for id, input_data in action_data.get("inputs", {}).items()
        ]
        self.outputs = [*action_data.get("outputs", {})]
        if self.name is None:
            self.name = action_data.get("name")


class LockData(ConfigElement):
    actions: list[Action] = dataclasses.field(default_factory=list)


class LocalAction(Action):
    path: str

    @property
    def spec(self) -> str:
        return f"./{self.path}"

    def fetch(self):
        """Fetch inputs from the local action directory."""
        source = project_dir().joinpath(self.path, "action.yml")
        if not source.exists():
            raise FileNotFoundError(f"Action inputs file not found: {source}")

        with source.open() as f:
            self._load(f)


class RemoteAction(Action):
    owner: str
    repo: str
    path: str
    ref: str
    resolved_ref: str
    sha: str

    @property
    def spec(self) -> str:
        return f"{self.unversioned_spec}{"@" + self.ref if self.ref else ""}"

    @property
    def resolved_spec(self) -> str:
        return f"{self.unversioned_spec}@{self.sha}"

    @property
    def unversioned_spec(self) -> str:
        return str(pathlib.PurePosixPath(self.owner, self.repo, self.path))

    @property
    def display_spec(self) -> str:
        if self.resolved_ref != self.sha:
            return f"{self.resolved_spec} ({self.resolved_ref})"
        else:
            return self.resolved_spec

    @property
    def comment(self):
        if self.sha != self.resolved_ref:
            return self.resolved_ref
        else:
            return None

    @contextlib.contextmanager
    def _gh_api(self, mime: str, address: str, *args, **kwargs):
        with subprocess.Popen(
            [
                "gh",
                "api",
                "-H",
                f"Accept: {mime}",
                f"repos/{self.owner}/{self.repo}/{address}",
                *args,
            ],
            text=True,
            stdout=subprocess.PIPE,
            **kwargs,
        ) as p:
            yield p.stdout
        if p.returncode != 0:
            raise subprocess.CalledProcessError(
                p.returncode,
                f"gh api {address}",
            )

    def _gh_api_jq(self, address: str, jq: str, **kwargs) -> str:
        with self._gh_api(
            "application/vnd.github+json", address, "--jq", jq, **kwargs
        ) as out:
            return out.read().strip()

    def fetch(self):
        """Fetch inputs from the remote action repository."""
        self.resolved_ref = self.ref or self._gh_api_jq("releases/latest", ".tag_name")
        address = str(
            pathlib.PurePosixPath(
                "contents", self.path, f"action.yml?ref={self.resolved_ref}"
            )
        )
        with self._gh_api("application/vnd.github.v3.raw", address) as out:
            self._load(out)
        for kind in ("tags", "heads"):
            try:
                self.sha = self._gh_api_jq(
                    f"git/ref/{kind}/{self.resolved_ref}",
                    ".object.sha",
                    stderr=subprocess.DEVNULL,
                )
                break
            except subprocess.CalledProcessError:
                pass
        else:
            self.sha = self.resolved_ref


def sync_lock_data(
    args: argparse.Namespace,
    actions_to_update: list[str] | typing.Literal["all", "changed"] = "changed",
):
    uses = args.config.uses or {}
    lock_file = project_dir() / "gh-gen.lock"
    lock_data = load(LockData, lock_file)
    actions = {a.id: a for a in lock_data.actions}
    for id in list(actions):
        if id not in uses:
            del actions[id]
    match actions_to_update:
        case list():
            to_update = lambda id: id in actions_to_update
        case "all":
            to_update = lambda id: True
        case "changed":
            to_update = lambda id: id not in actions or actions[id].spec != spec
        case _:
            assert False, "actions_to_update must be a list, 'all', or 'changed'"
    for id, u in uses.items():
        match u:
            case UsesClause(uses=spec, name=name):
                pass
            case str() as spec:
                name = None
            case _:
                raise TypeError("malformed lock file")
        if not to_update(id):
            continue
        prev = actions.get(id)
        actions[id] = Action.from_spec(f"{id}={spec}")
        actions[id].name = name
        # TODO: async
        actions[id].fetch()
        if actions[id].name is None:
            # no name in original action source, derive from id
            actions[id].name = inflection.titleize(id).lower().capitalize()
        message = [f"{id}: "]
        if prev is None:
            message[0] += f"{actions[id].display_spec}"
        elif prev == actions[id]:
            message[0] += "✅"
        elif prev.display_spec != actions[id].display_spec:
            message[0] += f"{prev.display_spec}"
            message.append(f"    → {actions[id].display_spec}")
        elif prev.inputs != actions[id].inputs:
            message[0] += f"inputs updated"
        elif prev.name != actions[id].name:
            message[0] += f"name updated"
        for m in message:
            logging.info(m)
    lock_data.actions[:] = sorted(actions.values(), key=lambda a: a.id)
    dump(lock_data, lock_file)
    args.includes[0].mkdir(parents=True, exist_ok=True)
    generated = args.includes[0] / "actions.py"
    renderer = pystache.Renderer(search_dirs=[pathlib.Path(__file__).parent])
    with open(generated, "w") as out:
        out.write(renderer.render_name("actions", lock_data))
