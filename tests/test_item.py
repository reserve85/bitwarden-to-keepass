# Copyright (C) 2025 David Němec
import unittest

from src.item import CustomFieldType, Item


class ItemTest(unittest.TestCase):
    def _login_item(self, **login: object) -> Item:
        return Item({"id": "id1", "login": login})

    def test_get_uris_returns_normalized_strings_without_mutation(self) -> None:
        item = self._login_item(uris=[{"uri": "https://example.com"}, {"uri": None}])
        self.assertEqual(item.get_uris(), ["https://example.com", ""])
        # The source item must not be modified by the getter.
        self.assertIsNone(item.item["login"]["uris"][1]["uri"])

    def test_get_uris_without_login_returns_empty(self) -> None:
        self.assertEqual(Item({"id": "1"}).get_uris(), [])

    def test_get_custom_fields_does_not_mutate_source(self) -> None:
        # Custom fields live at the top level of a Bitwarden item.
        item = Item({"id": "id1", "fields": [{"name": None, "value": None, "type": 0}]})
        fields = item.get_custom_fields()
        self.assertEqual(
            fields,
            [{"name": "", "value": "", "type": CustomFieldType.TEXT}],
        )
        self.assertIsNone(item.item["fields"][0]["name"])

    def test_get_totp_parses_query_params(self) -> None:
        totp = "otpauth://totp/Example:user?secret=ABCDEFGHIJ&period=60&digits=8"
        item = self._login_item(totp=totp)
        self.assertEqual(item.get_totp(), ("ABCDEFGHIJ", "60;8"))

    def test_get_totp_without_totp_returns_none(self) -> None:
        self.assertEqual(self._login_item().get_totp(), (None, None))

    def test_get_username_and_password_fallbacks(self) -> None:
        self.assertEqual(self._login_item().get_username(), "")
        self.assertEqual(self._login_item().get_password(), "")
        self.assertEqual(Item({"id": "1"}).get_username(), "")


if __name__ == "__main__":
    unittest.main()
