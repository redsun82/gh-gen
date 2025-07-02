import argparse
import importlib.util
import logging
import sys
import pathlib
import difflib

from ruamel.yaml import YAML, CommentedMap

from ..ctx import WorkflowInfo, GenerationError
from .utils import DiffError

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


yaml = YAML()
yaml.default_flow_style = False


def generate_workflow(
    w: WorkflowInfo, dir: pathlib.Path, check=False
) -> pathlib.Path | None:
    input = f"{w.file.name}::{w.spec.__name__}"
    output = (dir / w.id).with_suffix(".yml")
    tmp = output.with_suffix(".yml.tmp")
    w = w.worfklow.asdict()
    w = CommentedMap(w)
    w.yaml_set_start_comment(f"generated from {input}")
    with open(tmp, "w") as out:
        yaml.dump(w, out)
    if check:
        if output.exists():
            with open(output) as current:
                current = [*current]
        else:
            current = []
        with open(tmp) as new:
            new = [*new]
        diff = list(difflib.unified_diff(current, new, str(output), str(tmp)))
        if diff:
            raise DiffError([l.rstrip("\n") for l in diff])
        tmp.unlink()
    else:
        tmp.rename(output)
    return output


def run(opts: argparse.Namespace):
    sys.path.extend(map(str, opts.includes))
    sys.modules["ghgen"] = sys.modules[__name__]
    inputs = opts.inputs or opts.includes
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
