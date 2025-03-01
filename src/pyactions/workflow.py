from .element import element
from typing import ClassVar, Any
from .expr import Value
from dataclasses import field

__all__ = [
    "PullRequest",
    "WorkflowDispatch",
    "On",
    "Step",
    "Job",
    "Strategy",
    "Matrix",
    "Workflow",
]


@element
class PullRequest:
    tag: ClassVar[str] = "pull_request"
    branches: list[str]
    paths: list[str]


@element
class WorkflowDispatch:
    tag: ClassVar[str] = "workflow_dispatch"


@element
class On:
    pull_request: PullRequest
    workflow_dispatch: WorkflowDispatch


@element
class Step:
    name: str
    if_: str
    env: dict[str, str]
    continue_on_error: str | bool


@element
class Run(Step):
    run: str
    shell: str
    working_directory: str


@element
class Use(Step):
    use: str
    with_: dict[str, str]


@element
class Matrix:
    include: list[dict[str, str]]
    exclude: list[dict[str, str]]
    values: dict[str, list[str]]

    def asdict(self) -> dict[str, Any]:
        print("PROUT")
        ret = super().asdict()
        ret |= ret.pop("values", {})
        return ret


@element
class Strategy:
    matrix: Matrix
    fail_fast: Value[bool]
    max_parallel: Value[int]


@element
class Job:
    name: str
    runs_on: str
    strategy: Strategy
    steps: list[Step]


@element
class Workflow:
    name: str
    on: On = field(default_factory=On)
    jobs: dict[str, Job] = field(default_factory=dict)
