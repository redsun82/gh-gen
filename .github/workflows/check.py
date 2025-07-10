from ghgen.ctx import *
from actions import *


@workflow
def check():
    on.pull_request().push()
    checkout()
    setup_uv()
    pre_commit().name("Check")
