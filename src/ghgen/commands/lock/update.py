import argparse

from .utils import sync_lock_data

help = "update action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the update command."""
    parser.add_argument(
        "actions",
        type=str,
        nargs="*",
        help="The action to update as a dependency, by its assigned name. "
        "If none is provided, all actions will be updated.",
        metavar="name",
    )


def run(args: argparse.Namespace):
    """Update action dependencies in the lock file."""

    actions = args.actions
    if actions:
        missing = [a for a in args.actions if a not in args.config.uses]
        if missing:
            raise ValueError(
                f"The following actions are not defined in the configuration: {', '.join(missing)}"
            )
    else:
        actions = list(args.config.uses)

    sync_lock_data(args, actions)
