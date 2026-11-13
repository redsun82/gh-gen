from ghgen.syntax import *
from actions import *


@workflow
def check():
    on.pull_request().push(branches=["main"]).workflow_dispatch()
    checkout()
    setup_uv()
    run("uv run python scripts/update_contexts.py --check").name(
        "Check generated context definitions"
    )
    pre_commit().id("Check")
    run("""
        gh extension install github/gh-actions-lock --pin v0.4.1
        gh actions-lock --verify
    """).name("Verify actions.lock").env(GH_TOKEN=github.token)
