import argparse

from .utils import sync_lock_data
from ..utils import config_file, dump

help = "remove action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the remove command."""
    parser.add_argument(
        "actions",
        type=str,
        nargs="+",
        help="The action to remove as a dependency, by its assigned name.",
        metavar="name",
    )


def run(args: argparse.Namespace):
    actions = args.actions
    missing = [a for a in actions if a not in args.config.uses]
    if missing:
        raise ValueError(
            f"The following actions are not defined in the configuration: {', '.join(missing)}"
        )
    for a in actions:
        del args.config.yaml["uses"][a]
    args.config.reload()
    sync_lock_data(args)
    dump(args.config.yaml, config_file())
