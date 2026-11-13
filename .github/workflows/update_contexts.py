from ghgen.syntax import *
from actions import checkout, setup_uv


@workflow
def update_contexts():
    on.event("schedule", cron="17 4 * * 1").workflow_dispatch()
    permissions(contents="write", pull_requests="write", actions="write")
    concurrency(group="context-updates", cancel_in_progress=False)
    checkout(fetch_depth="0")
    setup_uv()
    run("uv run python scripts/propose_context_update.py").env(GH_TOKEN=github.token)
