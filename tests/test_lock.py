import io
import os
import pathlib
import subprocess
import textwrap
from unittest import mock

import pytest

from src.ghgen import main
from src.ghgen.commands.utils import yaml
from conftest import Call


def test_local(repo, monkeypatch):
    config = repo.config()
    lock = repo.lock()
    foo = repo.file(
        "my/actions/foo/action.yml",
        """\
        name: Foo Action
        inputs:
            input1:
                description: Input 1
                required: true
            input2:
                description: Input 2
                required: false
        outputs:
            output1:
                description: Output 1
            output2:
                description: Output 2
        """,
    )
    main(["add", "./my/actions/foo", "-v"])
    config.expect_diff("""\
        @@ -0,0 +1,2 @@
        +uses:
        +  foo: ./my/actions/foo
        """)
    lock.expect_diff("""\
        @@ -0,0 +1,14 @@
        +actions:
        +- id: foo
        +  name: Foo Action
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  outputs:
        +  - output1
        +  - output2
        +  path: my/actions/foo
        """)
    assert pathlib.Path(".github", "workflows", "actions.py").exists()

    # add another one
    repo.file(
        "my/actions/bar/action.yml",
        """\
        run: {}
        """,
    )
    main(["add", "my_bar=./my/actions/bar", "-v"])
    config.expect_diff("""\
        @@ -1,2 +1,3 @@
         uses:
           foo: ./my/actions/foo
        +  my_bar: ./my/actions/bar
        """)
    lock.expect_diff("""\
        @@ -12,3 +12,8 @@
           - output1
           - output2
           path: my/actions/foo
        +- id: my_bar
        +  name: My bar
        +  inputs: []
        +  outputs: []
        +  path: my/actions/bar
        """)

    # reject same name
    monkeypatch.setattr("builtins.input", lambda _: "n")
    main(["add", "other=./my/actions/foo", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # accept same name overwrite
    monkeypatch.setattr("builtins.input", lambda _: "y")
    main(["add", "other=./my/actions/foo", "-v"])
    config.expect_diff("""\
        @@ -1,3 +1,4 @@
         uses:
           foo: ./my/actions/foo
           my_bar: ./my/actions/bar
        +  other: ./my/actions/foo
        """)
    lock.expect_diff("""\
        @@ -17,3 +17,16 @@
           inputs: []
           outputs: []
           path: my/actions/bar
        +- id: other
        +  name: Foo Action
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  outputs:
        +  - output1
        +  - output2
        +  path: my/actions/foo
        """)

    # lock regenerated
    lock.path.unlink()
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # remove one
    main(["remove", "foo"])
    config.expect_diff("""\
        @@ -1,4 +1,3 @@
         uses:
        -  foo: ./my/actions/foo
           my_bar: ./my/actions/bar
           other: ./my/actions/foo
        """)
    lock.expect_diff("""\
        @@ -1,17 +1,4 @@
         actions:
        -- id: foo
        -  name: Foo Action
        -  inputs:
        -  - name: input1
        -    id: input1
        -    required: true
        -  - name: input2
        -    id: input2
        -    required: false
        -  outputs:
        -  - output1
        -  - output2
        -  path: my/actions/foo
         - id: my_bar
           name: My bar
           inputs: []
        """)

    foo.write("""\
        name: Foo Changed
        inputs:
            another-input:
                description: Input 1
                required: true
        """)
    main(["update", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -5,15 +5,10 @@
           outputs: []
           path: my/actions/bar
         - id: other
        -  name: Foo Action
        +  name: Foo Changed
           inputs:
        -  - name: input1
        -    id: input1
        +  - name: another_input
        +    id: another-input
             required: true
        -  - name: input2
        -    id: input2
        -    required: false
        -  outputs:
        -  - output1
        -  - output2
        +  outputs: []
           path: my/actions/foo
        """)


def test_update_regenerates_workflows(repo, mock_gh_api_call):
    config = repo.config("""\
        uses:
            repo: owner/repo@v2
        """)
    lock = repo.lock()
    repo.file(
        ".github/workflows/wf.py",
        """\
        from src.ghgen.syntax import *


        @workflow
        def wf():
            on.push()
            run("echo hello")
        """,
    )
    workflow_yml = repo.file(".github/workflows/wf.yml")
    mock_gh_api_call(
        "owner/repo",
        "v2",
        "sha_v2",
        """\
        name: My Action
        inputs:
            an-input:
                required: false
        """,
    )
    # `update` fetches the action *and* regenerates the workflow in one shot
    assert main(["update", "-v"]) == 0
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -0,0 +1,16 @@
        +actions:
        +- pinned: true
        +  id: repo
        +  name: My Action
        +  inputs:
        +  - name: an_input
        +    id: an-input
        +    required: false
        +  outputs: []
        +  owner: owner
        +  repo: repo
        +  path: ''
        +  ref: v2
        +  resolved-ref: v2
        +  sha: sha_v2
        +  trusted: false
        """)
    workflow_yml.expect_diff("""\
        @@ -0,0 +1,11 @@
        +# generated from wf.py::wf
        +on:
        +  push: {}
        +defaults:
        +  run:
        +    shell: bash
        +jobs:
        +  wf:
        +    runs-on: ubuntu-latest
        +    steps:
        +    - run: echo hello
        """)


class MockedGhApi:
    def __init__(self, monkeypatch):
        self.calls = {}

        def mock_subprocess_popen(cmd, *, text, stdout, stderr=None):
            assert text is True
            assert stdout is subprocess.PIPE
            ret = mock.MagicMock()
            ret.__enter__.return_value = ret
            try:
                _, value = self.calls.pop(tuple(cmd))
                ret.returncode = 0
                ret.stdout = io.StringIO(value)
            except KeyError:
                ret.returncode = 1
                ret.stdout = io.StringIO()
            return ret

        monkeypatch.setattr("subprocess.Popen", mock_subprocess_popen)

    def __call__(
        self,
        target: str,
        version: str,
        sha: str | None,
        contents: str,
        *,
        as_latest: bool = False,
        as_branch: bool = False,
        path: str | None = None,
    ):
        this_call = Call.get()
        path_to_action = "action.yml" if path is None else f"{path}/action.yml"
        if as_latest:
            self.calls[
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{target}/releases/latest",
                "--jq",
                ".tag_name",
            ] = (this_call, f"{version}\n")
        self.calls[
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.v3.raw",
            f"repos/{target}/contents/{path_to_action}?ref={sha or version}",
        ] = (this_call, textwrap.dedent(contents))
        if sha is not None:
            self.calls[
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{target}/git/ref/{'heads' if as_branch else 'tags'}/{version}",
                "--jq",
                ".object.sha",
            ] = (this_call, f"{sha}\n")

    def narrowing(self, target: str, ref: str, sha: str, tags: dict[str, str]):
        """Mock narrowing `ref`, pointing to `sha`, with `tags` mapping names to shas."""
        this_call = Call.get()
        self.calls[
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{target}/commits/{ref}",
            "--jq",
            ".sha",
        ] = (this_call, f"{sha}\n")
        self._tags(this_call, target, tags)

    def tags(self, target: str, tags: dict[str, str]):
        """Mock narrowing on an already locked commit, with `tags` mapping names to shas."""
        self._tags(Call.get(), target, tags)

    def _tags(self, this_call: Call, target: str, tags: dict[str, str]):
        self.calls[
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{target}/tags",
            "--jq",
            r'.[] | "\(.commit.sha) \(.name)"',
            "--paginate",
        ] = (this_call, "".join(f"{s} {n}\n" for n, s in tags.items()))


@pytest.fixture
def mock_gh_api_call(monkeypatch):
    ret = MockedGhApi(monkeypatch)
    yield ret
    if ret.calls:
        pytest.fail(
            f"Not all expected API calls were made.\nMissing:\n{"\n".join(f"  {" ".join(k)} (from {c.location})" for k, (c, _) in ret.calls.items())}"
        )


def test_remote(repo, mock_gh_api_call):
    config = repo.config()
    lock = repo.lock()
    mock_gh_api_call(
        "owner/repo",
        "v2",
        "this_is_a_sha",
        """\
        name: My Action
        inputs:
            input1:
                description: Input 1
                required: true
            input2:
                description: Input 2
                required: false
        """,
    )
    main(["add", "owner/repo@v2", "-v"])
    config.expect_diff("""\
        @@ -0,0 +1,2 @@
        +uses:
        +  repo: owner/repo@v2
        """)
    lock.expect_diff("""\
        @@ -0,0 +1,19 @@
        +actions:
        +- pinned: true
        +  id: repo
        +  name: My Action
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  outputs: []
        +  owner: owner
        +  repo: repo
        +  path: ''
        +  ref: v2
        +  resolved-ref: v2
        +  sha: this_is_a_sha
        +  trusted: false
        """)

    # fetch latest version
    mock_gh_api_call(
        "owner/foo",
        "v3.2.1",
        "another_sha",
        """\
        name: Foo Action
        inputs:
            an-input:
                description: Input 1
                required: true
        """,
        as_latest=True,
    )
    main(["add", "owner/foo", "-v"])
    config.expect_diff("""\
        @@ -1,2 +1,3 @@
         uses:
           repo: owner/repo@v2
        +  foo: owner/foo
        """)
    lock.expect_diff("""\
        @@ -1,4 +1,18 @@
         actions:
        +- pinned: true
        +  id: foo
        +  name: Foo Action
        +  inputs:
        +  - name: an_input
        +    id: an-input
        +    required: true
        +  outputs: []
        +  owner: owner
        +  repo: foo
        +  path: ''
        +  resolved-ref: v3.2.1
        +  sha: another_sha
        +  trusted: false
         - pinned: true
           id: repo
           name: My Action
        """)

    # test with path
    mock_gh_api_call(
        "owner/repo",
        "v1.0.0",
        "bar_sha",
        """\
        name: Bar Action
        """,
        path="path/to/bar",
    )
    main(["add", "owner/repo/path/to/bar@v1.0.0", "-v"])
    config.expect_diff("""\
        @@ -1,3 +1,4 @@
         uses:
           repo: owner/repo@v2
           foo: owner/foo
        +  repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
        """)
    lock.expect_diff("""\
        @@ -31,3 +31,15 @@
           resolved-ref: v2
           sha: this_is_a_sha
           trusted: false
        +- pinned: true
        +  id: repo_path_to_bar
        +  name: Bar Action
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: repo
        +  path: path/to/bar
        +  ref: v1.0.0
        +  resolved-ref: v1.0.0
        +  sha: bar_sha
        +  trusted: false
        """)

    # add with branch
    mock_gh_api_call(
        "owner/bar",
        "main",
        "branch_sha",
        """\
        name: Branch Action
        """,
        as_branch=True,
    )
    main(["add", "x=owner/bar@main", "-v"])
    config.expect_diff("""\
        @@ -2,3 +2,4 @@
           repo: owner/repo@v2
           foo: owner/foo
           repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
        +  x: owner/bar@main
        """)
    lock.expect_diff("""\
        @@ -43,3 +43,15 @@
           resolved-ref: v1.0.0
           sha: bar_sha
           trusted: false
        +- pinned: true
        +  id: x
        +  name: Branch Action
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: bar
        +  path: ''
        +  ref: main
        +  resolved-ref: main
        +  sha: branch_sha
        +  trusted: false
        """)

    # update one
    mock_gh_api_call(
        "owner/foo",
        "v3.3.3",
        "updated_sha",
        """\
        name: Foo Action
        inputs:
            an-input:
                description: Input 1
                required: true
        """,
        as_latest=True,
    )
    main(["update", "foo", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -10,8 +10,8 @@
           owner: owner
           repo: foo
           path: ''
        -  resolved-ref: v3.2.1
        -  sha: another_sha
        +  resolved-ref: v3.3.3
        +  sha: updated_sha
           trusted: false
         - pinned: true
           id: repo
        """)

    # remove one
    main(["remove", "repo_path_to_bar", "-v"])
    config.expect_diff("""\
        @@ -1,5 +1,4 @@
         uses:
           repo: owner/repo@v2
           foo: owner/foo
        -  repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
           x: owner/bar@main
        """)
    lock.expect_diff("""\
        @@ -32,18 +32,6 @@
           sha: this_is_a_sha
           trusted: false
         - pinned: true
        -  id: repo_path_to_bar
        -  name: Bar Action
        -  inputs: []
        -  outputs: []
        -  owner: owner
        -  repo: repo
        -  path: path/to/bar
        -  ref: v1.0.0
        -  resolved-ref: v1.0.0
        -  sha: bar_sha
        -  trusted: false
        -- pinned: true
           id: x
           name: Branch Action
           inputs: []
        """)

    # update all
    mock_gh_api_call(
        "owner/repo",
        "v2",
        "updated_sha_repo",
        """\
        name: Repo Action
        """,
    )
    mock_gh_api_call(
        "owner/foo",
        "v4.2.1",
        "updated_sha_foo",
        """\
        name: Foo Action
        """,
        as_latest=True,
    )
    mock_gh_api_call(
        "owner/bar",
        "main",
        "updated_sha_bar",
        """\
        name: Bar Action
        """,
        as_branch=True,
    )
    main(["update", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -2,38 +2,29 @@
         - pinned: true
           id: foo
           name: Foo Action
        -  inputs:
        -  - name: an_input
        -    id: an-input
        -    required: true
        +  inputs: []
           outputs: []
           owner: owner
           repo: foo
           path: ''
        -  resolved-ref: v3.3.3
        -  sha: updated_sha
        +  resolved-ref: v4.2.1
        +  sha: updated_sha_foo
           trusted: false
         - pinned: true
           id: repo
        -  name: My Action
        -  inputs:
        -  - name: input1
        -    id: input1
        -    required: true
        -  - name: input2
        -    id: input2
        -    required: false
        +  name: Repo Action
        +  inputs: []
           outputs: []
           owner: owner
           repo: repo
           path: ''
           ref: v2
           resolved-ref: v2
        -  sha: this_is_a_sha
        +  sha: updated_sha_repo
           trusted: false
         - pinned: true
           id: x
        -  name: Branch Action
        +  name: Bar Action
           inputs: []
           outputs: []
           owner: owner
        @@ -41,5 +32,5 @@
           path: ''
           ref: main
           resolved-ref: main
        -  sha: branch_sha
        +  sha: updated_sha_bar
           trusted: false
        """)
    mock_gh_api_call(
        "owner/baz",
        "v2",
        None,
        """\
        name: Baz Action
        """,
    )
    main(["add", "owner/baz@v2", "--no-pin", "-v"])
    config.expect_diff("""\
        @@ -2,3 +2,6 @@
           repo: owner/repo@v2
           foo: owner/foo
           x: owner/bar@main
        +  baz:
        +    uses: owner/baz@v2
        +    pin: false
        """)
    lock.expect_diff("""\
        @@ -1,4 +1,15 @@
         actions:
        +- pinned: false
        +  id: baz
        +  name: Baz Action
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: baz
        +  path: ''
        +  ref: v2
        +  resolved-ref: v2
        +  trusted: false
         - pinned: true
           id: foo
           name: Foo Action
        """)


def test_sync(repo, mock_gh_api_call):
    config = repo.config("""\
        uses:
            foo: owner/foo@v2
            bar: ./my/bar
        """)
    lock = repo.lock()
    repo.file(
        "my/bar/action.yml",
        """\
        name: Bar Action
        """,
    )
    mock_gh_api_call(
        "owner/foo",
        "v2",
        "foo_sha",
        """\
        name: Foo Action
        inputs:
            input:
                description: Input
                required: true
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -0,0 +1,21 @@
        +actions:
        +- id: bar
        +  name: Bar Action
        +  inputs: []
        +  outputs: []
        +  path: my/bar
        +- pinned: true
        +  id: foo
        +  name: Foo Action
        +  inputs:
        +  - name: input
        +    id: input
        +    required: true
        +  outputs: []
        +  owner: owner
        +  repo: foo
        +  path: ''
        +  ref: v2
        +  resolved-ref: v2
        +  sha: foo_sha
        +  trusted: false
        """)
    config.write("""\
        uses:
            foo: owner/foo@v2
            bar:
             uses: ./my/bar
             name: Bar
        """)
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -1,6 +1,7 @@
         actions:
         - id: bar
        -  name: Bar Action
        +  requested-name: Bar
        +  name: Bar
           inputs: []
           outputs: []
           path: my/bar
        """)
    config.write("""\
        uses:
            foo: owner/foo@v2
        """)
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -1,10 +1,4 @@
         actions:
        -- id: bar
        -  requested-name: Bar
        -  name: Bar
        -  inputs: []
        -  outputs: []
        -  path: my/bar
         - pinned: true
           id: foo
           name: Foo Action
        """)
    config.write("""\
        uses:
            foo: owner/foo@v3
        """)
    mock_gh_api_call(
        "owner/foo",
        "v3",
        "foo_sha_v3",
        """\
        name: Foo Action
        inputs:
            input:
                required: true
            another-input: {}
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -6,11 +6,13 @@
           - name: input
             id: input
             required: true
        +  - name: another_input
        +    id: another-input
           outputs: []
           owner: owner
           repo: foo
           path: ''
        -  ref: v2
        -  resolved-ref: v2
        -  sha: foo_sha
        +  ref: v3
        +  resolved-ref: v3
        +  sha: foo_sha_v3
           trusted: false
        """)


def test_trusted_owners(repo, mock_gh_api_call):
    config = repo.config("""\
        uses:
            checkout: actions/checkout@v4
            foo: owner1/foo@v1
        """)
    lock = repo.lock()
    mock_gh_api_call(
        "owner1/foo",
        "v1",
        "foo_sha_v1",
        """\
        {}
        """,
    )
    mock_gh_api_call(
        "actions/checkout",
        "v4",
        None,
        """\
        {}
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -0,0 +1,24 @@
        +actions:
        +- pinned: false
        +  id: checkout
        +  name: Checkout
        +  inputs: []
        +  outputs: []
        +  owner: actions
        +  repo: checkout
        +  path: ''
        +  ref: v4
        +  resolved-ref: v4
        +  trusted: true
        +- pinned: true
        +  id: foo
        +  name: Foo
        +  inputs: []
        +  outputs: []
        +  owner: owner1
        +  repo: foo
        +  path: ''
        +  ref: v1
        +  resolved-ref: v1
        +  sha: foo_sha_v1
        +  trusted: false
        """)
    config = repo.config("""\
        trusted-owners:
            - owner1
        uses:
            checkout: actions/checkout@v4
            foo: owner1/foo@v1
        """)
    mock_gh_api_call(
        "owner1/foo",
        "v1",
        None,
        """\
        {}
        """,
    )
    mock_gh_api_call(
        "actions/checkout",
        "v4",
        "checkout_sha_v4",
        """\
        {}
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -1,5 +1,5 @@
         actions:
        -- pinned: false
        +- pinned: true
           id: checkout
           name: Checkout
           inputs: []
        @@ -9,8 +9,9 @@
           path: ''
           ref: v4
           resolved-ref: v4
        -  trusted: true
        -- pinned: true
        +  sha: checkout_sha_v4
        +  trusted: false
        +- pinned: false
           id: foo
           name: Foo
           inputs: []
        @@ -20,5 +21,4 @@
           path: ''
           ref: v1
           resolved-ref: v1
        -  sha: foo_sha_v1
        -  trusted: false
        +  trusted: true
        """)


def test_actions_lock(repo, mock_gh_api_call):
    repo.file(".github/workflows/actions.lock", "version: 1\n")
    config = repo.config("""\
        uses:
            checkout: actions/checkout@v4
            foo: owner/foo@v2.1
            bar: owner/bar
            baz: owner/baz@main
        """)
    lock = repo.lock()
    # highest full tag in the `v4` family on the same commit
    mock_gh_api_call.narrowing(
        "actions/checkout",
        "v4",
        "sha4",
        {
            "v4": "sha4",
            "v4.2.1": "sha4",
            "v4.2.2": "sha4",
            "v4.2.3": "other",
            "v4.3.0-beta": "sha4",
            "v5.0.0": "sha4",
        },
    )
    mock_gh_api_call("actions/checkout", "v4.2.2", None, "{}")
    # no full tag in the `v2.1` family on the same commit
    mock_gh_api_call.narrowing("owner/foo", "v2.1", "shaf", {"v2.0.9": "shaf"})
    mock_gh_api_call("owner/foo", "v2.1", None, "{}")
    # full tags and non-version refs are not narrowed
    mock_gh_api_call("owner/bar", "v3.0.0", None, "{}", as_latest=True)
    mock_gh_api_call("owner/baz", "main", None, "{}")
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff("""\
        @@ -0,0 +1,40 @@
        +actions:
        +- pinned: actions-lock
        +  id: bar
        +  name: Bar
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: bar
        +  path: ''
        +  resolved-ref: v3.0.0
        +- pinned: actions-lock
        +  id: baz
        +  name: Baz
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: baz
        +  path: ''
        +  ref: main
        +  resolved-ref: main
        +- pinned: actions-lock
        +  id: checkout
        +  name: Checkout
        +  inputs: []
        +  outputs: []
        +  owner: actions
        +  repo: checkout
        +  path: ''
        +  ref: v4
        +  resolved-ref: v4.2.2
        +- pinned: actions-lock
        +  id: foo
        +  name: Foo
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: foo
        +  path: ''
        +  ref: v2.1
        +  resolved-ref: v2.1
        """)
    actions = pathlib.Path(".github", "workflows", "actions.py").read_text()
    for spec in (
        "actions/checkout@v4.2.2",
        "owner/foo@v2.1",
        "owner/bar@v3.0.0",
        "owner/baz@main",
    ):
        assert f'"{spec}"' in actions
    assert ".comment(" not in actions


def test_actions_lock_build_metadata_ref(repo, mock_gh_api_call):
    repo.file(".github/workflows/actions.lock", "version: 1\n")
    repo.config("uses:\n  foo: owner/foo@v1.2.3+build.1\n")
    mock_gh_api_call.narrowing("owner/foo", "v1.2.3+build.1", "sha", {})
    # `+` would be decoded as a space in the query
    mock_gh_api_call("owner/foo", "v1.2.3%2Bbuild.1", None, "{}")
    main(["sync", "-v"])
    actions = pathlib.Path(".github", "workflows", "actions.py").read_text()
    assert '"owner/foo@v1.2.3+build.1"' in actions


def test_actions_lock_mode_switch(repo, mock_gh_api_call):
    uses = """\
        uses:
            checkout: actions/checkout@v4
            bar: owner/bar
        """
    config = repo.config(uses)
    lock = repo.lock()
    mock_gh_api_call("actions/checkout", "v4", None, "{}")
    mock_gh_api_call("owner/bar", "v3.1.0", "bar_sha", "{}", as_latest=True)
    main(["sync", "-v"])
    lock.expect_diff("""\
        @@ -0,0 +1,23 @@
        +actions:
        +- pinned: true
        +  id: bar
        +  name: Bar
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: bar
        +  path: ''
        +  resolved-ref: v3.1.0
        +  sha: bar_sha
        +  trusted: false
        +- pinned: false
        +  id: checkout
        +  name: Checkout
        +  inputs: []
        +  outputs: []
        +  owner: actions
        +  repo: checkout
        +  path: ''
        +  ref: v4
        +  resolved-ref: v4
        +  trusted: true
        """)

    # `actions.lock` appearing re-resolves everything without upgrading
    actions_lock = repo.file(".github/workflows/actions.lock", "version: 1\n")
    mock_gh_api_call.narrowing(
        "actions/checkout", "v4", "sha4", {"v4": "sha4", "v4.2.2": "sha4"}
    )
    mock_gh_api_call("actions/checkout", "v4.2.2", None, "{}")
    mock_gh_api_call("owner/bar", "v3.1.0", None, "{}")
    main(["sync", "-v"])
    lock.expect_diff("""\
        @@ -1,5 +1,5 @@
         actions:
        -- pinned: true
        +- pinned: actions-lock
           id: bar
           name: Bar
           inputs: []
        @@ -8,9 +8,7 @@
           repo: bar
           path: ''
           resolved-ref: v3.1.0
        -  sha: bar_sha
        -  trusted: false
        -- pinned: false
        +- pinned: actions-lock
           id: checkout
           name: Checkout
           inputs: []
        @@ -19,5 +17,4 @@
           repo: checkout
           path: ''
           ref: v4
        -  resolved-ref: v4
        -  trusted: true
        +  resolved-ref: v4.2.2
        """)

    # configuration overrides detection
    config.write("actions-lock: false\n" + textwrap.dedent(uses))
    mock_gh_api_call("actions/checkout", "v4", None, "{}")
    mock_gh_api_call("owner/bar", "v3.1.0", "bar_sha", "{}")
    main(["sync", "-v"])
    lock.expect_diff("""\
        @@ -1,5 +1,5 @@
         actions:
        -- pinned: actions-lock
        +- pinned: true
           id: bar
           name: Bar
           inputs: []
        @@ -8,7 +8,9 @@
           repo: bar
           path: ''
           resolved-ref: v3.1.0
        -- pinned: actions-lock
        +  sha: bar_sha
        +  trusted: false
        +- pinned: false
           id: checkout
           name: Checkout
           inputs: []
        @@ -17,4 +19,5 @@
           repo: checkout
           path: ''
           ref: v4
        -  resolved-ref: v4.2.2
        +  resolved-ref: v4
        +  trusted: true
        """)

    actions_lock.path.unlink()
    config.write("actions-lock: true\n" + textwrap.dedent(uses))
    mock_gh_api_call.narrowing(
        "actions/checkout", "v4", "sha4", {"v4": "sha4", "v4.2.2": "sha4"}
    )
    mock_gh_api_call("actions/checkout", "v4.2.2", None, "{}")
    mock_gh_api_call("owner/bar", "v3.1.0", None, "{}")
    main(["sync", "-v"])
    lock.expect_diff("""\
        @@ -1,5 +1,5 @@
         actions:
        -- pinned: true
        +- pinned: actions-lock
           id: bar
           name: Bar
           inputs: []
        @@ -8,9 +8,7 @@
           repo: bar
           path: ''
           resolved-ref: v3.1.0
        -  sha: bar_sha
        -  trusted: false
        -- pinned: false
        +- pinned: actions-lock
           id: checkout
           name: Checkout
           inputs: []
        @@ -19,5 +17,4 @@
           repo: checkout
           path: ''
           ref: v4
        -  resolved-ref: v4
        -  trusted: true
        +  resolved-ref: v4.2.2
        """)


def test_actions_lock_mode_switch_keeps_locked_commit(repo, mock_gh_api_call):
    repo.config("""\
        uses:
            foo: owner/foo@v1
            bar: owner/bar@v2
            baz: owner/baz@main
        """)
    mock_gh_api_call("owner/foo", "v1", "foo_sha", "{}")
    mock_gh_api_call("owner/bar", "v2", "bar_sha", "{}")
    mock_gh_api_call("owner/baz", "main", "baz_sha", "{}", as_branch=True)
    main(["sync", "-v"])

    # all refs moved on since, pinning must stick to the locked commits
    repo.file(".github/workflows/actions.lock", "version: 1\n")
    mock_gh_api_call.tags(
        "owner/foo", {"v1": "new", "v1.0.0": "foo_sha", "v1.1.0": "new"}
    )
    mock_gh_api_call("owner/foo", "v1.0.0", None, "{}")
    # no full tag on the locked commit
    mock_gh_api_call.tags("owner/bar", {"v2": "new", "v2.0.0": "new"})
    mock_gh_api_call("owner/bar", "bar_sha", None, "{}")
    # not a version
    mock_gh_api_call("owner/baz", "baz_sha", None, "{}")
    main(["sync", "-v"])
    actions = pathlib.Path(".github", "workflows", "actions.py").read_text()
    for spec in ("owner/foo@v1.0.0", "owner/bar@bar_sha", "owner/baz@baz_sha"):
        assert f'"{spec}"' in actions
    assert main(["sync", "--check"]) is None


@pytest.mark.parametrize("actions_lock", [True, False])
def test_actions_lock_migrates_legacy_lock(repo, mock_gh_api_call, actions_lock):
    repo.file(".github/workflows/actions.lock", "version: 1\n")
    config = repo.config(
        ("" if actions_lock else "actions-lock: false\n") + textwrap.dedent("""\
            uses:
                bar: owner/bar
                checkout: actions/checkout@v4
            """)
    )
    lock = repo.lock("""\
        actions-lock: true
        actions:
        - pinned: false
          id: bar
          name: Bar
          inputs: []
          outputs: []
          owner: owner
          repo: bar
          path: ''
          resolved-ref: v3.1.0
          trusted: false
        - pinned: false
          id: checkout
          name: Checkout
          inputs: []
          outputs: []
          owner: actions
          repo: checkout
          path: ''
          ref: v4
          resolved-ref: v4.2.2
          trusted: true
        """)
    generated = pathlib.Path(".github/workflows/actions.py")
    for check in (True, False):
        if not actions_lock:
            mock_gh_api_call("owner/bar", "v3.1.0", "bar_sha", "{}")
            mock_gh_api_call("actions/checkout", "v4", None, "{}")
        result = main(["sync", "--check"] if check else ["sync"])
        assert result == (1 if check else None)
        if check:
            lock.expect_unchanged()
            assert not generated.exists()

    expected = yaml.load(lock.contents)
    del expected["actions-lock"]
    if actions_lock:
        for action in expected["actions"]:
            action["pinned"] = "actions-lock"
            del action["trusted"]
    else:
        bar, checkout = expected["actions"]
        bar.update(pinned=True, sha="bar_sha")
        checkout["resolved-ref"] = "v4"
    assert yaml.load(lock.path) == expected
    config.expect_unchanged()
    helpers = generated.read_text()
    assert f'"owner/bar@{"v3.1.0" if actions_lock else "bar_sha"}"' in helpers
    assert f'"actions/checkout@{"v4.2.2" if actions_lock else "v4"}"' in helpers
    assert main(["sync", "--check"]) is None


def test_actions_lock_rejects_pin(repo, caplog):
    repo.file(".github/workflows/actions.lock", "version: 1\n")
    config = repo.config("uses: {}\n")
    assert main(["add", "owner/foo@v1", "--pin"]) == 1
    assert "cannot pin with actions-lock enabled" in caplog.text
    config.expect_unchanged()
    assert not pathlib.Path("gh-gen.lock").exists()


def test_same_repo_local_action(repo):
    config = repo.config()
    lock = repo.lock()
    repo.file(
        "my/actions/foo/action.yml",
        """\
        name: Foo Action
        """,
    )
    main(["add", "$/my/actions/foo", "-v"])
    config.expect_diff("""\
        @@ -0,0 +1,2 @@
        +uses:
        +  foo: $/my/actions/foo
        """)
    lock.expect_diff("""\
        @@ -0,0 +1,7 @@
        +actions:
        +- id: foo
        +  name: Foo Action
        +  inputs: []
        +  outputs: []
        +  path: my/actions/foo
        +  same-repo: true
        """)
    assert (
        '"$/my/actions/foo"' in pathlib.Path(".github/workflows/actions.py").read_text()
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()


@pytest.fixture
def local_action_repo(repo):
    repo.config("""\
        uses:
            foo: ./my/actions/foo
        """)
    repo.file(
        "my/actions/foo/action.yml",
        """\
        name: Foo Action
        inputs:
            bar:
                required: false
        """,
    )
    repo.file(
        ".github/workflows/wf.py",
        """\
        from src.ghgen.syntax import *


        @workflow
        def wf():
            on.push()
            run("echo hello")
        """,
    )
    return repo


def test_check_does_not_write(local_action_repo):
    def files():
        return {
            p: p.read_bytes()
            for p in pathlib.Path().rglob("*")
            if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
        }

    before = files()
    assert main(["--check"]) == 1
    assert files() == before

    assert main([]) == 0
    assert pathlib.Path("gh-gen.lock").exists()
    before = files()
    assert main(["--check"]) == 0
    assert files() == before


def test_write_does_not_cross_directories(local_action_repo, monkeypatch):
    # renaming across filesystems fails with EXDEV, e.g. from /tmp into the repo
    def same_dir_only(orig):
        def wrapper(src, dst, *args, **kwargs):
            src, dst = pathlib.Path(src).resolve(), pathlib.Path(dst).resolve()
            if src.parent != dst.parent:
                raise OSError(18, "Invalid cross-device link")
            return orig(src, dst, *args, **kwargs)

        return wrapper

    monkeypatch.setattr("os.replace", same_dir_only(os.replace))
    monkeypatch.setattr("os.rename", same_dir_only(os.rename))
    assert main([]) == 0
    assert pathlib.Path("gh-gen.lock").exists()
    assert pathlib.Path(".github/workflows/actions.py").exists()
    assert pathlib.Path(".github/workflows/wf.yml").exists()
