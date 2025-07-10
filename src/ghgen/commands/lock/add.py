import argparse

from ..utils import config_file, dump
from .utils import Action, sync_lock_data

help = "add one or more action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the add command."""
    parser.add_argument(
        "actions",
        type=str,
        help="The action to add as a dependency (e.g., `actions/checkout@v2`)",
        nargs="*",
        metavar="[name=](owner/repo[/path][@ref] | ./path)",
    )
    parser.add_argument(
        "--title",
        type=str,
        help="The title to give the generated steps",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Assume yes to all questions",
    )


def _ask_yes_no(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    while True:
        answer = input(f"{question} (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        elif answer in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def _unversioned_spec_from_clause(clause: str | dict[str, str]) -> str:
    match clause:
        case (str() as uses) | {"uses": uses}:
            return uses.partition("@")[0]
        case _:
            raise ValueError(f"Invalid action clause: {clause}")


def run(args: argparse.Namespace):
    def ask(question: str) -> bool:
        return _ask_yes_no(question, args.yes)

    actions = list(map(Action.from_spec, args.actions))
    uses = args.config.yaml.setdefault("uses", {})
    for a in actions:
        clause = dict(uses=a.spec, title=args.title) if args.title else a.spec
        if a.name in uses:
            if uses[a.name] == clause:
                print(
                    f"{a.name} already exists in configuration with same settings, skipping"
                )
                continue
            if not ask(
                f"{a.name} already exists in configuration (as {uses[a.name]}). Overwrite?"
            ):
                print("...skipping")
                continue
        other_matching_specs = [
            (n, c)
            for n, c in uses.items()
            if n != a.name and _unversioned_spec_from_clause(c) == a.unversioned_spec
        ]
        if other_matching_specs and not ask(
            f"{a.unversioned_spec} is already present in configuration as "
            f"{', '.join(f'{n}={c}' for n, c in other_matching_specs)}. Add it anyway?"
        ):
            continue
        uses[a.name] = clause
    args.config.reload()
    sync_lock_data(args)
    dump(args.config.yaml, config_file())
