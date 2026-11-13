from typing import assert_type

from ghgen.expr import DynamicExpr, RefExpr
from ghgen.syntax import env, fromJson, github, job, matrix, run, secrets, strategy

assert_type(github.event.pull_request.head.sha, RefExpr)
assert_type(github.event.pull_request.labels[0].name, RefExpr)
assert_type(github.event.pull_request.labels._.name, RefExpr)
assert_type(github.event["pull_request"].head.sha, RefExpr)
assert_type(github.event.workflow_run.head_repository.owner.login, RefExpr)
assert_type(github.artifacts_list, RefExpr)
assert_type(job.check_run_id, RefExpr)
assert_type(job.container.network, RefExpr)
assert_type(job.services.database.ports[5432], RefExpr)
assert_type(strategy.job_index, RefExpr)
assert_type(env.MY_VARIABLE, RefExpr)
assert_type(matrix.os, RefExpr)
assert_type(secrets.MY_SECRET, RefExpr)
assert_type(run("echo hello").conclusion, RefExpr)
assert_type(run("echo hello").outputs.value, RefExpr)
assert_type(fromJson("{}").arbitrary.nested[0], DynamicExpr)


@job
def build():
    run("echo hello")


assert_type(build.outputs.result, RefExpr)
assert_type(build.result, RefExpr)

github.event.pull_request.hed.sha  # expect-error
github.event.pull_request.head.sha.typo  # expect-error
github.event["pull_request"].head.sah  # expect-error
github.event.pull_request.labels[0].nmae  # expect-error
github.unknown_field  # expect-error
job.statuz  # expect-error
run("echo hello").outcom  # expect-error
