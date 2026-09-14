# Copyright (C) 2025 David Němec
import tempfile
import unittest
from pathlib import Path

from pykeepass import create_database

from src.set_kp_entry_urls import (
    ANDROID_APP_PROPERTY,
    EXTRA_URL_PROPERTY_PREFIX,
    IOS_APP_PROPERTY_PREFIX,
    set_kp_entry_urls,
)


class SetKpEntryUrlsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.kdbx"
        self.kp = create_database(str(self.db_path), password="test")
        self.entry = self.kp.add_entry(self.kp.root_group, "title", "user", "pass")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_first_url_goes_to_url_attribute_and_rest_to_extra_props(self) -> None:
        set_kp_entry_urls(
            self.entry,
            ["https://example.com", "https://second.example.com"],
        )
        self.assertEqual(self.entry.url, "https://example.com")
        self.assertEqual(
            self.entry.get_custom_property(f"{EXTRA_URL_PROPERTY_PREFIX}1"),
            "https://second.example.com",
        )

    def test_android_and_ios_app_identifiers_are_stored(self) -> None:
        set_kp_entry_urls(
            self.entry,
            ["androidapp://com.example.android", "iosapp://com.example.ios"],
        )
        self.assertEqual(self.entry.url, None)
        self.assertEqual(
            self.entry.get_custom_property(ANDROID_APP_PROPERTY),
            "com.example.android",
        )
        self.assertEqual(
            self.entry.get_custom_property(f"{IOS_APP_PROPERTY_PREFIX}1"),
            "com.example.ios",
        )

    def test_reset_clears_previously_stored_urls(self) -> None:
        set_kp_entry_urls(
            self.entry,
            ["https://old.example.com", "https://second.example.com"],
        )
        set_kp_entry_urls(self.entry, ["https://new.example.com"], reset=True)

        self.assertEqual(self.entry.url, "https://new.example.com")
        self.assertEqual(
            self.entry.get_custom_property(f"{EXTRA_URL_PROPERTY_PREFIX}1"),
            None,
        )


if __name__ == "__main__":
    unittest.main()
