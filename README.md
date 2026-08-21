# `gh-gen`, a GitHub Actions generator

`gh-gen` is a Python DSL for authoring GitHub Actions workflows. You install it once as a
`gh` CLI extension and use it in **any** repository: write a small Python file describing a
workflow, run `gh gen`, and it generates the corresponding `.yml`. Because the workflow is
expressed as normal Python, you get editor completion, reuse, type checks on workflow fields,
`${{ ... }}` expressions built from real Python operators and t-strings, and optional typed
wrappers for the actions you `uses:`.

<!-- readme-test: expect-yaml -->
```python
# .github/workflows/check.py
from ghgen.syntax import *


@workflow
def check():
    on.pull_request().push(branches=["main"]).workflow_dispatch()
    run("echo hello")
```

generates

```yaml
# generated from check.py::check
on:
  pull_request: {}
  push:
    branches:
    - main
  workflow_dispatch: {}
defaults:
  run:
    shell: bash
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
    - run: echo hello
```

> **Status:** work in progress. The [`tests`](./tests) directory is the executable
> specification, and every example below has been checked against the generator's output.

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [How generation works](#how-generation-works)
- [Managing action dependencies (`gh gen add`)](#managing-action-dependencies-gh-gen-add)
- [Triggers (`on`)](#triggers-on)
- [Jobs](#jobs)
  - [`runs_on`](#runs_on)
  - [`needs` and job outputs](#needs-and-job-outputs)
  - [`strategy` / `matrix`](#strategy--matrix)
  - [`environment`](#environment)
  - [`continue_on_error` and `timeout_minutes`](#continue_on_error-and-timeout_minutes)
  - [Calling a reusable workflow](#calling-a-reusable-workflow)
- [Steps](#steps)
- [Expressions and contexts](#expressions-and-contexts)
- [Workflow-level settings](#workflow-level-settings)
  - [`run_name`](#run_name)
  - [`permissions`](#permissions)
  - [`env`, `concurrency`, `defaults`](#env-concurrency-defaults)
- [Builder reference](#builder-reference)

## Installation

`gh-gen` requires [`uv`](https://github.com/astral-sh/uv) and Python ≥ 3.12. Install it once
as a [`gh` CLI](https://cli.github.com) extension and the `gh gen` command becomes available
in every repository you work in:

```bash
gh extension install redsun82/gh-gen
```

Then, from the root of any repository:

```bash
gh gen            # generate every workflow under .github/workflows
gh gen --check    # fail if any generated file is out of date (use in CI)
```

You don't need `uv` or a Python project in the target repository — the extension carries its
own. When hacking on `gh-gen` itself you can instead run the bundled [`gh-gen`](./gh-gen)
wrapper script (just `uv --project <repo> run gh-gen "$@"`); `gh gen …`, `./gh-gen …`, and
`uv run gh-gen …` are interchangeable and this guide uses `gh gen`.

## Quick start

1. In the repository you want to add workflows to, create `.github/workflows/<id>.py`. The
   file name is up to you; the generated YAML is named after each `@workflow` function.
2. Describe the workflow:

   ```python
   # .github/workflows/check.py
   from ghgen.syntax import *


   @workflow
   def check():
       on.pull_request().push(branches=["main"]).workflow_dispatch()
       uses("actions/checkout@v6")
       run("uv run pytest")
   ```

3. Run `gh gen`. It writes `.github/workflows/check.yml` next to your source:

   ```yaml
   # generated from check.py::check
   on:
     pull_request: {}
     push:
       branches:
       - main
     workflow_dispatch: {}
   defaults:
     run:
       shell: bash
   jobs:
     check:
       runs-on: ubuntu-latest
       steps:
       - name: Checkout
         uses: actions/checkout@v6
       - run: uv run pytest
   ```

Commit both the `.py` source and the generated `.yml`; run `gh gen --check` in CI to keep
them in sync.

## How generation works

- `gh-gen` scans each include directory (defaults to `.github/workflows`) for `*.py` files,
  imports them, and generates one YAML file per `@workflow`-decorated function.
- The output file is `<function-name>.yml`, written to `--output-directory` (default
  `.github/workflows`). Override the function-derived id with `@workflow(id="my-id")`.
- Everything you need is exported by `ghgen.syntax`, so `from ghgen.syntax import *` is the
  idiomatic import.
- **Implicit job.** If you call step/job builders (`run`, `uses`, `runs_on`, …) directly at
  the top level of the workflow function, `gh-gen` creates a single job named after the
  workflow for you (as in the quick start). For multiple jobs, declare them explicitly with
  `@job` (see [Jobs](#jobs)).
- **Defaults.** A job that has steps but no `runs_on` defaults to `ubuntu-latest`, and a
  `defaults.run.shell: bash` is added at the workflow level.
- **Validation.** Field types and misuse (for example setting a job-only field at workflow
  level, or mixing `uses` with steps) are checked at generation time and reported with the
  source location.

Useful CLI options (accepted by every command): `-D/--output-directory`, `-I/--include`,
`--check`, `--verbose`. `gh gen` (aliases `g`, `gen`) generates workflows; action
dependencies are managed with `gh gen add`/`update`/`remove`/`sync` — see
[Managing action dependencies](#managing-action-dependencies-gh-gen-add).

## Managing action dependencies (`gh gen add`)

Rather than hand-writing `uses:` strings and pinning versions by hand, `gh-gen` manages the
actions you depend on like a package manager. Dependencies are declared in `gh-gen.yml`,
pinned to a commit in `gh-gen.lock`, and each one is exposed as a fully-typed Python helper
(one keyword argument per action input).

Add a dependency — this updates `gh-gen.yml` and `gh-gen.lock` for you:

```bash
gh gen add actions/checkout@v6
gh gen add astral-sh/setup-uv --name "Setup uv"
```

which records it in `gh-gen.yml`:

```yaml
# gh-gen.yml
uses:
  checkout: actions/checkout
  setup_uv:
    uses: astral-sh/setup-uv
    name: Setup uv
  pre_commit:
    uses: pre-commit/action
    name: Check
```

Each entry becomes an importable helper, so a workflow calls the action by name instead of
repeating a `uses:` string:

<!-- readme-test: skip (needs a generated `actions` module) -->
```python
from ghgen.syntax import *
from actions import *


@workflow
def check():
    on.pull_request().push(branches=["main"]).workflow_dispatch()
    checkout()
    setup_uv()
    pre_commit().id("Check")
```

The rest of the dependency lifecycle is command-driven too:

| Command | Purpose |
| --- | --- |
| `gh gen add <owner>/<repo>[@ref]` | Add an action dependency and lock it |
| `gh gen update` | Refresh pinned action versions |
| `gh gen remove <id>` | Remove an action dependency |
| `gh gen sync` | Regenerate `gh-gen.lock` from `gh-gen.yml` |

Actions are pinned to a commit by default; those whose owner is listed in `trusted-owners`
(just `actions` out of the box) are referenced by tag instead. Use `--no-pin`/`--pin` on
`add` to override per action.

## Triggers (`on`)

`on` configures workflow triggers. Call a trigger to enable it; pass keyword lists to refine
it, or use the fluent sub-builders. Triggers can be chained.

```python
@workflow
def ci():
    on.pull_request(branches=["main"], paths=["src/**"])
    on.push.tags("v*", "!v0.*")
    on.workflow_dispatch()
    run("echo hi")
```

```yaml
on:
  pull_request:
    branches:
    - main
    paths:
    - src/**
  push:
    tags:
    - v*
    - '!v0.*'
  workflow_dispatch: {}
```

`pull_request` and `push` accept `branches`, `ignore_branches`, `paths`, `ignore_paths`
(and `types` for `pull_request`, `tags`/`ignore_tags` for `push`), either as keyword
arguments or via the matching methods (`on.push.branches(...)`, `on.pull_request.paths(...)`,
…).

### Inputs and secrets

`on.workflow_dispatch` and `on.workflow_call` expose typed inputs; `on.input(...)` adds an
input to every trigger that supports one. Each input builder returns an expression you can
interpolate directly:

```python
@workflow
def release():
    on.workflow_dispatch().workflow_call()
    version = on.input("the version to release").required()
    verbose = on.input.default(False)
    run(t"echo releasing {version} (verbose={verbose})")
```

```yaml
on:
  workflow_call:
    inputs:
      version:
        description: the version to release
        required: true
        type: string
      verbose:
        required: false
        default: false
        type: boolean
  workflow_dispatch:
    inputs:
      version:
        description: the version to release
        required: true
        type: string
      verbose:
        required: false
        default: false
        type: boolean
defaults:
  run:
    shell: bash
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
    - run: echo releasing ${{ inputs.version }} (verbose=${{ inputs.verbose }})
```

Choice inputs use `.options(...)`, and `on.workflow_call.secret(...)` declares secrets. See
`tests/test_workflow.py::test_workflow_call` for the full surface.

An input, secret, or output with no explicit `.id(...)` takes its id from the variable it is
assigned to (`version`, `verbose` above). The name is captured where you assign it, so it
holds even when the builder is passed into a helper and only used there.

## Jobs

Declare a job with the `@job` decorator. The function name is the job id, and the decorator
returns a handle usable in `needs` and `${{ needs.<id> }}` expressions.

```python
@workflow
def build():
    on.workflow_dispatch()

    @job
    def compile():
        name("Compile")
        runs_on("ubuntu-latest")
        run("make")

    @job
    def package():
        needs(compile)
        run("make dist")
```

```yaml
jobs:
  compile:
    name: Compile
    runs-on: ubuntu-latest
    steps:
    - run: make
  package:
    needs: [compile]
    runs-on: ubuntu-latest
    steps:
    - run: make dist
```

### `runs_on`

`runs_on` accepts a runner label, a `group=`/`labels=` pair (GitHub-hosted runner groups),
or an array of labels passed positionally:

```python
runs_on("windows-latest")               # runs-on: windows-latest
runs_on(group="my-group")               # runs-on: {group: my-group}
runs_on(labels=["self-hosted", "linux", "x64"])
runs_on(group="my-group", labels=["linux"])
runs_on(["self-hosted", "linux", "x64"])  # runs-on: [self-hosted, linux, x64]
```

For example `runs_on(group="my-group", labels=["linux"])` yields:

```yaml
runs-on:
  group: my-group
  labels:
  - linux
```

A runner label cannot be combined with `group`/`labels` — doing so is a generation error.

### `needs` and job outputs

Declare dependencies with `needs(...)` (or `needs=` on a step), and expose job outputs with
`outputs(...)`. `outputs` accepts step handles (dumping their step outputs), keyword mappings,
or context references such as `matrix.a`:

```python
@job
def producer():
    strategy.matrix(a=[1, 2, 3])
    x = step("x").outputs(one="a", two="b")
    outputs(foo=x.outputs.one, bar=matrix.a)
```

```yaml
producer:
  runs-on: ubuntu-latest
  outputs:
    foo: ${{ steps.x.outputs.one }}
    bar: ${{ matrix.a }}
  strategy:
    matrix:
      a: [1, 2, 3]
  steps:
  - id: x
    name: x
    run: |
      echo one=a | tee -a $GITHUB_OUTPUT
      echo two=b | tee -a $GITHUB_OUTPUT
```

### `strategy` / `matrix`

`strategy.matrix(...)` builds a matrix; chain `.include(...)`, `.exclude(...)`,
`.fail_fast(...)`, and `.max_parallel(...)`. Matrix values are available through the `matrix`
context.

```python
@job
def test():
    strategy.matrix(x=[1, 2, 3], y=["a", "b", "c"]).fail_fast().max_parallel(5)
    run(t"{matrix.x}, {matrix.y}")
```

```yaml
test:
  runs-on: ubuntu-latest
  strategy:
    matrix:
      x: [1, 2, 3]
      y: [a, b, c]
    fail-fast: true
    max-parallel: 5
  steps:
  - run: ${{ matrix.x }}, ${{ matrix.y }}
```

### `environment`

```python
environment("production")                                   # environment: production
environment(name="staging", url="https://example.com")      # environment: {name, url}
```

### `continue_on_error` and `timeout_minutes`

Both are available on jobs (top-level builders) and on steps (methods / `step(...)` keyword
arguments). They accept a literal or an expression.

```python
@job
def flaky():
    runs_on("ubuntu-latest")
    continue_on_error(github.event_name == "push")
    timeout_minutes(30)
    run("echo hello").timeout_minutes(5)
    run("maybe").continue_on_error()   # defaults to True
```

```yaml
flaky:
  runs-on: ubuntu-latest
  continue-on-error: ${{ github.event_name == 'push' }}
  timeout-minutes: 30
  steps:
  - timeout-minutes: 5
    run: echo hello
  - continue-on-error: true
    run: maybe
```

### Calling a reusable workflow

Passing a reusable-workflow reference to `uses` at the job level turns the job into a call.
Provide inputs with `.with_(...)` and secrets with `.secrets(...)`:

```python
@job
def deploy():
    uses("octo/repo/.github/workflows/deploy.yml@v1").with_(target="prod")
```

A job cannot mix a workflow `uses` with `runs_on` or steps.

## Steps

Inside a job (or at the workflow top level for an implicit job) add steps with `run`, `uses`,
or the general `step(...)` builder. Builders are chainable and each returns a handle whose
`.outputs`, `.outcome`, and `.result` are usable in expressions.

```python
@job
def build():
    step.run("echo hello").id("salutations")
    run("echo $WHO").env(WHO="world")
    step("catastrophe").run("echo oh no").if_("failure()")
    uses("actions/checkout@v4").with_(ref="dev")
    step("Lint").uses("./my-action", arg_1="foo", arg__2="bar")
```

```yaml
steps:
- id: salutations
  run: echo hello
- run: echo $WHO
  env:
    WHO: world
- name: catastrophe
  if: failure()
  run: echo oh no
- name: Checkout
  uses: actions/checkout@v4
  with:
    ref: dev
- name: Lint
  uses: ./my-action
  with:
    arg-1: foo
    arg_2: bar
```

Notes:
- Step keyword arguments to `step(...)` mirror the chained methods: `name`, `run`, `id`,
  `if_`, `env`, `continue_on_error`, `timeout_minutes`, `uses`, `shell`,
  `working_directory`, `with_`, `outputs`, `needs`.
- In `with_(...)`, a trailing double underscore becomes a single one and single underscores
  become dashes (`arg__2` → `arg_2`, `arg_1` → `arg-1`), matching action input conventions.
- `uses` on an action auto-derives a step `name` from the action, and multi-line `run`
  blocks are dedented automatically.
- When a step is assigned to a variable and given no explicit `id`, it takes its id from that
  variable name, captured at the assignment so it survives being passed into a helper.

## Expressions and contexts

Any `${{ ... }}` expression is built from context objects and normal Python operators, then
interpolated with [t-strings](https://peps.python.org/pep-0750/) (`t"...{ctx}..."`) or passed
directly to builders. Exported contexts include `github`, `env`, `secrets`, `vars`, `matrix`,
`runner`, and `steps`; `needs.<job>` comes from job handles and `inputs.<name>` from input
builders.

```python
run(t"echo {github.actor} on {runner.os}")
run("deploy").if_((github.ref == "refs/heads/main") & ~contains(github.event_name, "pull"))
```

Operators map to Actions expression syntax:

| Python | Actions |
| --- | --- |
| `a & b` | `a && b` |
| `a \| b` | `a \|\| b` |
| `~a` | `!a` |
| `a == b`, `a != b`, `a < b`, … | `a == b`, `a != b`, `a < b`, … |
| `a[i]`, `a.x` | `a[i]`, `a.x` |

Precedence is preserved, so `(a | b) & c` renders parentheses. Built-in functions are
`contains`, `always`, `cancelled`, `fromJson`, `toJson`, `hashFiles`, and `format`:

```python
strategy.matrix(fromJson(inputs.config))
run("").if_(always())
```

> Actions expressions are not booleans, so never use Python `and`/`or`/`not` or put a
> context in an `if` statement — use `&`, `|`, `~`. Doing otherwise raises an error.
>
> Plain f-strings still interpolate contexts but are deprecated in favor of t-strings, and
> emit a `DeprecationWarning`.

## Workflow-level settings

### `run_name`

Set the dynamic run name shown in the Actions UI. It is an expression, so t-strings work:

```python
@workflow
def deploy():
    run_name(t"Deploy by {github.actor}")
    on.workflow_dispatch()
    run("")
```

```yaml
# generated from deploy.py::deploy
run-name: Deploy by ${{ github.actor }}
on:
  workflow_dispatch: {}
```

### `permissions`

Call `permissions` with `"read-all"`/`"write-all"`, or with per-scope keyword arguments. It
applies to the workflow at top level and to a job when called inside `@job`. Scopes include
`actions`, `contents`, `id_token`, `packages`, `pull_requests`, and `models` (the GitHub
Models scope, `"read"` or `"none"`):

```python
@workflow
def infer():
    on.workflow_dispatch()
    permissions(contents="read")

    @job
    def run_model():
        permissions(models="read", packages="read")
        run("")
```

```yaml
permissions:
  contents: read
jobs:
  run_model:
    permissions:
      models: read
      packages: read
    runs-on: ubuntu-latest
    steps:
    - run: ''
```

`"read-all"`/`"write-all"` cannot be combined with per-scope arguments.

### `env`, `concurrency`, `defaults`

```python
env(FOO="bar")                                   # workflow or job env

concurrency.group(t"{github.ref | github.run_id}")
concurrency.cancel_in_progress()                 # concurrency: {group, cancel-in-progress}

defaults.run(shell="zsh")                        # defaults.run.shell / working-directory
defaults.run.working_directory("foo")
```

`concurrency` and `defaults` are also callable inside `@job` for job-scoped settings, and
`env(...)` accepts either keyword arguments or a mapping.

## Builder reference

| Builder | Generated key | Scope |
| --- | --- | --- |
| `name(...)` | `name` | workflow / job |
| `run_name(...)` | `run-name` | workflow |
| `on.<trigger>(...)` | `on.<trigger>` | workflow |
| `on.input(...)`, `on.<trigger>.input(...)` | `on.*.inputs.<id>` | workflow |
| `on.workflow_call.secret(...)` | `on.workflow_call.secrets.<id>` | workflow |
| `on.workflow_call.output(...)` | `on.workflow_call.outputs.<id>` | workflow |
| `permissions(...)` | `permissions` | workflow / job |
| `env(...)` | `env` | workflow / job |
| `concurrency(...)` | `concurrency` | workflow / job |
| `defaults.run(...)` | `defaults.run` | workflow / job |
| `@job` | `jobs.<id>` | workflow |
| `runs_on(...)` | `runs-on` | job |
| `needs(...)` | `needs` | job |
| `outputs(...)` | `outputs` | job |
| `environment(...)` | `environment` | job |
| `strategy.matrix(...)` | `strategy` | job |
| `continue_on_error(...)` | `continue-on-error` | job / step |
| `timeout_minutes(...)` | `timeout-minutes` | job / step |
| `uses(<workflow>)` | `uses` (reusable workflow call) | job |
| `run(...)`, `step(...)` | `steps[].run` | step |
| `uses(<action>)`, `.uses(...)` | `steps[].uses` | step |
| `.with_(...)` | `steps[].with` | step |
| `.if_(...)` | `if` | job / step |
| `.shell(...)`, `.working_directory(...)` | `steps[].shell` / `working-directory` | step |
| `.outputs(...)` | `steps[].id` + job `outputs` wiring | step |

For the complete, executable specification of every builder and its output, browse
[`tests/test_workflow.py`](./tests/test_workflow.py).
