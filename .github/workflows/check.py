from ghgen.ctx import *


@workflow
def check():
    on.pull_request().push()
    uses("actions/checkout@v4")
    uses("astral-sh/setup-uv@v5")
    step("Check").uses("pre-commit/action@v3.0.1")
