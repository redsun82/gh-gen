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
import urllib.parse

from ...element import ConfigElement
from ..utils import yaml, project_dir, load, dumps, write_or_diff, DiffError
from ..config import Config, UsesClause

ACTIONS_LOCK = pathlib.PurePosixPath(".github", "workflows", "actions.lock")
# `pinned` value of actions whose pinning is left to `gh actions-lock`
ACTIONS_LOCK_PIN = "actions-lock"
Pin = typing.Literal["actions-lock"] | bool


def actions_lock_enabled(config: Config) -> bool:
    """Whether pinning is left to `gh actions-lock`."""
    if config.actions_lock is not None:
        return config.actions_lock
    return (project_dir() / ACTIONS_LOCK).exists()


_VERSION_REGEX = re.compile(r"(v?)(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)", re.ASCII)
_FULL_SHA_REGEX = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


class _Version(typing.NamedTuple):
    """Lenient version parsing, mirroring `SemVer` in github/actions-lockfile
    (go/pkg/lockfile/version.go), which `gh actions-lock` uses to narrow refs."""

    prefix: str
    major: int
    minor: int
    patch: int
    rest: str
    raw: str

    @classmethod
    def parse(cls, ref: str) -> typing.Self | None:
        if _FULL_SHA_REGEX.fullmatch(ref):
            return None
        m = _VERSION_REGEX.fullmatch(ref)
        if m is None:
            return None
        prefix, major, minor, patch, rest = m.groups()
        return cls(prefix, int(major), int(minor or 0), int(patch or 0), rest, ref)

    @property
    def is_major_only(self) -> bool:
        return self.raw == f"{self.prefix}{self.major}"

    @property
    def is_full(self) -> bool:
        return (
            not self.rest
            and not self.is_major_only
            and self.raw != f"{self.prefix}{self.major}.{self.minor}"
        )

    def narrows(self, other: "_Version") -> bool:
        return (
            self.is_full
            and self.major == other.major
            and (other.is_major_only or self.minor == other.minor)
        )


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
    pinned: typing.ClassVar[bool] = False

    id: str
    requested_name: str
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
    def from_spec(action: str, **kwargs) -> "Action":
        """Parse an action string into its id and optional version."""
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
            case ["." | "$", *_] if version:
                raise ValueError(
                    f"Invalid action specification: {action} (local action cannot have a reference)"
                )
            case ["." | "$"]:
                raise ValueError(
                    f"Invalid action specification: {action} (path required)"
                )
            case [("." | "$") as prefix, *path]:
                kwargs.pop("pinned", None)
                kwargs.pop("trusted", None)
                return LocalAction(
                    id=id or _make_id(path[-1]),
                    path="/".join(path),
                    same_repo=prefix == "$" or None,
                    **kwargs,
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
                    **kwargs,
                )
            case _:
                raise ValueError(
                    f"Invalid action specification: {action} (expected 'owner/repo[@version]')"
                )

    def fetch(self, default_ref: str | None = None, locked_sha: str | None = None): ...

    def _load(self, f: typing.IO[str]):
        action_data = yaml.load(f)
        self.inputs = [
            ActionInput(
                name=id.replace("-", "_"), id=id, required=input_data.get("required")
            )
            for id, input_data in action_data.get("inputs", {}).items()
        ]
        self.outputs = [*action_data.get("outputs", {})]
        if self.requested_name is not None:
            self.name = self.requested_name
        elif "name" in action_data:
            self.name = action_data["name"]
        else:
            # no name in original action source, derive from id
            self.name = inflection.titleize(self.id).lower().capitalize()


class LockData(ConfigElement):
    # Accepted only to migrate older lockfiles.
    actions_lock: bool
    actions: list[Action] = dataclasses.field(default_factory=list)


class LocalAction(Action):
    path: str
    # referenced as `$/path` (repository at the running commit) rather than `./path`
    same_repo: bool

    @property
    def spec(self) -> str:
        return f"{'$' if self.same_repo else '.'}/{self.path}"

    def fetch(self, default_ref: str | None = None, locked_sha: str | None = None):
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
    pinned: Pin = True
    # only set without `gh actions-lock`, where it affects resolution
    trusted: bool

    @property
    def spec(self) -> str:
        return f"{self.unversioned_spec}{"@" + self.ref if self.ref else ""}"

    @property
    def resolved_spec(self) -> str:
        return f"{self.unversioned_spec}@{self.sha or self.resolved_ref}"

    @property
    def unversioned_spec(self) -> str:
        return str(pathlib.PurePosixPath(self.owner, self.repo, self.path))

    @property
    def display_spec(self) -> str:
        if self.sha and self.resolved_ref != self.sha:
            return f"{self.resolved_spec} ({self.resolved_ref})"
        else:
            return self.resolved_spec

    @property
    def comment(self):
        if self.sha and self.sha != self.resolved_ref:
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

    def _gh_api_jq(self, address: str, jq: str, *args, **kwargs) -> str:
        with self._gh_api(
            "application/vnd.github+json", address, "--jq", jq, *args, **kwargs
        ) as out:
            return out.read().strip()

    def _narrow(self, ref: str, locked_sha: str | None = None) -> str:
        """Narrow a partial version ref (`v4`, `v4.2`) to the highest full version tag in
        its family on the same commit, as `gh actions-lock` would (see `BestPatchTagForSHA`
        in github/gh-actions-lock). Full versions are returned as is.

        With `locked_sha`, that commit is used rather than where `ref` points now, and it
        is what anything that cannot be narrowed becomes, so as not to upgrade."""
        version = _Version.parse(ref)
        if version and version.is_full:
            return ref
        fallback = locked_sha or ref
        if version is None:
            return fallback
        sha = (locked_sha or self._gh_api_jq(f"commits/{ref}", ".sha")).lower()
        tags = self._gh_api_jq(
            "tags", r'.[] | "\(.commit.sha) \(.name)"', "--paginate"
        ).splitlines()
        candidates = [
            v
            for tag_sha, _, name in (t.partition(" ") for t in tags)
            if tag_sha.lower() == sha
            and (v := _Version.parse(name))
            and v.narrows(version)
        ]
        if not candidates:
            return fallback
        best = max(
            candidates,
            key=lambda v: (v.major, v.minor, v.patch, v.prefix == "v", v.raw),
        )
        return best.raw

    def fetch(self, default_ref: str | None = None, locked_sha: str | None = None):
        """Fetch inputs from the remote action repository.

        Without a requested ref, `default_ref` or else the latest release is used.
        `locked_sha` is the commit that was previously locked, which `actions-lock`
        narrowing sticks to."""
        self.resolved_ref = (
            self.ref or default_ref or self._gh_api_jq("releases/latest", ".tag_name")
        )
        if self.pinned == ACTIONS_LOCK_PIN:
            self.resolved_ref = self._narrow(self.resolved_ref, locked_sha)
        elif not self.ref and self.trusted and not self.pinned:
            # take just the major version
            self.resolved_ref = self.resolved_ref.partition(".")[0]
        if self.pinned is True:
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
        ref = urllib.parse.quote(self.sha or self.resolved_ref)
        address = str(
            pathlib.PurePosixPath("contents", self.path, f"action.yml?ref={ref}")
        )
        with self._gh_api("application/vnd.github.v3.raw", address) as out:
            self._load(out)


class ActionDescription(typing.NamedTuple):
    id: str
    spec: str
    pin: Pin
    trust: bool
    name: str | None


def sync_lock_data(
    args: argparse.Namespace,
    actions_to_update: list[str] | typing.Literal["all", "changed"] = "changed",
):
    uses = args.config.uses or {}
    lock_file = project_dir() / "gh-gen.lock"
    lock_data = load(LockData, lock_file)
    if lock_data.actions_lock:
        for action in lock_data.actions:
            if isinstance(action, RemoteAction):
                action.pinned = ACTIONS_LOCK_PIN
                action.trusted = None
    lock_data.actions_lock = None
    actions_lock = actions_lock_enabled(args.config)
    actions = {a.id: a for a in lock_data.actions}
    for id in list(actions):
        uses.setdefault(id, None)
    to_update: typing.Callable[[Action | None, Action], bool]
    match actions_to_update:
        case list():
            to_update = (
                lambda prev, new: new is not None and new.id in actions_to_update
            )
        case "all":
            to_update = lambda prev, new: True
        case "changed":
            to_update = lambda prev, new: prev != new
        case _:
            assert False, "actions_to_update must be a list, 'all', or 'changed'"

    def get_description(
        id: str, spec: str, name: str | None = None, pin: bool | None = None
    ) -> bool:
        owner, _, _ = spec.partition("/")
        trusted = owner in args.config.trusted_owners
        if actions_lock:
            if pin:
                raise ValueError(
                    f"{id}: cannot pin with actions-lock enabled, `gh actions-lock` does the pinning"
                )
            pin = ACTIONS_LOCK_PIN
        return ActionDescription(
            id,
            spec,
            pin=pin if pin is not None else not trusted,
            trust=trusted,
            name=name,
        )

    def get_pinned_value(request: bool | None, trusted: bool) -> bool:
        return request if request is not None else not trusted

    for id, u in uses.items():
        match u:
            case UsesClause(uses=spec, name=name, pin=pin):
                new = get_description(id, spec, name, pin)
            case str() as spec:
                new = get_description(id, spec)
            case None:
                new = None
            case _:
                raise TypeError("malformed lock file")
        prev = actions.get(id)
        prev_desc = prev and ActionDescription(
            prev.id,
            prev.spec,
            prev.pinned,
            prev.spec.partition("/")[0] in args.config.trusted_owners,
            prev.requested_name,
        )
        # pinning follows the mode, it was not requested
        mode_changed = (
            bool(prev_desc and new)
            and (prev_desc.pin == ACTIONS_LOCK_PIN) != actions_lock
        )
        if mode_changed:
            prev_desc = prev_desc._replace(pin=new.pin)
        default_ref = locked_sha = None
        if not to_update(prev_desc, new):
            if not mode_changed:
                continue
            # re-resolve without upgrading
            default_ref = getattr(prev, "resolved_ref", None)
            locked_sha = getattr(prev, "sha", None)
        if new is None:
            del actions[id]
            continue
        actions[id] = Action.from_spec(
            f"{new.id}={new.spec}",
            requested_name=new.name,
            pinned=new.pin,
            trusted=None if actions_lock else new.trust,
        )
        # TODO: async
        actions[id].fetch(default_ref, locked_sha)

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
    check = getattr(args, "check", False)
    if not check:
        args.includes[0].mkdir(parents=True, exist_ok=True)
    renderer = pystache.Renderer(search_dirs=[pathlib.Path(__file__).parent])
    errors = write_or_diff(lock_file, dumps(lock_data), check) + write_or_diff(
        args.includes[0] / "actions.py",
        renderer.render_name("actions", lock_data),
        check,
    )
    if errors:
        raise DiffError(errors)
