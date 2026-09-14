# Copyright (C) 2025 David Němec
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from pykeepass import PyKeePass, create_database

from src.bitwarden_to_keepass import bitwarden_to_keepass
from src.item import CustomFieldType


class FakeBwClient:
    """Replaces the real BwClient for tests."""

    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def list_folders(self) -> list[dict]:
        return []

    def list_items(self) -> list[dict]:
        return self._items

    def get_attachment(self, item_id: str, attachment_id: str) -> bytes:
        message = (
            f"Unexpected attachment fetch: item={item_id} attachment={attachment_id}"
        )
        raise AssertionError(message)


def _login_item(name: str, item_id: str) -> dict:
    return {
        "id": item_id,
        "type": 1,
        "name": name,
        "folderId": None,
        "login": {
            "username": "user",
            "password": "pass",
            "uris": [{"uri": "https://example.com"}],
            "totp": "otpauth://totp/Example:user?secret=SECRET&period=30&digits=6",
        },
        "fields": [{"name": "note", "value": "value", "type": CustomFieldType.HIDDEN}],
        "attachments": [],
        "notes": "some notes",
    }


class BitwardenToKeePassTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.kdbx"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _new_db(self, titles: list[str] | None = None) -> None:
        kp = create_database(str(self.db_path), password="test")
        for title in titles or []:
            kp.add_entry(kp.root_group, title, "user", "pass")
        kp.save()

    def _run(self, items: list[dict]) -> None:
        bitwarden_to_keepass(
            Namespace(
                bw_path="bw",
                bw_session="session",
                database_path=str(self.db_path),
                database_password="test",
                database_keyfile=None,
            ),
            client=FakeBwClient(items),
        )

    def _reload(self) -> PyKeePass:
        return PyKeePass(str(self.db_path), password="test")

    def test_item_is_added_with_fields_and_totp(self) -> None:
        self._new_db()
        self._run([_login_item("Vault", "id1")])

        entry = self._reload().entries[0]
        self.assertEqual(entry.title, "Vault")
        self.assertEqual(entry.password, "pass")
        self.assertEqual(entry.get_custom_property("TOTP Seed"), "SECRET")
        self.assertEqual(entry.get_custom_property("note"), "value")

    def test_duplicate_title_is_suffixed_instead_of_hanging(self) -> None:
        self._new_db(titles=["Vault"])
        self._run([_login_item("Vault", "id1")])

        titles = sorted(e.title for e in self._reload().entries)
        self.assertEqual(titles, ["Vault", "Vault - (id1) [1]"])

    def test_repeated_exports_do_not_hang(self) -> None:
        # Regression: the old fallback title never changed, so a third run
        # with a title collision looped forever.
        self._new_db()
        for _ in range(3):
            self._run([_login_item("Vault", "id1")])

        titles = sorted(e.title for e in self._reload().entries)
        self.assertEqual(
            titles,
            ["Vault", "Vault - (id1) [1]", "Vault - (id1) [2]"],
        )

    def test_wrong_database_password_raises(self) -> None:
        self._new_db(titles=["Vault"])
        with self.assertRaises(RuntimeError):
            bitwarden_to_keepass(
                Namespace(
                    bw_path="bw",
                    bw_session="session",
                    database_path=str(self.db_path),
                    database_password="wrong",
                    database_keyfile=None,
                ),
                client=FakeBwClient([]),
            )

    def test_card_and_identity_items_are_skipped(self) -> None:
        self._new_db()
        self._run(
            [
                {"id": "c1", "type": 3, "name": "Visa", "folderId": None},
                {"id": "i1", "type": 4, "name": "ID", "folderId": None},
            ],
        )
        self.assertEqual(self._reload().entries, [])


if __name__ == "__main__":
    unittest.main()
