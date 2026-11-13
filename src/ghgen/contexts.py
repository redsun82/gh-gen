import threading
import typing
import re

from .expr import contexts, RefExpr, Map, FlatMap
from .rules import *
from .workflow import *
from ._context_types import Contexts
from .schema import catalog, supports

Contexts = contexts(Contexts)


steps = Contexts.steps
matrix = Contexts.matrix
runner = Contexts.runner
secrets = Contexts.secrets
vars = Contexts.vars
github = Contexts.github

# we don't expose the following
# needs: handled through job handles returned by @job
# jobs: handled via `outputs` function
# inputs: handled via `input` function
# strategy: replaced by a ProxyExpr to also be the `strategy` field setter
# job: replaced by a ProxyExpr to also be the `@job` decorator
# env: replaced by a ProxyExpr to also be the `env` field setter


@dataclasses.dataclass
class ContextBase(threading.local, RuleSet):
    current_workflow: Workflow | None = None
    current_job: Job | None = None
    current_workflow_id: str | None = None
    current_job_id: str | None = None
    current_action: Action | None = None
    current_action_id: str | None = None

    def _knows_step_id(self, target: typing.Any, id: str) -> bool:
        if not self.current_job:
            return False
        for s in self.current_job.steps:
            if s is target:
                return False
            if s.id == id:
                return True
        return False

    def error(self, message: str):
        raise NotImplemented

    def check(self, cond: typing.Any, message: str) -> bool:
        if not cond:
            self.error(message)
        return cond

    def availability_rule(self, target: typing.Any, field: str | None) -> dict | None:
        key = Element._key(field) if isinstance(field, str) else ""
        job_prefix = "jobs.<job_id>."
        match target:
            case Step():
                path = job_prefix + "steps." + key
            case Job():
                path = job_prefix + key
            case Workflow():
                path = key
            case Strategy() | Matrix():
                path = job_prefix + "strategy"
            case RunsOn():
                path = job_prefix + "runs-on"
            case Service():
                path = job_prefix + "services.<service_id>." + key
            case Container():
                path = job_prefix + "container." + key
            case Credentials():
                container = self.current_job and self.current_job.container
                owner = (
                    "container"
                    if container and container.credentials is target
                    else "services.<service_id>"
                )
                path = job_prefix + owner + ".credentials"
            case Concurrency():
                path = (job_prefix if self.current_job else "") + "concurrency"
            case Environment():
                path = job_prefix + "environment." + key
            case JobDefaults() | JobDefaults.Run():
                path = (job_prefix if self.current_job else "") + "defaults.run"
            case Input() if (
                self.current_workflow and self.current_workflow.on.workflow_call
            ):
                path = "on.workflow_call.inputs.<inputs_id>." + key
            case Output() if not self.current_action:
                path = "on.workflow_call.outputs.<output_id>.value"
            case _:
                return None
        candidates = [
            rule
            for rule in catalog()["availability"]
            if (
                path == re.sub(r"\.<[^.]+>$", "", rule["path"])
                or path.startswith(re.sub(r"\.<[^.]+>$", "", rule["path"]) + ".")
            )
        ]
        return max(candidates, key=lambda rule: len(rule["path"]), default=None)

    @rule()
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        match target, field:
            case (WorkflowCall(), "outputs"):
                return True
            case (Workflow(), "on" | "name") | (On() | Trigger(), _):
                self.error(f"no contextual information can be used in `{field}`")
                return False
            case _:
                return True

    @rule(steps)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        return self.check(
            self.current_job,
            "`steps` can only be used in a job, did you forget a `@job` decoration?",
        ) and self.check(
            isinstance(target, Step)
            or field == "outputs"
            or (self.current_action and isinstance(target, Output)),
            "`steps` can only be used while constructing a step or setting outputs",
        )

    @rule(steps._)
    def v(self, id: str, *, target: typing.Any = None, **kwargs) -> bool:
        return self.check(
            self._knows_step_id(target, id),
            f"step `{id}` not defined yet in job `{self.current_job_id}`",
        )

    @rule(steps._.outputs._)
    def v(
        self,
        id: str,
        output: str,
        **kwargs,
    ) -> bool:
        step = next(s for s in self.current_job.steps if s.id == id)
        return self.check(
            step.outputs and step.outputs and output in step.outputs.values(),
            f"`{output}` was not declared in step `{id}`, use `outputs()` to declare it",
        )

    @rule(matrix)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        return self.check(
            self.current_job
            and self.current_job.strategy is not None
            and self.current_job.strategy.matrix is not None,
            "`matrix` can only be used in a matrix job",
        ) and self.check(
            not isinstance(target, (Strategy, Matrix))
            or (isinstance(target, Job) and field == "strategy"),
            "`matrix` cannot be used in the `strategy` field defining it",
        )

    @rule(matrix._)
    def v(self, id, **kwargs) -> bool:
        m = self.current_job.strategy.matrix
        # don't try to be smart if using something like an Expr
        return not isinstance(m, Matrix) or self.check(
            (m.values and id in m.values)
            or (m.include and any(id in include for include in m.include)),
            f"`{id}` was not declared in the `matrix` for this job",
        )

    @rule(Contexts.job)
    def v(self, **kwargs) -> bool:
        return self.check(self.current_job, "`job` can only be used in a job")

    @rule(Contexts.job.container)
    def v(self, **kwargs) -> bool:
        return self.check(
            self.current_job.container,
            "`job.container` can only be used in a containerized job",
        )

    @rule(Contexts.job.services)
    def v(self, **kwargs) -> bool:
        return self.check(
            self.current_job.services,
            "`job.services` can only be used in a job with services",
        )

    @rule(Contexts.job.services._)
    def v(self, id, **kwargs) -> bool:
        return self.check(
            id in (s.id for s in self.current_job.services),
            f"no `{id}` service defined in `job.services`",
        )

    @rule(Contexts.jobs)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        match target, field:
            case (WorkflowCall(), "outputs") | (Output(), _):
                return True
            case _:
                self.error(
                    "`jobs` is only allowed while declaring worfklow outputs in a workflow call trigger",
                )
                return False

    @rule(Contexts.jobs._)
    def v(self, id, **kwargs) -> bool:
        return self.check(
            id in self.current_workflow.jobs,
            f"no `{id}` job declared yet in this workflow",
        )

    @rule(Contexts.jobs._.outputs._)
    @rule(Contexts.needs._.outputs._)
    def v(self, id, out, **kwargs) -> bool:
        job = self.current_workflow.jobs[id]
        return self.check(
            job.outputs and out in job.outputs,
            f"no outputs `{out}` declared in job `{id}`",
        )

    @rule(Contexts.needs)
    def v(self, **kwargs) -> bool:
        if not self.check(
            self.current_job, "job handle used as an expression outside a job"
        ):
            return False
        # in case `needs._` is used without any actual needed job, let's make sure `needs` is present
        if self.current_job.needs is None:
            self.current_job.needs = []
        return True

    @rule(Contexts.needs._)
    def v(self, id, **kwargs) -> bool:
        if not self.check(
            id in self.current_workflow.jobs,
            f"no `{id}` job declared yet in this workflow",
        ):
            return False
        if id not in self.current_job.needs:
            self.current_job.needs.append(id)
        return True

    @rule(runner)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        return (
            self.check(self.current_job, "`runner` can only be used in a job")
            and self.check(
                field != "strategy" and not isinstance(target, Strategy),
                f"`runner` cannot be used to update a job `strategy`",
            )
            and self.check(
                field != "runs_on", f"`runner` cannot be used to update a job `runs-on`"
            )
        )

    @rule(Contexts.strategy)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        return self.check(
            self.current_job,
            "`strategy` can only be used inside a job",
        ) and self.check(
            not isinstance(target, (Strategy, Matrix))
            or (isinstance(target, Job) and field == "strategy"),
            "`strategy` context cannot be used while defining the strategy itself",
        )

    @rule(secrets)
    def v(self, *, target: typing.Any = None, **kwargs) -> bool:
        return self.check(
            not self.current_action,
            "`secrets` cannot be used in an action, pass them as inputs instead",
        ) and self.check(
            not isinstance(target, Concurrency),
            "`secrets` cannot be used in `concurrency`",
        )

    @rule(Contexts.env)
    def v(self, target: typing.Any = None, field: str | None = None) -> bool:
        rule = self.availability_rule(target, field)
        allowed = rule is not None and "env" in rule["contexts"]
        return self.check(
            isinstance(target, Step) or allowed,
            "`env` can only be used in steps",
        ) and self.check(
            field != "uses",
            "`env` cannot be used for `id` and `uses`",
        )

    @rule(github.action_path)
    @rule(github.action_status)
    def v(self, **kwargs) -> bool:
        return self.check(
            self.current_action,
            "`github.action_path` and `github.action_status` are only available in composite actions",
        )

    @rule(github.action_ref)
    @rule(github.action_repository)
    def v(self, *, target: typing.Any = None, field: str | None = None) -> bool:
        return self.check(
            not isinstance(target, Step) or field != "run",
            "`github.action_ref` and `github.action_repository` must be passed through `env`, not used directly in `run`",
        )

    def event_error(self, path: tuple[str, ...]) -> str | None:
        workflow = self.current_workflow
        if not workflow or workflow.on.workflow_call is not None:
            return None
        triggers = {
            field.name: getattr(workflow.on, field.name)
            for field in dataclasses.fields(workflow.on)
            if field.name != "inputs" and getattr(workflow.on, field.name) is not None
        }
        if path[:2] in (("github", "base_ref"), ("github", "head_ref")):
            if not triggers.keys() & {"pull_request", "pull_request_target"}:
                return f"`{'.'.join(path)}` requires a pull_request or pull_request_target trigger"
            return None
        if path[:2] != ("github", "event") or len(path) == 2:
            return None
        data = catalog()
        for event, trigger in triggers.items():
            if event not in data["events"]:
                return f"no payload schema for configured trigger `{event}`"
            variants = data["events"][event]
            activities = (
                None
                if event == "repository_dispatch"
                else getattr(trigger, "types", None) or data["defaults"].get(event)
            )
            if path[2] == "inputs" and event == "workflow_dispatch":
                names = {item.id for item in trigger.inputs or ()}
                if len(path) == 3 or (len(path) == 4 and path[3] in names):
                    return None
                continue
            if any(
                supports(node, path[2:])
                for activity, node in variants.items()
                if activities is None or activity in activities
            ):
                return None
        return (
            f"`{'.'.join(path)}` is unavailable for configured triggers "
            f"({', '.join(sorted(triggers))})"
        )
