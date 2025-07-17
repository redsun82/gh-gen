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


@pytest.fixture
def mock_gh_api_call(monkeypatch):
    def f(target, version, sha, contents, *, as_latest=False, as_branch=False):
        def mock_subprocess_popen(cmd, *, text, stdout, stderr=None):
            assert text is True
            assert stdout is subprocess.PIPE
            ret = mock.MagicMock()
            ret.__enter__.return_value = ret
            ret.returncode = 0
            print("PROUT", cmd)
            match cmd:
                case [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github+json",
                    address,
                    "--jq",
                    ".tag_name",
                ] if (
                    as_latest and address == f"repos/{target}/releases/latest"
                ):
                    ret.stdout.read.return_value = f"{version}\n"
                case [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github.v3.raw",
                    address,
                ] if (
                    address == f"repos/{target}/contents/action.yml?ref={version}"
                ):
                    ret.stdout = io.StringIO(textwrap.dedent(contents))
                case [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github+json",
                    address,
                    "--jq",
                    ".object.sha",
                ] if (
                    address == f"repos/{target}/git/ref/tags/{version}"
                ):
                    if as_branch:
                        raise subprocess.CalledProcessError
                    else:
                        ret.stdout.read.return_value = f"{sha}\n"
                case [
                    "gh",
                    "api",
                    "-H",
                    "Accept: application/vnd.github+json",
                    address,
                    "--jq",
                    ".object.sha",
                ] if (
                    as_branch and address == f"repos/{target}/git/ref/heads/{version}"
                ):
                    ret.stdout.read.return_value = f"{sha}\n"
                case _:
                    assert False, f"unexpected command: {cmd}"
            return ret

        monkeypatch.setattr("subprocess.Popen", mock_subprocess_popen)

    return f


def test_remote(repo, mock_gh_api_call):
    config = repo.config()
    print("repo", repo.path)
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
