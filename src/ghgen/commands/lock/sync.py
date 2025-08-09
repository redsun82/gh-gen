import argparse
from .utils import sync_lock_data

help = "Synchronize the lock file with the current configuration"


def add_arguments(parser: argparse.ArgumentParser):
    pass


def run(args: argparse.Namespace):
    sync_lock_data(args)
