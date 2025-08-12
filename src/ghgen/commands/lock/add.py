import argparse

from ..utils import config_file, dump
from .utils import Action, sync_lock_data

help = "add one or more action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the add command."""
    parser.add_argument(
        "actions",
        type=str,
        help="The action(s) to add as a dependency (e.g., `actions/checkout@v2`)",
        nargs="+",
        metavar="[id=](owner/repo[/path][@ref] | ./path)",
    )
    parser.add_argument(
        "--name",
        type=str,
        help="The name to give to the generated steps. The action's own name is used by default",
    )
    parser.add_argument(
        "--pin",
        help="Pin the action to a specific commit "
        "(true by default unless the owner is listed in `trusted-owners` in the configuration)",
        action=argparse.BooleanOptionalAction,
        default=None,
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
        clause: dict[str, str | bool] | str = (
            dict(uses=a.spec) if args.name or not args.pin else a.spec
        )
        if args.name:
            clause["name"] = args.name
        if not args.pin:
            clause["pin"] = False
        if a.id in uses:
            if uses[a.id] == clause:
                print(
                    f"{a.id} already exists in configuration with same settings, skipping"
                )
                continue
            if not ask(
                f"{a.id} already exists in configuration (as {uses[a.id]}). Overwrite?"
            ):
                print("...skipping")
                continue
        other_matching_specs = [
            (n, c)
            for n, c in uses.items()
            if n != a.id and _unversioned_spec_from_clause(c) == a.unversioned_spec
        ]
        if other_matching_specs and not ask(
            f"{a.unversioned_spec} is already present in configuration as "
            f"{', '.join(f'{n}={c}' for n, c in other_matching_specs)}. Add it anyway?"
        ):
            continue
        uses[a.id] = clause
    args.config.reload()
    sync_lock_data(args)
    dump(args.config.yaml, config_file())
