import threading
import typing

from .expr import contexts, RefExpr, Map, FlatMap
from .rules import *
from .workflow import *


@contexts
class Contexts:
    class Steps(RefExpr):
        class Step(RefExpr):
            outputs: FlatMap
            result: RefExpr
            outcome: RefExpr

        __getattr__: Map[Step]

    steps: Steps
    matrix: FlatMap

    class Job(RefExpr):
        class Container(RefExpr):
            id: RefExpr
            network: RefExpr

        container: Container

        class Services(RefExpr):
            class Service(RefExpr):
                id: RefExpr
                network: RefExpr
                ports: RefExpr

            __getattr__: Map[Service]

        services: Services
        status: RefExpr

    job: Job

    class Jobs(RefExpr):
        class Job:
            outputs: FlatMap
            result: RefExpr

        __getattr__: Map[Job]

    jobs: Jobs
    needs: Jobs

    class Runner(RefExpr):
        name: RefExpr
        os: RefExpr
        arch: RefExpr
        temp: RefExpr
        tool_cache: RefExpr
        debug: RefExpr
        environment: RefExpr

    runner: Runner

    inputs: FlatMap

    class Strategy(RefExpr):
        _use_dashes = True

        fail_fast: RefExpr
        job_index: RefExpr
        job_total: RefExpr
        max_parallel: RefExpr

    strategy: Strategy

    secrets: FlatMap
    vars: FlatMap
    env: FlatMap

    class Github(RefExpr):
        action: RefExpr
        # action_path: RefExpr
        action_ref: RefExpr
        action_repository: RefExpr
        # action_status: RefExpr
        actor: RefExpr
        actor_id: RefExpr
        api_url: RefExpr
        base_ref: RefExpr
        env: RefExpr
        event: typing.Any
        event_name: RefExpr
        event_path: RefExpr
        graphql_url: RefExpr
        head_ref: RefExpr
        job: RefExpr
        path: RefExpr
        ref: RefExpr
        ref_name: RefExpr
        ref_protected: RefExpr
        ref_type: RefExpr
        repository: RefExpr
        repository_id: RefExpr
        repository_owner: RefExpr
        repository_owner_id: RefExpr
        repositoryUrl: RefExpr
        retention_days: RefExpr
        run_id: RefExpr
        run_number: RefExpr
        run_attempt: RefExpr
        secret_source: RefExpr
        server_url: RefExpr
        sha: RefExpr
        token: RefExpr
        triggering_actor: RefExpr
        workflow: RefExpr
        workflow_ref: RefExpr
        workflow_sha: RefExpr
        workspace: RefExpr

    github: Github


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
            isinstance(target, Step) or field == "outputs",
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
            step.outputs and step.outputs and output in step.outputs,
            f"`{output}` was not declared in step `{id}`, use `returns()` declare it",
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
            not isinstance(target, Concurrency),
            "`secrets` cannot be used in `concurrency`",
        )

    @rule(Contexts.env)
    def v(self, target: typing.Any = None, field: str | None = None) -> bool:
        return self.check(
            isinstance(target, Step),
            "`env` can only be used in steps",
        ) and self.check(
            field != "uses",
            "`env` cannot be used for `id` and `uses`",
        )
