import argparse
import logging
import typing
import pathlib
import colorlog

from .ctx import WorkflowInfo, GenerationError
from .commands import commands
from .commands.generate import run as generate
from .commands.utils import relativized_path, project_dir, load, config_file
from .commands.config import Config


def discover_workflows_dir() -> pathlib.Path:
    return project_dir() / ".github" / "workflows"


def options(args: typing.Sequence[str] = None):
    p = argparse.ArgumentParser(description="Generate Github Actions workflows")
    config = load(Config, config_file())

    def common_opts(parser):
        parser.add_argument(
            "--output-directory",
            "-D",
            type=relativized_path,
            metavar="DIR",
            default=config.output_directory
            and relativized_path(config.output_directory),
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
            default=config.includes or [],
        )
        parser.add_argument("--verbose", "-v", action="store_true")
        parser.add_argument("--check", "-C", action="store_true")

    common_opts(p)
    p.set_defaults(command=generate, inputs=[], config=config)
    subcommands = p.add_subparsers()
    for command in commands:
        _, _, name = command.__name__.rpartition(".")
        subparser = subcommands.add_parser(
            name,
            aliases=getattr(command, "aliases", ()),
            help=command.help,
        )
        common_opts(subparser)
        subparser.set_defaults(command=command.run)
        command.add_arguments(subparser)
    ret = p.parse_args(args)
    ret.output_directory = ret.output_directory or discover_workflows_dir()
    ret.includes = ret.includes or [discover_workflows_dir()]
    return ret


class LogFormatter(colorlog.ColoredFormatter):
    def __init__(self):
        super().__init__(
            "{log_color}{levelname}{reset}{message_log_color}: {message}",
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

    def format(self, record):
        if record.levelno == logging.INFO:
            return record.getMessage()
        return super().format(record)


def main(args: typing.Sequence[str] = None) -> int:
    opts = options(args)
    handler = colorlog.StreamHandler()
    handler.setFormatter(LogFormatter())
    logging.basicConfig(
        level=logging.INFO if not opts.verbose else logging.DEBUG, handlers=[handler]
    )
    logging.debug(opts.__dict__)
    try:
        return opts.command(opts)
    except Exception as e:
        logging.exception(e, exc_info=opts.verbose)
