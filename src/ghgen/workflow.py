import dataclasses
import typing

from ruamel.yaml import CommentedSeq

from .element import Element, asobj
from typing import Any, cast
from .expr import Value, Expr, ProxyExpr, RefExpr, instantiate, ErrorExpr
from dataclasses import field
from ruamel.yaml.scalarstring import LiteralScalarString
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _set_flow_style(d: dict, *fields) -> dict:
    for f in fields:
        if f not in d:
            continue
        d[f] = _with_flow_style(d[f])
    return d


def _with_flow_style(v: dict | list) -> CommentedSeq | CommentedMap:
    match v:
        case list():
            ret = CommentedSeq(v)
        case dict():
            ret = CommentedMap(v)
        case _:
            assert False
    ret.fa.set_flow_style()
    return ret


class Input[T](Element):
    Type: typing.ClassVar[type] = typing.Literal[
        "boolean", "choice", "number", "environment", "string"
    ]

    description: str
    _: dataclasses.KW_ONLY
    id: str
    required: bool = False
    default: T
    type: Type = "string"
    options: list[str]

    def asdict(self) -> typing.Any:
        return _flow_text(super().asdict(), "description")

    def __post_init__(self):
        if self.default is not None:
            self.type = type(self.default)
        if self.type is bool:
            self.type = "boolean"
        elif self.type in (int, float):
            self.type = "number"
        elif self.type is str:
            self.type = "string"
        elif typing.get_origin(self.type) is typing.Literal:
            self.options = list(typing.get_args(self.type))
            self.type = "choice"
        elif (typing.get_origin(self.type) or self.type) is dict:
            self.type = "environment"
        elif self.type not in (None,) + tuple(typing.get_args(self.Type)):
            raise ValueError(f"unexpected input type `{self.type}`")
        if self.options:
            self.type = "choice"


class Secret(Element):
    description: str
    _: dataclasses.KW_ONLY
    id: str
    required: bool = False

    def asdict(self) -> typing.Any:
        return _flow_text(super().asdict(), "description")


class Output(Element):
    description: str
    _: dataclasses.KW_ONLY
    id: str
    value: Value

    def asdict(self) -> typing.Any:
        return _flow_text(super().asdict(), "description")


class Trigger(Element):
    pass


class TypedTrigger(Trigger):
    types: list[str]


class StrictTypedTrigger(type):
    def __new__(cls, *types: str):
        return type(
            f"StrictTypedTrigger[{', '.join(map(repr, types))}]",
            (TypedTrigger,),
            {
                "allowed_types": tuple(sorted(types)),
            },
        )


class ChangeTrigger(Trigger):
    branches: list[str]
    ignore_branches: list[str]
    paths: list[str]
    ignore_paths: list[str]


class PullRequest(
    ChangeTrigger,
    StrictTypedTrigger(
        "assigned",
        "unassigned",
        "labeled",
        "unlabeled",
        "opened",
        "edited",
        "closed",
        "reopened",
        "synchronize",
        "converted_to_draft",
        "locked",
        "unlocked",
        "enqueued",
        "dequeued",
        "milestoned",
        "demilestoned",
        "ready_for_review",
        "review_requested",
        "review_request_removed",
        "auto_merge_enabled",
        "auto_merge_disabled",
    ),
):
    pass


class PullRequestTarget(
    ChangeTrigger,
    StrictTypedTrigger(
        "assigned",
        "unassigned",
        "labeled",
        "unlabeled",
        "opened",
        "edited",
        "closed",
        "reopened",
        "synchronize",
        "converted_to_draft",
        "ready_for_review",
        "locked",
        "unlocked",
        "review_requested",
        "review_request_removed",
        "auto_merge_enabled",
        "auto_merge_disabled",
    ),
):
    pass


class Push(ChangeTrigger):
    tags: list[str]
    ignore_tags: list[str]


class Schedule(Trigger):
    cron: str


def _dictionarize(d: dict, *args: str) -> dict:
    for k in args:
        if k not in d:
            continue
        serialized: list = d.pop(k)
        print(serialized)
        d[k] = {id: x for id, x in ((e.pop("id", None), e) for e in serialized) if id}
    return d


class WorkflowDispatch(Trigger):
    inputs: list[Input]

    def asdict(self) -> typing.Any:
        return _dictionarize(super().asdict(), "inputs")


class WorkflowCall(Trigger):
    inputs: list[Input]
    secrets: list[Secret]
    outputs: list[Output]

    def asdict(self) -> typing.Any:
        return _dictionarize(super().asdict(), "inputs", "secrets", "outputs")


class ProxyList[T](list[T]):
    def __init__(self):
        super().__init__()
        self.proxied = []

    def append(self, new: T):
        super().append(new)
        for p in self.proxied:
            p.append(new)

    def extend(self, iterable: typing.Iterable[T]):
        new = list(iterable)
        super().extend(new)
        for p in self.proxied:
            p.extend(new)

    def add_proxied(self, l: list[T]):
        super().extend(l)
        self.proxied.append(l)


class _ProxyListAttr[T]:
    def __init__(self, *attrs: str):
        self.attrs = attrs

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner) -> list[T] | None:
        if instance is None:
            return None
        return self._get(instance)

    def _get(self, instance) -> ProxyList[T] | None:
        ret = ProxyList()
        for a in self.attrs:
            parent = getattr(instance, a)
            if parent is None:
                continue
            elements = getattr(parent, self.name)
            if elements is None:
                elements = []
                setattr(parent, self.name, elements)
            ret.add_proxied(elements)
        return ret

    def __set__(self, instance, value):
        pass


class On(Element):
    _preserve_underscores = True

    branch_protection_rule: StrictTypedTrigger("created", "edited", "deleted")
    check_run: StrictTypedTrigger(
        "created", "completed", "requested_action", "rerequested"
    )
    check_suite: StrictTypedTrigger("completed")
    create: Trigger
    delete: Trigger
    deployment: Trigger
    deployment_status: Trigger
    discussion: StrictTypedTrigger(
        "created",
        "edited",
        "deleted",
        "transferred",
        "pinned",
        "unpinned",
        "labeled",
        "unlabeled",
        "locked",
        "unlocked",
        "category_changed",
        "answered",
        "unanswered",
    )
    discussion_comment: StrictTypedTrigger("created", "edited", "deleted")
    fork: Trigger
    gollum: Trigger
    issue_comment: StrictTypedTrigger("created", "edited", "deleted")
    issues: StrictTypedTrigger(
        "opened",
        "edited",
        "deleted",
        "transferred",
        "pinned",
        "unpinned",
        "closed",
        "reopened",
        "assigned",
        "unassigned",
        "labeled",
        "unlabeled",
        "locked",
        "unlocked",
        "milestoned",
        "demilestoned",
    )
    label: StrictTypedTrigger("created", "edited", "deleted")
    merge_group: StrictTypedTrigger("checks_requested")
    milestone: StrictTypedTrigger("created", "closed", "opened", "edited", "deleted")
    page_build: Trigger
    public: Trigger
    pull_request: PullRequest
    pull_request_review: StrictTypedTrigger("submitted", "edited", "dismissed")
    pull_request_review_comment: StrictTypedTrigger("created", "edited", "deleted")
    pull_request_target: PullRequestTarget
    push: Push
    registry_package: StrictTypedTrigger("published", "updated")
    release: StrictTypedTrigger(
        "published",
        "unpublished",
        "created",
        "edited",
        "deleted",
        "prereleased",
        "released",
    )
    repository_dispatch: TypedTrigger
    schedule: Schedule
    status: Element
    watch: StrictTypedTrigger("started")
    workflow_call: WorkflowCall
    workflow_dispatch: WorkflowDispatch
    workflow_run: StrictTypedTrigger("completed", "in_progress", "requested")

    # extensions
    inputs: list[Input] | None = field(
        default=_ProxyListAttr("workflow_call", "workflow_dispatch"),
        repr=False,
        init=False,
    )

    @property
    def has_triggers(self) -> bool:
        return any(
            getattr(self, field.name) is not None
            for field in dataclasses.fields(self)
            if field.name != "inputs"
        )

    def asdict(self) -> typing.Any:
        ret = super().asdict()
        ret.pop("inputs", None)
        return ret


def _flow_text(d: dict, *keys: str) -> dict:
    for k in keys:
        v = d.get(k)
        if v is None or "\n" not in v:
            continue
        if v[-1] != "\n":
            v += "\n"
        d[k] = LiteralScalarString(v)
    return d


class Step(Element):
    id: str
    name: Value
    if_: Value
    continue_on_error: Value
    run: Value
    env: dict[str, Value]
    uses: str
    with_: dict[str, Value]

    # extensions
    outputs: list[str]
    needs: list[str]

    def asdict(self) -> typing.Any:
        ret = super().asdict()
        ret.pop("outputs", None)
        needs = ret.pop("needs", None)
        if isinstance(self.if_, Expr):
            ret["if"] = self.if_._formula
        ret = _flow_text(ret, "run")
        if needs:
            ret = CommentedMap(ret)
            ret.yaml_set_start_comment(f"needs {", ".join(needs)}", indent=4)
        return ret


class Matrix(Element):
    include: list[dict[str, str]]
    exclude: list[dict[str, str]]
    values: dict[str, list[str]]

    def __init__(
        self,
        *,
        include: list[dict[str, str]] = None,
        exclude: list[dict[str, str]] = None,
        **values: list[str],
    ):
        self.include = include
        self.exclude = exclude
        self.values = values

    def asdict(self) -> dict[str, Any]:
        ret = super().asdict()
        values = ret.pop("values", {})
        values = _set_flow_style(values, *values)
        ret = {k: [_with_flow_style(x) for x in v] for k, v in ret.items()}
        return values | ret


class Strategy(Element):
    matrix: Matrix
    fail_fast: Value
    max_parallel: Value


class Credentials(Element):
    username: Value
    password: Value


class Container(Element):
    image: Value
    _: dataclasses.KW_ONLY
    credentials: Credentials
    env: dict[str, Value]
    ports: list[Value]
    volumes: list[Value]
    options: list[Value]


class Service(Container):
    id: str


default_runner = "ubuntu-latest"


class Job(Element):
    name: str
    needs: list[str]
    runs_on: str
    container: Container
    services: list[Service]
    outputs: dict[str, Value]
    strategy: Strategy
    env: dict[str, Value]
    steps: list[Step]
    uses: str
    with_: dict[str, Value]

    def asdict(self) -> typing.Any:
        return _dictionarize(_set_flow_style(super().asdict(), "needs"), "services")


class Workflow(Element):
    name: str
    on: On = field(default_factory=On)
    env: dict[str, Value]
    jobs: dict[str, Job] = field(default_factory=dict)
