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
        """,
    )
    main(["add", "./my/actions/foo", "-v"])
    config.expect_diff(
        """\
        @@ -0,0 +1,2 @@
        +uses:
        +  foo: ./my/actions/foo
        """
    )
    lock.expect_diff(
        """\
        @@ -0,0 +1,11 @@
        +actions:
        +- id: foo
        +  title: Foo
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  path: my/actions/foo
        """
    )
    assert pathlib.Path(".github", "workflows", "actions.py").exists()

    # add another one
    repo.file(
        "my/actions/bar/action.yml",
        """\
        name: Bar Action
        """,
    )
    main(["add", "my_bar=./my/actions/bar", "-v"])
    config.expect_diff(
        """\
        @@ -1,2 +1,3 @@
         uses:
           foo: ./my/actions/foo
        +  my_bar: ./my/actions/bar
        """
    )
    lock.expect_diff(
        """\
        @@ -9,3 +9,7 @@
             id: input2
             required: false
           path: my/actions/foo
        +- id: my_bar
        +  title: My bar
        +  inputs: []
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
        @@ -1,3 +1,4 @@
         uses:
           foo: ./my/actions/foo
           my_bar: ./my/actions/bar
        +  other: ./my/actions/foo
        """
    )
    lock.expect_diff(
        """\
        @@ -13,3 +13,13 @@
           title: My bar
           inputs: []
           path: my/actions/bar
        +- id: other
        +  title: Other
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  path: my/actions/foo
        """
    )

    # lock regenerated
    lock.path.unlink()
    main(["add", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # remove one
    main(["remove", "foo"])
    config.expect_diff(
        """\
        @@ -1,4 +1,3 @@
         uses:
        -  foo: ./my/actions/foo
           my_bar: ./my/actions/bar
           other: ./my/actions/foo
        """
    )
    lock.expect_diff(
        """\
        @@ -1,14 +1,4 @@
         actions:
        -- id: foo
        -  title: Foo
        -  inputs:
        -  - name: input1
        -    id: input1
        -    required: true
        -  - name: input2
        -    id: input2
        -    required: false
        -  path: my/actions/foo
         - id: my_bar
           title: My bar
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
        @@ -6,10 +6,7 @@
         - id: other
           title: Other
           inputs:
        -  - name: input1
        -    id: input1
        +  - name: another_input
        +    id: another-input
             required: true
        -  - name: input2
        -    id: input2
        -    required: false
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
        target,
        version,
        sha,
        contents,
        *,
        as_latest=False,
        as_branch=False,
        path=None,
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
    print("repo", repo.path)
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
        @@ -0,0 +1,2 @@
        +uses:
        +  repo: owner/repo@v2
        """
    )
    lock.expect_diff(
        """\
        @@ -0,0 +1,16 @@
        +actions:
        +- id: repo
        +  title: Repo
        +  inputs:
        +  - name: input1
        +    id: input1
        +    required: true
        +  - name: input2
        +    id: input2
        +    required: false
        +  owner: owner
        +  repo: repo
        +  path: ''
        +  ref: v2
        +  resolved-ref: v2
        +  sha: this_is_a_sha
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
        @@ -1,2 +1,3 @@
         uses:
           repo: owner/repo@v2
        +  foo: owner/foo
        """
    )
    lock.expect_diff(
        """\
        @@ -1,4 +1,15 @@
         actions:
        +- id: foo
        +  title: Foo
        +  inputs:
        +  - name: an_input
        +    id: an-input
        +    required: true
        +  owner: owner
        +  repo: foo
        +  path: ''
        +  resolved-ref: v3.2.1
        +  sha: another_sha
         - id: repo
           title: Repo
           inputs:
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
        @@ -1,3 +1,4 @@
         uses:
           repo: owner/repo@v2
           foo: owner/foo
        +  repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
        """
    )
    lock.expect_diff(
        """\
        @@ -25,3 +25,12 @@
           ref: v2
           resolved-ref: v2
           sha: this_is_a_sha
        +- id: repo_path_to_bar
        +  title: Repo path to bar
        +  inputs: []
        +  owner: owner
        +  repo: repo
        +  path: path/to/bar
        +  ref: v1.0.0
        +  resolved-ref: v1.0.0
        +  sha: bar_sha
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
        @@ -2,3 +2,4 @@
           repo: owner/repo@v2
           foo: owner/foo
           repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
        +  x: owner/bar@main
        """
    )
    lock.expect_diff(
        """\
        @@ -34,3 +34,12 @@
           ref: v1.0.0
           resolved-ref: v1.0.0
           sha: bar_sha
        +- id: x
        +  title: X
        +  inputs: []
        +  owner: owner
        +  repo: bar
        +  path: ''
        +  ref: main
        +  resolved-ref: main
        +  sha: branch_sha
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
        @@ -8,8 +8,8 @@
           owner: owner
           repo: foo
           path: ''
        -  resolved-ref: v3.2.1
        -  sha: another_sha
        +  resolved-ref: v3.3.3
        +  sha: updated_sha
         - id: repo
           title: Repo
           inputs:
        """
    )

    # remove one
    main(["remove", "repo_path_to_bar", "-v"])
    config.expect_diff(
        """\
        @@ -1,5 +1,4 @@
         uses:
           repo: owner/repo@v2
           foo: owner/foo
        -  repo_path_to_bar: owner/repo/path/to/bar@v1.0.0
           x: owner/bar@main
        """
    )
    lock.expect_diff(
        """\
        @@ -25,15 +25,6 @@
           ref: v2
           resolved-ref: v2
           sha: this_is_a_sha
        -- id: repo_path_to_bar
        -  title: Repo path to bar
        -  inputs: []
        -  owner: owner
        -  repo: repo
        -  path: path/to/bar
        -  ref: v1.0.0
        -  resolved-ref: v1.0.0
        -  sha: bar_sha
         - id: x
           title: X
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
        @@ -1,30 +1,21 @@
         actions:
         - id: foo
           title: Foo
        -  inputs:
        -  - name: an_input
        -    id: an-input
        -    required: true
        +  inputs: []
           owner: owner
           repo: foo
           path: ''
        -  resolved-ref: v3.3.3
        -  sha: updated_sha
        +  resolved-ref: v4.2.1
        +  sha: updated_sha_foo
         - id: repo
           title: Repo
        -  inputs:
        -  - name: input1
        -    id: input1
        -    required: true
        -  - name: input2
        -    id: input2
        -    required: false
        +  inputs: []
           owner: owner
           repo: repo
           path: ''
           ref: v2
           resolved-ref: v2
        -  sha: this_is_a_sha
        +  sha: updated_sha_repo
         - id: x
           title: X
           inputs: []
        @@ -33,4 +24,4 @@
           path: ''
           ref: main
           resolved-ref: main
        -  sha: branch_sha
        +  sha: updated_sha_bar
        """
    )
