# Copyright (C) 2025 David Němec
import logging
import os
import sys
from argparse import ArgumentParser, Namespace
from getpass import getpass
from pathlib import Path

from src.bitwarden_to_keepass import bitwarden_to_keepass

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s :: %(levelname)s :: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def check_args(args: Namespace) -> Namespace:
    if not args.database_password:
        if not sys.stdin.isatty():
            raise RuntimeError(
                "DATABASE_PASSWORD is not set and there is no interactive "
                "terminal available. Refusing to prompt: without a TTY the "
                "typed password would be echoed in clear text.",
            )
        args.database_password = getpass(
            "Enter the database password (will not display): ",
        )

    if not args.database_password:
        raise RuntimeError("A database password must be supplied.")

    if args.database_keyfile and (
        not Path(args.database_keyfile).is_file()
        or not os.access(
            args.database_keyfile,
            os.R_OK,
        )
    ):
        raise RuntimeError("Key File for KeePass database is not readable.")

    if not Path(args.bw_path).is_file() or not os.access(args.bw_path, os.X_OK):
        raise RuntimeError(
            "bitwarden-cli was not found or not executable. "
            "Did you set correct '--bw-path'?",
        )

    return args


def environ_or_required(key: str) -> dict:
    return (
        {"default": os.environ.get(key)} if os.environ.get(key) else {"required": True}
    )


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Export (most of) your Bitwarden items into a KeePass database.",
        epilog=(
            "SECURITY: the Bitwarden session (BW_SESSION) and the database "
            "password (DATABASE_PASSWORD or an interactive prompt) are never "
            "read from the command line, because argv is visible to other "
            "processes."
        ),
    )
    parser.add_argument(
        "--database-path",
        help="Path to the KeePass database (created if it does not exist).",
        **environ_or_required("DATABASE_PATH"),
    )
    parser.add_argument(
        "--database-keyfile",
        help="Path to Key File for KeePass database",
        default=os.environ.get("DATABASE_KEYFILE", None),
    )
    parser.add_argument(
        "--bw-path",
        help="Path for bw binary",
        default=os.environ.get("BW_PATH", "bw"),
    )
    return parser


parser = build_parser()
args = parser.parse_args()

# SECURITY: the session is exclusively an environment variable, never a
# command-line argument. Generate one with `bw unlock --raw` (or run the
# Docker entrypoint, which authenticates with the personal API key and unlocks
# with the BW_PASSWORD environment variable) and export it.
session = os.environ.get("BW_SESSION")
if not session:
    parser.error(
        "BW_SESSION is not set. Generate one with `bw unlock --raw` and export "
        "it, e.g. `export BW_SESSION=$(bw unlock --raw)`. The session is not "
        "accepted as a command-line argument because argv is visible to other "
        "processes.",
    )
args.bw_session = session
args.database_password = os.environ.get("DATABASE_PASSWORD")

try:
    check_args(args)
    bitwarden_to_keepass(args)
except Exception:
    logger.exception("Exception occurred.")
    sys.exit(1)
