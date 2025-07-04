import argparse
from .utils import Action, yaml

help = "add one or more action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the add command."""
    parser.add_argument(
        "actions",
        type=str,
        help="The action to add as a dependency (e.g., `actions/checkout@v2`)",
        nargs="+",
        metavar="[name=](owner/repo[/path][@ref] | ./path)",
    )


def run(args: argparse.Namespace):
    lock_file = args.output_directory / "ghgen.lock"
    actions = dict(map(Action.from_spec, args.actions))
    for a in actions.values():
        # TODO async
        a.fetch()
    yaml.dump(actions, lock_file)
