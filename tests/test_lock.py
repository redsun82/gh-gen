import io
import pathlib
import subprocess
import textwrap
from unittest import mock

import pytest

from src.ghgen import main


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
    config.expect_diff(
        """\
        @@ -0,0 +1,4 @@
        +uses:
        +  foo:
        +    uses: ./my/actions/foo
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
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
        """
    )
    assert pathlib.Path(".github", "workflows", "actions.py").exists()

    # add another one
    repo.file(
        "my/actions/bar/action.yml",
        """\
        run: {}
        """,
    )
    main(["add", "my_bar=./my/actions/bar", "-v"])
    config.expect_diff(
        """\
        @@ -2,3 +2,6 @@
           foo:
             uses: ./my/actions/foo
             pin: false
        +  my_bar:
        +    uses: ./my/actions/bar
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -12,3 +12,8 @@
           - output1
           - output2
           path: my/actions/foo
        +- id: my_bar
        +  name: My bar
        +  inputs: []
        +  outputs: []
        +  path: my/actions/bar
        """
    )

    # reject same name
    monkeypatch.setattr("builtins.input", lambda _: "n")
    main(["add", "other=./my/actions/foo", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # accept same name overwrite
    monkeypatch.setattr("builtins.input", lambda _: "y")
    main(["add", "other=./my/actions/foo", "-v"])
    config.expect_diff(
        """\
        @@ -5,3 +5,6 @@
           my_bar:
             uses: ./my/actions/bar
             pin: false
        +  other:
        +    uses: ./my/actions/foo
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
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
        """
    )

    # lock regenerated
    lock.path.unlink()
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # remove one
    main(["remove", "foo"])
    config.expect_diff(
        """\
        @@ -1,7 +1,4 @@
         uses:
        -  foo:
        -    uses: ./my/actions/foo
        -    pin: false
           my_bar:
             uses: ./my/actions/bar
             pin: false
        """
    )
    lock.expect_diff(
        """\
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
        """
    )

    foo.write(
        """\
        name: Foo Changed
        inputs:
            another-input:
                description: Input 1
                required: true
        """
    )
    main(["update", "-v"])
    config.expect_unchanged()
    lock.expect_diff(
        """\
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
        """
    )


class MockedGhApi:
    def __init__(self, monkeypatch):
        self.calls = {}

        def mock_subprocess_popen(cmd, *, text, stdout, stderr=None):
            assert text is True
            assert stdout is subprocess.PIPE
            ret = mock.MagicMock()
            ret.__enter__.return_value = ret
            try:
                value = self.calls[tuple(cmd)]
                ret.returncode = 0
                ret.stdout = io.StringIO(value)
            except KeyError:
                ret.returncode = 1
            return ret

        monkeypatch.setattr("subprocess.Popen", mock_subprocess_popen)

    def add(
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
            ] = f"{version}\n"
        self.calls[
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github.v3.raw",
            f"repos/{target}/contents/{path_to_action}?ref={version}",
        ] = textwrap.dedent(contents)
        if sha is not None:
            self.calls[
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{target}/git/ref/{'heads' if as_branch else 'tags'}/{version}",
                "--jq",
                ".object.sha",
            ] = f"{sha}\n"

    def clear(self):
        self.calls.clear()

    def __call__(self, *args, **kwargs):
        self.clear()
        self.add(*args, **kwargs)


@pytest.fixture
def mock_gh_api_calls(monkeypatch):
    return MockedGhApi(monkeypatch)


def test_remote(repo, mock_gh_api_calls):
    config = repo.config()
    lock = repo.lock()
    mock_gh_api_calls(
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
    config.expect_diff(
        """\
        @@ -0,0 +1,4 @@
        +uses:
        +  repo:
        +    uses: owner/repo@v2
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -0,0 +1,17 @@
        +actions:
        +- pinned: false
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
        """
    )

    # fetch latest version
    mock_gh_api_calls(
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
    config.expect_diff(
        """\
        @@ -2,3 +2,6 @@
           repo:
             uses: owner/repo@v2
             pin: false
        +  foo:
        +    uses: owner/foo
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -1,4 +1,16 @@
         actions:
        +- pinned: false
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
         - pinned: false
           id: repo
           name: My Action
        """
    )

    # test with path
    mock_gh_api_calls(
        "owner/repo",
        "v1.0.0",
        "bar_sha",
        """\
        name: Bar Action
        """,
        path="path/to/bar",
    )
    main(["add", "owner/repo/path/to/bar@v1.0.0", "-v"])
    config.expect_diff(
        """\
        @@ -5,3 +5,6 @@
           foo:
             uses: owner/foo
             pin: false
        +  repo_path_to_bar:
        +    uses: owner/repo/path/to/bar@v1.0.0
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -27,3 +27,13 @@
           path: ''
           ref: v2
           resolved-ref: v2
        +- pinned: false
        +  id: repo_path_to_bar
        +  name: Bar Action
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: repo
        +  path: path/to/bar
        +  ref: v1.0.0
        +  resolved-ref: v1.0.0
        """
    )

    # add with branch
    mock_gh_api_calls(
        "owner/bar",
        "main",
        "branch_sha",
        """\
        name: Branch Action
        """,
        as_branch=True,
    )
    main(["add", "x=owner/bar@main", "-v"])
    config.expect_diff(
        """\
        @@ -8,3 +8,6 @@
           repo_path_to_bar:
             uses: owner/repo/path/to/bar@v1.0.0
             pin: false
        +  x:
        +    uses: owner/bar@main
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -37,3 +37,13 @@
           path: path/to/bar
           ref: v1.0.0
           resolved-ref: v1.0.0
        +- pinned: false
        +  id: x
        +  name: Branch Action
        +  inputs: []
        +  outputs: []
        +  owner: owner
        +  repo: bar
        +  path: ''
        +  ref: main
        +  resolved-ref: main
        """
    )

    # update one
    mock_gh_api_calls(
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
    lock.expect_diff(
        """\
        @@ -10,7 +10,7 @@
           owner: owner
           repo: foo
           path: ''
        -  resolved-ref: v3.2.1
        +  resolved-ref: v3.3.3
         - pinned: false
           id: repo
           name: My Action
        """
    )

    # remove one
    main(["remove", "repo_path_to_bar", "-v"])
    config.expect_diff(
        """\
        @@ -5,9 +5,6 @@
           foo:
             uses: owner/foo
             pin: false
        -  repo_path_to_bar:
        -    uses: owner/repo/path/to/bar@v1.0.0
        -    pin: false
           x:
             uses: owner/bar@main
             pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -28,16 +28,6 @@
           ref: v2
           resolved-ref: v2
         - pinned: false
        -  id: repo_path_to_bar
        -  name: Bar Action
        -  inputs: []
        -  outputs: []
        -  owner: owner
        -  repo: repo
        -  path: path/to/bar
        -  ref: v1.0.0
        -  resolved-ref: v1.0.0
        -- pinned: false
           id: x
           name: Branch Action
           inputs: []
        """
    )

    # update all
    mock_gh_api_calls.clear()
    mock_gh_api_calls.add(
        "owner/repo",
        "v2",
        "updated_sha_repo",
        """\
        name: Repo Action
        """,
    )
    mock_gh_api_calls.add(
        "owner/foo",
        "v4.2.1",
        "updated_sha_foo",
        """\
        name: Foo Action
        """,
        as_latest=True,
    )
    mock_gh_api_calls.add(
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
    lock.expect_diff(
        """\
        @@ -2,25 +2,16 @@
         - pinned: false
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
        +  resolved-ref: v4.2.1
         - pinned: false
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
        @@ -29,7 +20,7 @@
           resolved-ref: v2
         - pinned: false
           id: x
        -  name: Branch Action
        +  name: Bar Action
           inputs: []
           outputs: []
           owner: owner
        """
    )
    mock_gh_api_calls(
        "owner/baz",
        "v2",
        None,
        """\
        name: Baz Action
        """,
    )
    main(["add", "owner/baz@v2", "--no-pin", "-v"])
    config.expect_diff(
        """\
        @@ -8,3 +8,6 @@
           x:
             uses: owner/bar@main
             pin: false
        +  baz:
        +    uses: owner/baz@v2
        +    pin: false
        """
    )
    lock.expect_diff(
        """\
        @@ -1,4 +1,14 @@
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
         - pinned: false
           id: foo
           name: Foo Action
        """
    )


def test_sync(repo, mock_gh_api_calls):
    config = repo.config(
        """\
        uses:
            foo: owner/foo@v2
            bar: ./my/bar
        """
    )
    lock = repo.lock()
    repo.file(
        "my/bar/action.yml",
        """\
        name: Bar Action
        """,
    )
    mock_gh_api_calls(
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
    lock.expect_diff(
        """\
        @@ -0,0 +1,20 @@
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
        """
    )
    config.write(
        """\
        uses:
            foo: owner/foo@v2
            bar:
             uses: ./my/bar
             name: Bar
        """
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff(
        """\
        @@ -1,6 +1,7 @@
         actions:
         - id: bar
        -  name: Bar Action
        +  requested-name: Bar
        +  name: Bar
           inputs: []
           outputs: []
           path: my/bar
        """
    )
    config.write(
        """\
        uses:
            foo: owner/foo@v2
        """
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff(
        """\
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
        """
    )
    config.write(
        """\
        uses:
            foo: owner/foo@v3
        """
    )
    mock_gh_api_calls(
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
    lock.expect_diff(
        """\
        @@ -6,10 +6,12 @@
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
        """
    )


def test_trusted_owners(repo, mock_gh_api_calls):
    config = repo.config(
        """\
        uses:
            checkout: actions/checkout@v4
            foo: owner1/foo@v1
        """
    )
    lock = repo.lock()
    mock_gh_api_calls.add(
        "owner1/foo",
        "v1",
        "foo_sha_v1",
        """\
        {}
        """,
    )
    mock_gh_api_calls.add(
        "actions/checkout",
        "v4",
        None,
        """\
        {}
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff(
        """\
        @@ -0,0 +1,22 @@
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
        """
    )
    config = repo.config(
        """\
        trusted-owners:
            - owner1
        uses:
            checkout: actions/checkout@v4
            foo: owner1/foo@v1
        """
    )
    mock_gh_api_calls.clear()
    mock_gh_api_calls.add(
        "owner1/foo",
        "v1",
        None,
        """\
        {}
        """,
    )
    mock_gh_api_calls.add(
        "actions/checkout",
        "v4",
        "checkout_sha_v4",
        """\
        {}
        """,
    )
    main(["sync", "-v"])
    config.expect_unchanged()
    lock.expect_diff(
        """\
        @@ -1,5 +1,5 @@
         actions:
        -- pinned: false
        +- pinned: true
           id: checkout
           name: Checkout
           inputs: []
        @@ -9,7 +9,8 @@
           path: ''
           ref: v4
           resolved-ref: v4
        -- pinned: true
        +  sha: checkout_sha_v4
        +- pinned: false
           id: foo
           name: Foo
           inputs: []
        @@ -19,4 +20,3 @@
           path: ''
           ref: v1
           resolved-ref: v1
        -  sha: foo_sha_v1
        """
    )
