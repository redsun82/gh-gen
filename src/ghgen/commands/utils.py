import pathlib


class DiffError(Exception):
    def __init__(self, diff):
        super().__init__("generated code does not match expected")
        self.errors = diff


def relativized_path(p: str | pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(p)
    if not p.is_absolute():
        return p
    cwd = pathlib.Path.cwd()
    try:
        return p.relative_to(cwd)
    except ValueError:
        return p
