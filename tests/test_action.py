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
    output("cache hit", id="hit", value=cache.outputs.cache_hit)


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
      hit:
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
        - run: echo ${{ steps.t.outputs.path }} ${{ steps.t.outputs.hit }}
        - name: Root
          uses: $/.
    """)
def test_call_actions():
    on.workflow_dispatch()
    _wrapper()
    t = _tools(token="x")
    run(t"echo {t.outputs.path} {t.outputs.hit}")
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


@expect_errors
def test_action_builders_in_workflow(error):
    on.workflow_dispatch()
    error("`input` must be used in an action")
    input("x")
    error("`output` must be used in an action")
    output("y", value=1)
    run("true")
