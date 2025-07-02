import argparse
import dataclasses
from dataclasses import dataclass
import pathlib

help = "add one or more action dependencies"


def add_arguments(parser: argparse.ArgumentParser):
    """Add command-line options for the add command."""
    parser.add_argument(
        "actions",
        type=str,
        help="The action to add as a dependency (e.g., `actions/checkout@v2`)",
        nargs="+",
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        help="Optional name for the action, defaults to the action name",
    )
