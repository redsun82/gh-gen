import textwrap


def test_trial(repo):
    file = repo.file(
        "test.txt",
        """\
        This is a test file.
        with lines.
        """,
    )
    file.path.write_text(
        textwrap.dedent(
            """\
            This is a test file.
            changed.
            with lines.
            and more lines.
            """
        )
    )
    file.expect_diff(
        """\
        @@ -1,2 +1,4 @@
         This is a test file.
        +changed.
         with lines.
        +and more lines.
        """
    )
