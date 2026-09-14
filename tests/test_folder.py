# Copyright (C) 2025 David Němec
import tempfile
import unittest
from pathlib import Path

from pykeepass import create_database

from src.folder import Folder, load_folders, nested_traverse_insert


class FolderTreeTest(unittest.TestCase):
    def test_nested_traverse_insert_builds_hierarchy(self) -> None:
        root = Folder(None)
        folders = [
            ("1", ["a"]),
            ("2", ["a", "b"]),
            ("3", ["a", "b", "c"]),
            ("4", ["d"]),
        ]
        inserted = {folder_id: Folder(folder_id) for folder_id, _ in folders}
        for folder_id, name_parts in folders:
            nested_traverse_insert(root, name_parts, inserted[folder_id], "/")

        self.assertEqual(root.children, [inserted["1"], inserted["4"]])
        self.assertEqual(inserted["1"].children, [inserted["2"]])
        self.assertEqual(inserted["2"].children, [inserted["3"]])
        self.assertEqual(inserted["2"].name, "b")
        self.assertEqual(inserted["3"].name, "c")


class LoadFoldersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.kdbx"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_folders_creates_nested_groups(self) -> None:
        kp = create_database(str(self.db_path), password="test")
        groups = load_folders(
            kp,
            [
                {"id": "1", "name": "a"},
                {"id": "2", "name": "a/b"},
                {"id": "3", "name": "a/b/c"},
            ],
        )
        # NOTE: pykeepass `root_group` is a fresh object per access, so
        # compare by name rather than identity.
        self.assertEqual(groups[None].name, kp.root_group.name)
        self.assertEqual(groups["1"].name, "a")
        self.assertEqual(groups["2"].name, "b")
        self.assertEqual(groups["3"].name, "c")
        self.assertEqual(kp.root_group.subgroups[0].name, "a")
        self.assertEqual(kp.root_group.subgroups[0].subgroups[0].name, "b")
        self.assertEqual(kp.root_group.subgroups[0].subgroups[0].subgroups[0].name, "c")

    def test_load_folders_without_folders_maps_root(self) -> None:
        kp = create_database(str(self.db_path), password="test")
        groups = load_folders(kp, [])
        self.assertEqual(groups, {None: kp.root_group})


if __name__ == "__main__":
    unittest.main()
