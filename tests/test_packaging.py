"""Distribution checks for Windows Explorer's path-length failure."""

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("package_release", ROOT / "tools" / "package_release.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackagingTests(unittest.TestCase):
    def test_user_package_extracts_with_short_paths_and_runtime_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "FFHS_Archiver.zip"
            members = packager.build_package(ROOT, archive_path)
            self.assertLessEqual(max(map(len, members)), 140)
            self.assertTrue(all(name.startswith("FFHS_Archiver/") for name in members))
            self.assertFalse(any("esempio_archivio" in name or "/tests/" in name or "/tools/" in name
                                 or "__pycache__" in name or "credentials.json" in name for name in members))
            with ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                archive.extractall(Path(tmp) / "extracted")
            extracted = Path(tmp) / "extracted" / "FFHS_Archiver"
            for name in ("FFHS Moodle Archiver.pyw", "Avvia FFHS Moodle Archiver.bat",
                         "ffhs_archiver/__init__.py", "ffhs_archiver/gui.py", "LEGGIMI.md"):
                self.assertEqual((extracted / name).read_bytes(), (ROOT / name).read_bytes())

    def test_package_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.zip", Path(tmp) / "b.zip"
            packager.build_package(ROOT, a)
            packager.build_package(ROOT, b)
            self.assertEqual(hashlib.sha256(a.read_bytes()).digest(), hashlib.sha256(b.read_bytes()).digest())

    def test_obsolete_custom_extension_is_not_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            for path in ("ffhs_archiver/__init__.py", "LEGGIMI.md", "FFHS Moodle Archiver.pyw",
                         "Avvia FFHS Moodle Archiver.bat", "browser_helper/manifest.json",
                         "browser_helper/popup.js", "browser_helper/tests/test.js",
                         "browser_helper/__pycache__/test.pyc"):
                file = root / path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("fixture")
            members = packager.build_package(root, Path(tmp) / "dist.zip")
            self.assertFalse(any("browser_helper" in name for name in members))
            self.assertFalse(any("/tests/" in name or "__pycache__" in name for name in members))

    def test_historical_example_is_preserved_without_long_checkout_paths(self):
        example = ROOT / "docs" / "esempio_archivio"
        with ZipFile(example / "esempio_v02.zip") as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 74)
            self.assertIn("index.md", archive.namelist())
        self.assertEqual({p.name for p in example.iterdir()}, {"esempio_v02.zip", "LEGGIMI_ESEMPIO.md"})


if __name__ == "__main__":
    unittest.main()
