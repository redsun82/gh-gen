import inspect

import pytest

from conftest import expect_errors
from src.ghgen.ctx import *


@expect_errors
def test_wrong_types(error):
    on.workflow_dispatch()
    error("illegal assignment to `env`: 'int' object is not iterable")
    env(3)
    env(FOO="bar")
    error(
        "illegal assignment to `env`: dictionary update sequence element #0 has length 4; 2 is required"
    )
    env(["nope"])
    run("")


@expect_errors
def test_wrong_jobs(error):
    on.workflow_dispatch()

    @job
    def external():
        run("echo Hello, world")

        # fmt: off
        error('job `nested` not created directly inside a workflow body')
        @job
        # fmt: on
        def nested():
            run("false")

    @job
    def another():
        pass

    # fmt: off
    error('job `external` already exists in workflow `test_wrong_jobs`')
    @job
    # fmt: on
    def external():
        run("nope")


def test_job_outside_workflow(error):
    with error:
        # fmt: off
        error('job `nested` not created directly inside a workflow body')
        @job
        def nested():
            run("echo Hello, world")
        # fmt: on


@expect_errors
def test_auto_job_with_existing_jobs(error):
    on.workflow_dispatch()

    @job
    def a_job():
        pass

    error(
        "`runs_on` is a `job` field, but implicit job cannot be created because there are already jobs in the workflow"
    )
    runs_on("x")


@expect_errors
def test_adding_jobs_to_auto_job(error):
    on.workflow_dispatch()
    runs_on("x")

    # fmt: off
    error('explict job `a_job` cannot be created after already implicitly creating a job, which happened when setting `runs_on`')
    @job
    # fmt: on
    def a_job():
        pass


@expect_errors
def test_workflow_fields_in_job(error):
    on.workflow_dispatch()

    @job
    def a_job():
        name("a name")

        error("`on.workflow_dispatch` must be used in a workflow")
        on.workflow_dispatch()


@expect_errors
def test_workflow_fields_in_auto_job(error):
    on.workflow_dispatch()
    runs_on("x")

    error("`on.workflow_dispatch` must be used in a workflow")
    on.workflow_dispatch()


@expect_errors
def test_wrong_input(error):
    error(
        "`on.input` must be used after setting either `on.workflow_call` or `on.workflow_dispatch`"
    )
    on.input()
    on.workflow_dispatch()
    error(
        "expected `type` to be of type `Literal['boolean', 'choice', 'number', 'environment', 'string']`, got `list[int]` of type `GenericAlias`"
    )
    on.input.type(list[int])

    @job
    def j():
        error("`on.input` must be used in a workflow")
        on.input()


@expect_errors
def test_unexpected_step_outputs(error):
    on.workflow_dispatch()
    x = step("x")
    error("`foo` was not declared in step `x`, use `returns()` declare it")
    step("y").run(x.outputs.foo)


@expect_errors
def test_wrong_outputs(error):
    on.workflow_call()

    @job
    def j1():
        x = step("x")
        error(
            "step `x` passed to `outputs`, but no outputs were declared on it. Use `returns()` to do so"
        )
        outputs(x)

    @job
    def j2():
        x = step("x").outputs("foo")
        y = step("y")
        error(
            "step `y` passed to `outputs`, but no outputs were declared on it. Use `returns()` to do so"
        )
        outputs(x, y)

    @job
    def j3():
        error("unsupported unnamed output `42`, must be a context field or a step")
        outputs(42)

    @job
    def j4():
        step.id("x").outputs("foo")
        step.id("y").outputs("bar")
        error(
            "unsupported unnamed output `${{ steps.x && steps.y }}`, must be a context field or a step"
        )
        outputs(steps.x & steps.y)


@expect_errors
def test_undeclared_step_output(error):
    on.workflow_dispatch()
    x = step("step1").outputs("foo")
    error("`bar` was not declared in step `x`, use `returns()` declare it")
    step("step2").run(x.outputs.bar)


@expect_errors
def test_wrong_job_needs(error):
    on.workflow_dispatch()

    @job
    def init():
        pass

    error("job handle used as an expression outside a job")
    env(FOO=init)

    @job
    def j():
        error("no `non_existing` job declared yet in this workflow")
        needs(Contexts.needs.non_existing)
        error("no `other_job` job declared yet in this workflow")
        needs(Contexts.needs.other_job)
        error(
            "`needs` only accepts job handles given by `@job`, got `42`, `init`, `${{ matrix.a }}`"
        )
        needs(42, "init", matrix.a)

    @job
    def other_job():
        pass


@expect_errors
def test_unavailable_job_contexts(error):
    on.workflow_dispatch()
    _ = str(matrix)
    _ = str(steps)
    _ = str(job)

    @job
    def j():
        error("`matrix` can only be used in a matrix job")
        step(matrix.x)


@expect_errors
def test_unavailable_container(error):
    on.workflow_dispatch()

    @job
    def j1():
        error("`job.container` can only be used in a containerized job")
        step(job.container.id)


@expect_errors
def test_unavailable_service(error):
    on.workflow_dispatch()

    @job
    def j1():
        error("`job.services` can only be used in a job with services")
        step(job.services)

    @job
    def j2():
        service("a")
        error("no `b` service defined in `job.services`")
        step(job.services.b)


@expect_errors
def test_unavailable_matrix_values(error):
    on.workflow_dispatch()

    @job
    def j1():
        strategy.matrix(a=[0])
        error("`x` was not declared in the `matrix` for this job")
        step(matrix.x)

    @job
    def j2():
        strategy.matrix(b=["x"])
        error("`a` was not declared in the `matrix` for this job")
        step(matrix.a)

    @job
    def j3():
        strategy.matrix(fromJson("{}"))

    @job
    def j4():
        strategy.matrix(x=[42])
        error("`a` was not declared in the `matrix` for this job")
        step(matrix.a)

    @job
    def j5():
        strategy.matrix(x=[42])
        error("`matrix` cannot be used in the `strategy` field defining it")
        strategy.max_parallel(matrix.x)
        error("`matrix` cannot be used in the `strategy` field defining it")
        strategy.matrix(y=[matrix.x])
        run("")


@expect_errors
def test_steps_errors(error):
    on.workflow_dispatch()

    error("`steps` can only be used in a job, did you forget a `@job` decoration?")
    env(FOO=steps)

    # fmt: off
    @job
    # fmt: on
    def j():
        error("`steps` can only be used while constructing a step or setting outputs")
        env(FOO=steps)
        error("step `x` not defined yet in job `j`")
        run(f"echo {steps.x.outcome}")
        run("").id("x")
        error("step `y` not defined yet in job `j`")
        step("print self outcome?").run(f" {steps.y.result}").id("y")
        return steps.z.outputs


@expect_errors
def test_wrong_runner_use(error):
    on.workflow_dispatch()
    error("`runner` can only be used in a job")
    env(FOO=runner.arch)

    @job
    def j():
        error("`runner` cannot be used to update a job `strategy`")
        strategy.fail_fast(runner.os == "Linux")
        strategy.matrix(x=[runner.environment])
        error("`runner` cannot be used to update a job `runs-on`")
        runs_on(runner.os)


@expect_errors
def test_wrong_strategy_context_use(error):
    on.workflow_dispatch()
    error("`strategy` can only be used inside a job")
    env(FOO=strategy)

    @job
    def j():
        error("`strategy` context cannot be used while defining the strategy itself")
        strategy.fail_fast(strategy)
        error("`strategy` context cannot be used while defining the strategy itself")
        strategy.max_parallel(strategy & 2 | 1)
        error("`strategy` context cannot be used while defining the strategy itself")
        strategy.matrix(x=[strategy.job_index])


@expect_errors
def test_wrong_calls(error):
    on.workflow_dispatch()

    @job
    def j1():
        run("echo hello")
        error("job `j1` specifies both `uses` (with `call`) and steps")
        call("foo")
        error("job `j1` must specify `uses` (via `call`) in order to specify `with`")
        with_(arg="bar")

    @job
    def j2():
        call("foo")
        error("job `j2` has already specified `uses` (with `call`)")
        call("bar")
        error("job `j2` adds steps when `uses` is already set (by `call`)")
        run("echo hello")
        run("echo world")
        error(
            "job `j2` cannot set `runs-on` as it has already specified `uses` (with `call`)"
        )
        runs_on("ubuntu-latest")


def test_on_must_be_set(error):
    with error:

        @workflow
        def w():
            run("")

        error.id = "w"

        error("workflow `w` must have at least one trigger")
        _ = w.worfklow


def test_at_least_one_job(error):
    @workflow
    def w():
        on.workflow_dispatch()

    error.id = "w"

    with error:
        error("workflow `w` must have at least one job")
        _ = w.worfklow


def test_all_outputs_set(error):
    @workflow
    def w():
        on.workflow_call.output(id="one")
        on.workflow_call.output(id="two", value=2)
        on.workflow_call.output(id="three")
        step("")

    error.id = "w"

    with error:
        error("workflow `w` has no value set for one, three, use `outputs` to set them")
        _ = w.worfklow


@expect_errors
def test_any_context_in_wrong_place(error):
    error("no contextual information can be used in `name`")
    name(vars.NAME)
    error("no contextual information can be used in `branches`")
    on.pull_request(branches=[vars.BRANCH])
    error("no contextual information can be used in `paths`")
    on.push(paths=[f"./{vars.PATH}"])

    run("")


@expect_errors
def test_wrong_permissions(error):
    on.workflow_dispatch()
    error(
        "`permissions` cannot be set to `read-all` or `write-all` with any other more specific permission field"
    )
    permissions("read-all", attestations="write")
    error(
        "expected `permissions` to be of type `Union[Permissions, Literal['read-all', 'write-all'], NoneType]`, got `'foo'` of type `str`"
    )
    permissions("foo")

    @job
    def j():
        error(
            "expected `contents` to be of type `Optional[Literal['read', 'write', 'none']]`, got `'bar'` of type `str`"
        )
        permissions(contents="bar")
        error(
            "expected `id_token` to be of type `Optional[Literal['write', 'none']]`, got `'read'` of type `str`"
        )
        permissions(id_token="read")
        run("")


@expect_errors
def test_wrong_defaults(error):
    on.workflow_dispatch()
    error(
        "expected `working_directory` to be of type `str | None`, got `github.actor` of type `RefExpr`"
    )
    defaults.run.working_directory(github.actor)
    error(
        "expected `shell` to be of type `str | None`, got `'${{ \\x00github.actor && 42 }}'` of type `str`"
    )
    defaults.run.shell(f"{github.actor & 42}")

    @job
    def j():
        defaults.run.working_directory(github.actor)
        error(
            "expected `shell` to be of type `str | bool | int | float | Expr | None`, got `['foo', 'bar']` of type `list`"
        )
        defaults.run.shell(["foo", "bar"])
        run("")


@expect_errors
def test_wrong_concurrency(error):
    on.workflow_dispatch()
    error("`secrets` cannot be used in `concurrency`")
    concurrency(group=secrets.FOO)

    @job
    def j():
        error("`secrets` cannot be used in `concurrency`")
        concurrency(group=secrets.FOO)
        # TODO error()
        # concurrency(group=env.BAR)
        step("")
