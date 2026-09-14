# Copyright (C) 2025 David Němec
"""Thin adapter around the Bitwarden CLI (``bw``)."""

import json
import os
import subprocess
from typing import cast

# Cap every ``bw`` invocation so a stalled CLI (unreachable server, hung
# daemon, network timeout) cannot block an export forever.
COMMAND_TIMEOUT_SECONDS = 600


class BwClient:
    """Runs read-only ``bw`` commands.

    The session key is passed through the ``BW_SESSION`` environment
    variable instead of the command line, so it never shows up in
    process listings.
    """

    def __init__(self, bw_path: str, session: str) -> None:
        self._bw_path = bw_path
        self._env = {**os.environ, "BW_SESSION": session}

    def _check_output(self, *args: str, binary: bool = False) -> str | bytes:
        kwargs: dict = {
            "env": self._env,
            "stderr": subprocess.PIPE,
            "timeout": COMMAND_TIMEOUT_SECONDS,
        }
        if not binary:
            kwargs["encoding"] = "utf8"
        try:
            return subprocess.check_output([self._bw_path, *args], **kwargs)
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf8", errors="replace")
            message = (
                f"bw command failed with exit code {e.returncode}: "
                f"{stderr or '(no error output)'}"
            )
            raise RuntimeError(message) from e
        except subprocess.TimeoutExpired as e:
            command = " ".join([self._bw_path, *args])
            message = (
                f"bw command timed out after {COMMAND_TIMEOUT_SECONDS}s: {command}"
            )
            raise RuntimeError(message) from e
        except FileNotFoundError as e:
            message = f"bw binary not found: {self._bw_path}"
            raise RuntimeError(message) from e

    def _run(self, *args: str) -> str:
        return cast("str", self._check_output(*args))

    def _run_binary(self, *args: str) -> bytes:
        return cast("bytes", self._check_output(*args, binary=True))

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
