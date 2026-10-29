import subprocess
import sys

import pytest

from conftest import expect, expect_action, expect_action_errors, expect_errors
from ghgen.syntax import *


def _tools_body():
    name("Setup tools")
    description("Install our toolchain")
    author("me")
    branding(icon="tool", color="blue")
    version = input("tool version", default="1.0")
    token = input("auth token", required=True, deprecation_message="use OIDC")
    uses("actions/setup-node@v4", node_version=20)
    cache = uses("actions/cache@v4", path="~/.cache", key=t"tools-{runner.os}").outputs(
        "cache-hit"
    )
    install = run(t"""
        "$GITHUB_ACTION_PATH/install.sh" {version} {token}
        echo "path=$HOME/tools" >> $GITHUB_OUTPUT
    """).outputs("path")
    run("echo cached").if_(cache.outputs.cache_hit == "true").shell("pwsh")
    output("installed path", value=install.outputs.path)
    output("cache hit", value=cache.outputs.cache_hit)


@expect_action("""\
    # generated from test_action.py::test_action_steps
    name: A
    description: |
      Multi-line
      description
    runs:
      using: composite
      steps:
      - name: empty
        run: ''
        shell: bash
      - run: echo hi
        env:
          FOO: bar
        shell: bash
        working-directory: ${{ github.action_path }}
      - continue-on-error: true
        run: echo maybe
        shell: bash
    """)
def test_action_steps():
    name("A")
    description("""
        Multi-line
        description
    """)
    step("empty")
    run("echo hi").working_directory(github.action_path).env(FOO="bar")
    run("echo maybe").continue_on_error()


@expect_action_errors
def test_action_errors(error):
    name("A")
    description("A")
    error("`runs_on` cannot be used in an action")
    runs_on("ubuntu-latest")
    error("`env` cannot be used in an action")
    env(FOO="bar")
    error("`secrets` cannot be used in an action, pass them as inputs instead")
    run(t"echo {secrets.TOKEN}")
    error("`outputs` cannot be used in an action, use `output` instead")
    outputs(x="y")
    error("`run_name` must be used in a workflow")
    run_name("foo")
    error("composite action steps do not support `timeout-minutes`")
    run("sleep 100").timeout_minutes(1)
    error("`on` cannot be used in an action, use `input` instead")
    on.input("x")


def test_action_output_before_steps():
    @action
    def early_output():
        name("A")
        description("A")
        output(value=steps.build.outputs.result)
        run("true")

    with pytest.raises(GenerationError) as exc:
        _ = early_output.action
    assert any(
        e.message == "step `build` not defined yet in job `early_output`"
        for e in exc.value.errors
    )


_tools = action(_tools_body, id="tools")


@expect_action("""\
    # generated from test_action.py::test_basic_action
    name: Setup tools
    author: me
    description: Install our toolchain
    branding:
      icon: tool
      color: blue
    inputs:
      version:
        description: tool version
        default: '1.0'
      token:
        description: auth token
        required: true
        deprecationMessage: use OIDC
    outputs:
      path:
        description: installed path
        value: ${{ steps.install.outputs.path }}
      cache-hit:
        description: cache hit
        value: ${{ steps.cache.outputs.cache-hit }}
    runs:
      using: composite
      steps:
      - name: Setup node
        uses: actions/setup-node@v4
        with:
          node-version: 20
      - id: cache
        name: Cache
        uses: actions/cache@v4
        with:
          path: ~/.cache
          key: tools-${{ runner.os }}
      - id: install
        run: |
          "$GITHUB_ACTION_PATH/install.sh" ${{ inputs.version }} ${{ inputs.token }}
          echo "path=$HOME/tools" >> $GITHUB_OUTPUT
        shell: bash
      - if: steps.cache.outputs.cache-hit == 'true'
        run: echo cached
        shell: pwsh
    """)
def test_basic_action():
    _tools_body()


@action
def _wrapper():
    name("Wrapper")
    description("Calls another action")
    tools = _tools(version="2.0", token=t"{github.token}")
    output("forwarded", value=tools.outputs.path)


@action(path=".")
def _root():
    name("Root")
    description("Root action")
    run("true")


@expect("""\
    # generated from test_action.py::test_call_actions
    on:
      workflow_dispatch: {}
    defaults:
      run:
        shell: bash
    jobs:
      test_call_actions:
        runs-on: ubuntu-latest
        steps:
        - name: Wrapper
          uses: $/.github/actions/_wrapper
        - id: t
          name: Setup tools
          uses: $/.github/actions/tools
          with:
            token: x
        - run: echo ${{ steps.t.outputs.path }} ${{ steps.t.outputs.cache-hit }}
        - name: Root
          uses: $/
    """)
def test_call_actions():
    on.workflow_dispatch()
    _wrapper()
    t = _tools(token="x")
    run(t"echo {t.outputs.path} {t.outputs.cache_hit}")
    _root()


@expect_action("""\
    # generated from test_action.py::test_call_action_from_action
    name: Outer
    description: Calls a wrapper
    outputs:
      path:
        description: out
        value: ${{ steps.w.outputs.path }}
    runs:
      using: composite
      steps:
      - id: w
        name: Wrapper
        uses: $/.github/actions/_wrapper
    """)
def test_call_action_from_action():
    name("Outer")
    description("Calls a wrapper")
    w = _wrapper()
    output("out", value=w.outputs.path)


def test_action_call_errors(error):
    error.id = "test_action_call_errors"

    def body():
        on.workflow_dispatch()
        error("action `tools` has no inputs nope")
        _tools(token="x", nope=1)
        error("action `tools` requires inputs token")
        _tools()

    with error:
        _ = workflow(body, id="test_action_call_errors").worfklow


def test_action_needs_name(error):
    @action
    def a():
        description("d")
        run("x")

    error.id = "a"
    with error:
        error("action `a` must have a `name`")
        _ = a.action


def test_action_needs_description(error):
    @action
    def a():
        name("n")
        run("x")

    error.id = "a"
    with error:
        error("action `a` must have a `description`")
        _ = a.action


def test_action_needs_steps(error):
    @action
    def a():
        name("n")
        description("d")

    error.id = "a"
    with error:
        error("action `a` must have at least one step")
        _ = a.action


def test_action_needs_outputs_set(error):
    @action
    def a():
        name("n")
        description("d")
        output(id="unset")
        run("x")

    error.id = "a"
    with error:
        error("action `a` has no value set for unset")
        _ = a.action


def test_action_output_without_value(error):
    @action
    def a():
        name("n")
        description("d")
        result = output("Result")
        run("x")

    error.id = "a"
    with error:
        error("action `a` has no value set for result")
        _ = a.action


@expect_action("""\
    # generated from test_action.py::test_unused_required_input
    name: Required input
    description: An unused required input
    inputs:
      token:
        required: true
    runs:
      using: composite
      steps:
      - run: 'true'
        shell: bash
    """)
def test_unused_required_input():
    _required_input_body()


def _required_input_body():
    name("Required input")
    description("An unused required input")
    token = input(required=True)
    run("true")


required_action = action(_required_input_body, id="required")


def test_unused_required_input_invocation(error):
    error.id = "test_unused_required_input_invocation"

    def body():
        on.workflow_dispatch()
        error("action `required` requires inputs token")
        required_action()

    with error:
        _ = workflow(body, id=error.id).worfklow


@expect_action("""\
    # generated from test_action.py::test_expression_default
    name: Token
    description: Expression default
    inputs:
      token:
        default: ${{ github.token }}
    runs:
      using: composite
      steps:
      - run: 'true'
        shell: bash
    """)
def test_expression_default():
    name("Token")
    description("Expression default")
    token = input(default=github.token)
    run("true")


@expect("""\
    # generated from test_action.py::test_underscore_input_id
    on:
      workflow_dispatch: {}
    defaults:
      run:
        shell: bash
    jobs:
      test_underscore_input_id:
        runs-on: ubuntu-latest
        steps:
        - name: Underscore input
          uses: $/.github/actions/underscore_input
          with:
            my_input: value
    """)
def test_underscore_input_id():
    @action
    def underscore_input():
        name("Underscore input")
        description("Uses an underscore input ID")
        my_input = input(id="my_input", required=True)
        run(t"echo {my_input}")

    on.workflow_dispatch()
    underscore_input(my_input="value")


@expect("""\
    # generated from test_action.py::test_input_output_in_workflow
    on:
      workflow_call:
        outputs:
          x:
            description: forwarded
            value: ${{ jobs.j.outputs.x }}
      workflow_dispatch:
        inputs:
          version:
            description: the version
            required: false
            default: '1.0'
            type: string
          verbose:
            required: false
            default: false
            type: boolean
    defaults:
      run:
        shell: bash
    jobs:
      j:
        runs-on: ubuntu-latest
        outputs:
          x: 1
        steps:
        - run: echo ${{ inputs.version }} ${{ inputs.verbose }}
    """)
def test_input_output_in_workflow():
    on.workflow_dispatch()
    version = input("the version", default="1.0")
    verbose = input.default(False)

    @job
    def j():
        run(t"echo {version} {verbose}")
        outputs(x=1)

    output("forwarded", value=j.outputs.x)


@pytest.mark.parametrize("root_kind", ["workflow", "action"])
def test_generate_called_actions(repo, root_kind):
    repo.file(
        "action_dependency_helpers.py",
        """\
        from ghgen.syntax import *

        @action
        def leaf():
            name("Leaf")
            description("Imported leaf")
            run("true")

        @action
        def wrapper():
            name("Wrapper")
            description("Imported wrapper")
            leaf()
        """,
    )
    setup = (
        "on.workflow_dispatch()"
        if root_kind == "workflow"
        else 'name("Root"); description("Root action")'
    )
    repo.file(
        ".github/workflows/entry.py",
        f"""\
        import action_dependency_helpers as helpers
        from ghgen.syntax import *

        # Dependencies must survive a cached action build.
        helpers.wrapper.action

        @{root_kind}
        def root():
            {setup}

            @action
            def local():
                name("Local")
                description("Workflow-local action")
                helpers.wrapper()

            local()
            local()
        """,
    )
    command = [
        sys.executable,
        "-c",
        "import sys; from ghgen import main; raise SystemExit(main(sys.argv[1:]))",
    ]

    def generate(*args):
        return subprocess.run(command + list(args), capture_output=True, text=True)

    result = generate("--check")
    assert result.returncode == 1
    assert "leaf/action.yml" in result.stderr
    assert not list(repo.path.rglob("*.yml"))

    result = generate()
    assert result.returncode == 0, result.stderr
    expected = {
        repo.path / ".github/actions" / action_id / "action.yml"
        for action_id in ("local", "wrapper", "leaf")
    }
    expected.add(
        repo.path / ".github/workflows/root.yml"
        if root_kind == "workflow"
        else repo.path / ".github/actions/root/action.yml"
    )
    assert set(repo.path.rglob("*.yml")) == expected
    assert generate("--check").returncode == 0

    leaf = repo.path / ".github/actions/leaf/action.yml"
    leaf.unlink()
    result = generate("--check")
    assert result.returncode == 1
    assert "leaf/action.yml" in result.stderr
    assert not leaf.exists()


@pytest.mark.parametrize("check", [False, True])
def test_conflicting_action_paths(repo, check):
    repo.file(
        ".github/workflows/conflicts.py",
        """\
        from ghgen.syntax import *

        @workflow
        def first():
            on.workflow_dispatch()

            @action(path=".github/actions/setup")
            def setup():
                name("First")
                description("First setup")
                run("echo first")

            setup()

        @workflow
        def second():
            on.workflow_dispatch()

            @action(path=".github/actions/other/../setup")
            def setup():
                name("Second")
                description("Second setup")
                run("echo second")

            setup()
        """,
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from ghgen import main; raise SystemExit(main(sys.argv[1:]))",
            *(["--check"] if check else []),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert (
        "conflicting action definitions for .github/actions/setup/action.yml"
        in result.stderr
    )
    output_file = repo.path / ".github/actions/setup/action.yml"
    if check:
        assert not output_file.exists()
    else:
        assert "echo first" in output_file.read_text()
        assert "echo second" not in output_file.read_text()


def test_reimported_action_definition(repo):
    repo.file(
        ".github/workflows/shared.py",
        """\
        from ghgen.syntax import *

        @action
        def setup():
            name("Setup")
            description("Shared setup")
            run("true")
        """,
    )
    repo.file(
        ".github/workflows/entry.py",
        """\
        from ghgen.syntax import *
        from shared import setup

        @workflow
        def entry():
            on.workflow_dispatch()
            setup()
            setup()
        """,
    )
    for args in ([], ["--check"]):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from ghgen import main; raise SystemExit(main(sys.argv[1:]))",
                *args,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    assert (repo.path / ".github/actions/setup/action.yml").exists()
