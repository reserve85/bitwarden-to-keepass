# Copyright (C) 2025 David Němec
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from pykeepass import PyKeePass, create_database

from src.bitwarden_to_keepass import _redacted_item, bitwarden_to_keepass
from src.item import CustomFieldType


class FakeBwClient:
    """Replaces the real BwClient for tests."""

    def __init__(
        self,
        items: list[dict],
        attachment_payload: bytes = b"attachment-data",
        *,
        fail_attachment: bool = False,
    ) -> None:
        self._items = items
        self._attachment_payload = attachment_payload
        self._fail_attachment = fail_attachment

    def list_folders(self) -> list[dict]:
        return []

    def list_items(self) -> list[dict]:
        return self._items

    def get_attachment(self, item_id: str, attachment_id: str) -> bytes:
        if self._fail_attachment:
            message = f"boom: item={item_id} attachment={attachment_id}"
            raise RuntimeError(message)
        return self._attachment_payload


def _login_item(
    name: str,
    item_id: str,
    password: str = "pass",
    folder_id: str | None = None,
) -> dict:
    item = {
        "id": item_id,
        "type": 1,
        "name": name,
        "login": {
            "username": "user",
            "password": password,
            "uris": [{"uri": "https://example.com"}],
            "totp": "otpauth://totp/Example:user?secret=SECRET&period=30&digits=6",
        },
        "fields": [{"name": "note", "value": "value", "type": CustomFieldType.HIDDEN}],
        "attachments": [],
        "notes": "some notes",
    }
    if folder_id is not None:
        item["folderId"] = folder_id
    return item


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

    def _run(self, items: list[dict], client: FakeBwClient | None = None) -> None:
        bitwarden_to_keepass(
            Namespace(
                bw_path="bw",
                bw_session="session",
                database_path=str(self.db_path),
                database_password="test",
                database_keyfile=None,
            ),
            client=client or FakeBwClient(items),
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

    def test_repeated_exports_update_in_place(self) -> None:
        # Regression: the old export added a near-duplicate on every run, and
        # the fixed fallback title looped forever. The Bitwarden ID now makes
        # repeated exports update the existing entry instead.
        self._new_db()
        self._run([_login_item("Vault", "id1")])
        self._run([_login_item("Vault", "id1", password="newpass")])

        entries = self._reload().entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Vault")
        self.assertEqual(entries[0].password, "newpass")

    def test_partial_entry_is_rolled_back_on_attachment_failure(self) -> None:
        # Regression: a failing attachment used to leave a half-populated
        # (but still saved) entry behind, and retries accumulated duplicates.
        self._new_db()
        item = _login_item("Vault", "id1")
        item["attachments"] = [{"id": "a1", "fileName": "file.txt"}]
        client = FakeBwClient([item], fail_attachment=True)

        self._run([item], client=client)

        self.assertEqual(self._reload().entries, [])

    def test_unknown_folder_falls_back_to_root(self) -> None:
        # A stale folderId (folder deleted after list_folders) must not
        # silently drop the item.
        self._new_db()
        self._run([_login_item("Vault", "id1", folder_id="ghost-folder")])

        entry = self._reload().entries[0]
        self.assertEqual(entry.title, "Vault")
        self.assertTrue(entry.group.is_root_group)

    def test_update_removes_stale_custom_field(self) -> None:
        # Regression: a custom field deleted in Bitwarden used to stay in the
        # KeePass entry forever (rotated/revoked secrets were carried over).
        self._new_db()
        item = _login_item("Vault", "id1")
        item["fields"] = [
            {"name": "stale", "value": "old-secret", "type": CustomFieldType.HIDDEN},
        ]
        self._run([item])
        self.assertEqual(
            self._reload().entries[0].get_custom_property("stale"),
            "old-secret",
        )

        item = _login_item("Vault", "id1", password="newpass")
        item["fields"] = [
            {"name": "current", "value": "value", "type": CustomFieldType.TEXT},
        ]
        self._run([item])

        entry = self._reload().entries[0]
        self.assertIsNone(entry.get_custom_property("stale"))
        self.assertEqual(entry.get_custom_property("current"), "value")
        self.assertEqual(entry.password, "newpass")

    def test_attachments_are_not_duplicated_and_removed_when_deleted(self) -> None:
        # Regression: every export used to re-add and download each surviving
        # attachment (duplicate binaries, growing DB), and attachments deleted
        # in Bitwarden lingered in the KeePass entry.
        self._new_db()
        item = _login_item("Vault", "id1")
        item["attachments"] = [{"id": "a1", "fileName": "file.txt"}]
        self._run([item])
        self.assertEqual(len(self._reload().entries[0].attachments), 1)

        # A second export must not re-add the same attachment.
        self._run([item])
        self.assertEqual(len(self._reload().entries[0].attachments), 1)

        # Deleting the attachment in Bitwarden must remove it from the DB.
        item = _login_item("Vault", "id1")
        item["attachments"] = []
        self._run([item])
        self.assertEqual(self._reload().entries[0].attachments, [])

    def test_failed_update_is_rolled_back_to_previous_state(self) -> None:
        # Regression: a failing attachment used to leave a half-updated entry
        # behind (new password/fields saved) while the item was reported
        # "skipped"; the next run could not cleanly recover either.
        self._new_db()
        item = _login_item("Vault", "id1", password="pass")
        item["attachments"] = [{"id": "a1", "fileName": "file.txt"}]
        self._run([item])

        failing = _login_item("Vault", "id1", password="newpass")
        failing["attachments"] = [{"id": "b2", "fileName": "new.txt"}]
        self._run([failing], client=FakeBwClient([failing], fail_attachment=True))

        entry = self._reload().entries[0]
        self.assertEqual(entry.title, "Vault")
        self.assertEqual(entry.password, "pass")
        self.assertEqual(len(entry.attachments), 1)
        self.assertEqual(entry.attachments[0].filename, "file.txt")

    def test_item_without_type_is_skipped_not_fatal(self) -> None:
        # Regression: a single malformed item used to abort the whole export.
        self._new_db()
        self._run(
            [
                {"id": "bad", "name": "broken"},
                _login_item("Vault", "id1"),
            ],
        )
        entries = self._reload().entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Vault")

    def test_missing_parent_directory_is_created(self) -> None:
        db_path = Path(self._tmp.name) / "nested" / "dir" / "test.kdbx"
        bitwarden_to_keepass(
            Namespace(
                bw_path="bw",
                bw_session="session",
                database_path=str(db_path),
                database_password="test",
                database_keyfile=None,
            ),
            client=FakeBwClient([]),
        )
        self.assertTrue(db_path.is_file())

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


class RedactionTest(unittest.TestCase):
    def test_redacted_item_redacts_secrets_but_not_plain_text(self) -> None:
        item = {
            "id": "id1",
            "login": {
                "password": "hunter2",
                "totp": "otpauth://totp/x",
                "username": "u",
            },
            "notes": "recovery code: 1234-5678",
            "fields": [
                {"name": "note", "value": "visible", "type": CustomFieldType.TEXT},
                {"name": "API Key", "value": "sk-xyz", "type": CustomFieldType.TEXT},
                {"name": "hidden", "value": "secret", "type": CustomFieldType.HIDDEN},
                {"name": "future", "value": "future-secret", "type": 9},
            ],
        }

        redacted = _redacted_item(item)

        self.assertEqual(redacted["login"]["password"], "***")
        self.assertNotIn("totp", redacted["login"])
        self.assertEqual(redacted["notes"], "***")
        self.assertEqual(redacted["fields"][0]["value"], "visible")
        self.assertEqual(redacted["fields"][1]["value"], "***")
        self.assertEqual(redacted["fields"][2]["value"], "***")
        self.assertEqual(redacted["fields"][3]["value"], "***")
        # The source item is not modified.
        self.assertEqual(item["login"]["password"], "hunter2")
        self.assertEqual(item["notes"], "recovery code: 1234-5678")
        self.assertEqual(item["fields"][1]["value"], "sk-xyz")


if __name__ == "__main__":
    unittest.main()
