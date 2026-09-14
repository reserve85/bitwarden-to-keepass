# Copyright (C) 2025 David Němec
import subprocess
import unittest
from unittest.mock import patch

from src.bw_client import BwClient


class BwClientTest(unittest.TestCase):
    def _capture(self, payload: object) -> tuple[dict, object]:
        calls: dict = {}

        def fake_check_output(command: list[str], **kwargs: object) -> object:
            calls["command"] = command
            calls["env"] = kwargs.get("env")
            return payload

        return calls, fake_check_output

    def test_session_is_passed_via_environment_not_arguments(self) -> None:
        calls, fake = self._capture("[]")
        with patch("src.bw_client.subprocess.check_output", fake):
            folders = BwClient("/usr/local/bin/bw", "super-secret").list_folders()
        self.assertEqual(calls["command"], ["/usr/local/bin/bw", "list", "folders"])
        self.assertEqual(calls["env"]["BW_SESSION"], "super-secret")
        self.assertNotIn("--session", calls["command"])
        self.assertEqual(folders, [])

    def test_list_items_parses_json(self) -> None:
        calls, fake = self._capture('[{"id": "id1"}]')
        with patch("src.bw_client.subprocess.check_output", fake):
            items = BwClient("bw", "session").list_items()
        self.assertEqual(calls["command"], ["bw", "list", "items"])
        self.assertEqual(items, [{"id": "id1"}])

    def test_get_attachment_uses_raw_binary_output(self) -> None:
        calls, fake = self._capture(b"binary-data")
        with patch("src.bw_client.subprocess.check_output", fake):
            data = BwClient("bw", "session").get_attachment("item1", "att1")
        self.assertEqual(
            calls["command"],
            ["bw", "get", "attachment", "att1", "--raw", "--itemid", "item1"],
        )
        self.assertEqual(data, b"binary-data")

    def test_subprocess_failure_raises_runtime_error_with_stderr(self) -> None:
        def fake_check_output(command: list[str], **_kwargs: object) -> str:
            raise subprocess.CalledProcessError(
                1,
                command,
                output=b"",
                stderr=b"Not logged in.",
            )

        with (
            patch("src.bw_client.subprocess.check_output", fake_check_output),
            self.assertRaisesRegex(RuntimeError, "Not logged in."),
        ):
            BwClient("bw", "session").list_folders()

    def test_subprocess_timeout_raises_runtime_error(self) -> None:
        def fake_check_output(command: list[str], **_kwargs: object) -> str:
            raise subprocess.TimeoutExpired(command, timeout=600)

        with (
            patch("src.bw_client.subprocess.check_output", fake_check_output),
            self.assertRaisesRegex(RuntimeError, "timed out"),
        ):
            BwClient("bw", "session").list_folders()

    def test_missing_binary_raises_runtime_error(self) -> None:
        def fake_check_output(_command: list[str], **_kwargs: object) -> str:
            raise FileNotFoundError

        with (
            patch("src.bw_client.subprocess.check_output", fake_check_output),
            self.assertRaisesRegex(RuntimeError, "bw binary not found"),
        ):
            BwClient("bw", "session").list_folders()


if __name__ == "__main__":
    unittest.main()
