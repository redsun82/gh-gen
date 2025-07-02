import argparse
import logging
import typing
import pathlib
import colorlog
import functools
import subprocess

from .ctx import WorkflowInfo, GenerationError
from .commands import commands
from .commands.generate import run as generate
from .commands.utils import relativized_path


@functools.cache
def discover_workflows_dir() -> pathlib.Path:
    path = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
    return pathlib.Path(path, ".github", "workflows")


def options(args: typing.Sequence[str] = None):
    p = argparse.ArgumentParser(description="Generate Github Actions workflows")

    def common_opts(parser):
        parser.add_argument(
            "--output-directory",
            "-D",
            type=relativized_path,
            metavar="DIR",
            help="Where output files should be written (`.github/workflows` by default)",
        )
        parser.add_argument(
            "--include",
            "-I",
            type=relativized_path,
            metavar="DIR",
            action="append",
            dest="includes",
            help="Add DIR to the system include paths. Can be repeated. If none are provided `.github/workflows` is used. Includes are also used as default inputs.",
        )
        parser.add_argument("--verbose", "-v", action="store_true")
        parser.add_argument("--check", "-C", action="store_true")

    common_opts(p)
    p.set_defaults(command=generate, inputs=[])
    subcommands = p.add_subparsers()
    for command in commands:
        subparser = subcommands.add_parser(
            command.__name__,
            aliases=getattr(command, "aliases", None),
            help=command.help,
        )
        common_opts(subparser)
        subparser.set_defaults(command=command.run)
        command.add_arguments(subparser)
    ret = p.parse_args(args)
    ret.output_directory = ret.output_directory or discover_workflows_dir()
    ret.includes = ret.includes or [discover_workflows_dir()]
    return ret


def main(args: typing.Sequence[str] = None) -> int:
    opts = options(args)
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "{log_color}{levelname: <8}{reset} {message_log_color}{message}",
            secondary_log_colors={
                "message": {
                    "DEBUG": "white",
                    "WARNING": "bold",
                    "ERROR": "bold",
                    "CRITICAL": "bold",
                },
            },
            style="{",
        )
    )
    logging.basicConfig(
        level=logging.INFO if not opts.verbose else logging.DEBUG, handlers=[handler]
    )
    logging.debug(opts.__dict__)
    return opts.command(opts)
