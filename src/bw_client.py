# Copyright (C) 2025 David Němec
"""Thin adapter around the Bitwarden CLI (``bw``)."""

import json
import os
import subprocess


class BwClient:
    """Runs read-only ``bw`` commands.

    The session key is passed through the ``BW_SESSION`` environment
    variable instead of the command line, so it never shows up in
    process listings.
    """

    def __init__(self, bw_path: str, session: str) -> None:
        self._bw_path = bw_path
        self._env = {**os.environ, "BW_SESSION": session}

    def _run(self, *args: str) -> str:
        return subprocess.check_output(
            [self._bw_path, *args],
            env=self._env,
            encoding="utf8",
        )

    def _run_binary(self, *args: str) -> bytes:
        return subprocess.check_output([self._bw_path, *args], env=self._env)

    def list_folders(self) -> list[dict]:
        return json.loads(self._run("list", "folders"))

    def list_items(self) -> list[dict]:
        return json.loads(self._run("list", "items"))

    def get_attachment(self, item_id: str, attachment_id: str) -> bytes:
        return self._run_binary(
            "get",
            "attachment",
            attachment_id,
            "--raw",
            "--itemid",
            item_id,
        )
