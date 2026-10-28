import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from ffhs_archiver.study_bundle import prepare_bundle

class StudyBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'downloads'
        self.source.mkdir()
        self.dest = self.root/'ready'
    def test_portable_files_deduplicated_sources_preserved_and_warnings_honest(self):
        (self.source/'one.pdf').write_bytes(b'PDF original')
        (self.source/'duplicate.pdf').write_bytes(b'PDF original')
        (self.source/'lecture.mp4').write_bytes(b'video')
        (self.source/'settings.json').write_text('private config')
        result = prepare_bundle(self.source,self.dest,moodle_text='[Page](local.md) [Moodle](https://moodle.ffhs.ch/)')
        self.assertEqual(result['files'],2)
        self.assertEqual(result['duplicates'],1)
        self.assertEqual(len(result['warnings']),1)
        self.assertEqual(result['skipped'],['settings.json'])
        self.assertEqual((self.source/'one.pdf').read_bytes(), b'PDF original')
        self.assertNotIn('local.md',(self.dest/'TESTI_MOODLE.md').read_text())
        with ZipFile(result['zip']) as z:
            self.assertIsNone(z.testzip())
            for file in self.dest.rglob('*'):
                if file.is_file(): self.assertEqual(z.read(file.relative_to(self.dest).as_posix()),file.read_bytes())
    def test_update_and_failure_restore_previous_package(self):
        (self.source/'a.txt').write_text('first')
        result = prepare_bundle(self.source,self.dest)
        old_zip=result['zip'].read_bytes()
        (self.source/'a.txt').write_text('second')
        import os
        original=os.rename
        def fail(a,b):
            if str(a).endswith('bundle.zip'): raise OSError('disk full')
            return original(a,b)
        with patch('ffhs_archiver.study_bundle.os.rename',side_effect=fail):
            with self.assertRaises(OSError):prepare_bundle(self.source,self.dest)
        self.assertEqual(result['zip'].read_bytes(),old_zip)
        self.assertEqual(next((self.dest/'MATERIALI').iterdir()).read_text(),'first')
        prepare_bundle(self.source,self.dest)
        self.assertEqual(next((self.dest/'MATERIALI').iterdir()).read_text(),'second')
    def test_refuses_empty_nested_and_unowned_destinations(self):
        with self.assertRaises(ValueError):prepare_bundle(self.source,self.dest)
        (self.source/'a.pdf').write_bytes(b'pdf')
        with self.assertRaises(ValueError):prepare_bundle(self.source,self.source/'out')
        self.dest.mkdir()
        (self.dest/'keep.txt').write_text('mine')
        with self.assertRaises(ValueError):prepare_bundle(self.source,self.dest)
        self.assertEqual((self.dest/'keep.txt').read_text(),'mine')
    def test_symlink_never_copies_outside_materials(self):
        private=self.root/'private.pdf';private.write_bytes(b'private')
        (self.source/'link.pdf').symlink_to(private)
        with self.assertRaises(ValueError):prepare_bundle(self.source,self.dest)
        self.assertFalse(self.dest.exists())
