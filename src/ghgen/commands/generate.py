import argparse
import importlib.util
import logging
import sys
import pathlib
import io

from ruamel.yaml import CommentedMap

from ..syntax import WorkflowInfo, GenerationError
from .utils import DiffError, yaml, write_or_diff
from .lock.sync import run as sync

aliases = ["g", "gen"]
help = "generate worklows"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the generate command."""
    parser.add_argument(
        "inputs",
        type=str,
        nargs="*",
        help="Input files or directories containing workflow definitions",
    )


def generate_workflow(
    w: WorkflowInfo, dir: pathlib.Path, check=False
) -> pathlib.Path | None:
    input = f"{w.file.name}::{w.spec.__name__}"
    output = (dir / w.id).with_suffix(".yml")
    w = w.worfklow.asdict()
    w = CommentedMap(w)
    w.yaml_set_start_comment(f"generated from {input}")
    out = io.StringIO()
    yaml.dump(w, out)
    diff = write_or_diff(output, out.getvalue(), check)
    if diff:
        raise DiffError(diff)
    return output


def generate_all(opts: argparse.Namespace) -> int:
    """Generate workflows from the discovered inputs, without syncing the lock file."""
    sys.path.extend(map(str, opts.includes))
    sys.modules["ghgen"] = sys.modules[__name__]
    inputs = getattr(opts, "inputs", None) or opts.includes
    failed = False
    found = False
    for i in inputs:
        logging.debug(f"@ {i}")
        for f in i.glob("*.py"):
            logging.debug(f"← {f}")
            spec = importlib.util.spec_from_file_location(f.name, str(f))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for k, v in mod.__dict__.items():
                if isinstance(v, WorkflowInfo):
                    found = True
                    try:
                        output = generate_workflow(
                            v, opts.output_directory, check=opts.check
                        )
                        logging.info(f"{'✅' if opts.check else '→'} {output}")
                    except (GenerationError, DiffError) as e:
                        failed = True
                        for error in e.errors:
                            logging.error(error)
    if not found:
        logging.error("no workflows found")
        return 2
    if failed:
        return 1
    return 0


def run(opts: argparse.Namespace):
    try:
        sync(opts)
        synced = True
    except DiffError as e:
        for error in e.errors:
            logging.error(error)
        synced = False
    return generate_all(opts) or (0 if synced else 1)
