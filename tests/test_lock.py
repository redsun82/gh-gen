import pathlib
import textwrap

import pytest
from src.ghgen import main


def test_add_local(repo, monkeypatch):
    config = repo.config()
    lock = repo.lock()
    repo.file(
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
    bar_action = repo.file(
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
    main(["add", "my_bar=./my/actions/foo", "-v"])
    config.expect_unchanged()
    lock.expect_unchanged()

    # accept same name overwrite
    monkeypatch.setattr("builtins.input", lambda _: "y")
    main(["add", "my_bar=./my/actions/foo", "-v"])
    config.expect_diff(
        """\
        @@ -1,3 +1,3 @@
         uses:
           foo: ./my/actions/foo
        -  my_bar: ./my/actions/bar
        +  my_bar: ./my/actions/foo
        """
    )
    lock.expect_diff(
        """\
        @@ -11,5 +11,11 @@
           path: my/actions/foo
         - id: my_bar
           title: My bar
        -  inputs: []
        -  path: my/actions/bar
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
        @@ -1,3 +1,2 @@
         uses:
        -  foo: ./my/actions/foo
           my_bar: ./my/actions/foo
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
           inputs:
        """
    )

    # update TODO
    # bar_action.path.write_text(
    #     textwrap.dedent(
    #         """\
    #         name: Bar Changed
    #         inputs:
    #             input1:
    #                 description: Input 1
    #                 required: true
    #         """
    # ))
    # main(["update", "-v"])
    # config.expect_unchanged()
    # lock.expect_diff()
