"""Regressões de associação conservadora, ZIP e exportação do cache."""

import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from imvu_obj.cache import associate, export_product, scan_cache
from imvu_obj.converter import convert_file


def triangle(offset=0):
    data = bytearray(b'CMF\0' + struct.pack('<2i', 919, 1))
    data += struct.pack('<7i', 0, 3, 1, 0, 0, 1, 0)
    for x, y, uv in [(0, 0, (0, 0)), (1, 0, (1, 0)), (0, 1, (0, 1))]:
        data += struct.pack('<9f2i2fi', x + offset, y, 0, 0, 0, 1,
                            1, 1, 1, -1, 0, *uv, 0)
    data += struct.pack('<3i', 0, 1, 2)
    return bytes(data)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'IMVU'
        self.cache.mkdir()
        self.index = self.root / 'settings' / 'index.json'
        self.output = self.root / 'out'

    def put(self, relative, data=None):
        path = self.cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(triangle() if data is None else data)
        return path

    def scan(self, pid='74667074'):
        return scan_cache(self.cache, pid, self.index)

    def test_hash_filename_without_index_never_guesses_requested_pid(self):
        path = self.put('Cache/8f2039dc239bb35f8bba9f905f62fc03')
        report = self.scan()
        self.assertEqual(report.matches, [])
        self.assertEqual(len(report.unassigned), 1)
        self.assertEqual(report.unassigned[0].source, path)
        self.assertIsNone(report.unassigned[0].pid)
        self.assertEqual(report.scanned_files, 1)

    def test_pid_directory_and_filename_map_only_exact_id(self):
        path = self.put('products/product_74667074/blob')
        self.put('products/174667074/not_the_pid', triangle(2))
        self.put('74667074.cmf', triangle(3))
        matches = self.scan().matches
        self.assertEqual(len(matches), 2)
        self.assertIn(path, [m.source for m in matches])
        self.assertTrue(all(m.pid == '74667074' for m in matches))

    def test_conflicting_pid_path_remains_unassigned(self):
        self.put('product_74667074/999/mesh.cmf')
        report = self.scan()
        self.assertEqual(report.matches, [])
        self.assertEqual(len(report.unassigned), 1)
        self.assertIn('conflitantes', report.unassigned[0].evidence)

    def test_register_hash_association_survives_filename_change(self):
        path = self.put('Cache/hash_only')
        associate(path, '74667074', self.index)
        renamed = path.with_name('totally_different_name')
        path.rename(renamed)
        matches = self.scan().matches
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].source, renamed)
        self.assertIn('cadastrado', matches[0].evidence)
        content = json.loads(self.index.read_text())
        self.assertEqual(content['sha256_to_pid'][hashlib.sha256(triangle()).hexdigest()], '74667074')

    def test_conflicting_registration_preserves_existing_index(self):
        path = self.put('blob')
        associate(path, '74667074', self.index)
        before = self.index.read_bytes()
        with self.assertRaises(ValueError):
            associate(path, '999', self.index)
        self.assertEqual(self.index.read_bytes(), before)

    def test_explicit_hash_index_prevails_over_different_directory_label(self):
        path = self.put('999/blob')
        associate(path, '74667074', self.index)
        report = self.scan()
        self.assertEqual(len(report.matches), 1)
        self.assertEqual(report.matches[0].pid, '74667074')
        self.assertTrue(any('diverge' in warning for warning in report.warnings))

    def test_zip_mesh_and_traversal_members_never_extract_into_filesystem(self):
        archive = self.cache / 'bundle'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
            stream.writestr('74667074/mesh.cmf', triangle())
            stream.writestr('../../outside.cmf', triangle(3))
            stream.writestr('image.txt', 'not a mesh')
        report = self.scan()
        self.assertEqual(len(report.matches), 1)
        self.assertEqual(len(report.unassigned), 1)
        self.assertEqual(report.matches[0].member, '74667074/mesh.cmf')
        outputs = convert_file(archive, self.output)
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(path.parent == self.output for path in outputs))
        self.assertFalse((self.root / 'outside.cmf').exists())
        self.assertFalse((self.cache / '74667074').exists())

    def test_export_without_match_reports_unassigned_instead_of_exporting(self):
        self.put('hash')
        with self.assertRaisesRegex(ValueError, 'sem PID'):
            export_product(self.cache, '74667074', self.output, index_path=self.index)
        self.assertFalse(self.output.exists())

    def test_export_deduplicates_identical_content_and_keeps_cache_unchanged(self):
        first = self.put('74667074/first')
        second = self.put('74667074/second')
        outputs = export_product(self.cache, '74667074', self.output, index_path=self.index)
        self.assertEqual(len(outputs), 1)
        obj = outputs[0]
        self.assertEqual(sum(line.startswith('f ') for line in obj.read_text().splitlines()), 1)
        self.assertEqual(first.read_bytes(), triangle())
        self.assertEqual(second.read_bytes(), triangle())
        report = json.loads(obj.with_suffix('.json').read_text())
        self.assertEqual(report['pid'], '74667074')
        self.assertEqual(report['vertices'], 3)
        self.assertEqual(report['triangles'], 1)

    def test_invalid_index_is_reported_and_not_replaced(self):
        self.index.parent.mkdir()
        self.index.write_text('{ broken')
        before = self.index.read_bytes()
        with self.assertRaisesRegex(ValueError, 'inválido'):
            self.scan()
        self.assertEqual(self.index.read_bytes(), before)

    def test_index_numeric_or_null_pid_is_rejected_without_modification(self):
        self.index.parent.mkdir()
        sha = hashlib.sha256(triangle()).hexdigest()
        for invalid_pid in (74667074, None):
            with self.subTest(pid=invalid_pid):
                self.index.write_text(json.dumps({
                    'version': 1, 'sha256_to_pid': {sha: invalid_pid},
                }))
                before = self.index.read_bytes()
                with self.assertRaisesRegex(ValueError, 'string numérica'):
                    self.scan()
                self.assertEqual(self.index.read_bytes(), before)

    def test_unknown_file_and_broken_zip_do_not_prevent_other_matches(self):
        self.put('unknown', b'hello')
        self.put('broken.zip', b'PK\x03\x04broken')
        self.put('74667074/good')
        report = self.scan()
        self.assertEqual(len(report.matches), 1)
        self.assertTrue(any('broken.zip' in warning for warning in report.warnings))

    def test_corrupt_deflate_stream_is_warning_and_scan_continues(self):
        archive = self.cache / 'corrupt-deflate.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
            stream.writestr('mesh.cmf', triangle())
        content = bytearray(archive.read_bytes())
        name_length, extra_length = struct.unpack_from('<HH', content, 26)
        content[30 + name_length + extra_length] = 0xff  # reserved deflate block type
        archive.write_bytes(content)
        self.put('74667074/good')
        report = self.scan()
        self.assertEqual(len(report.matches), 1)
        self.assertTrue(any('corrupt-deflate.zip' in warning for warning in report.warnings))

    @unittest.skipUnless(os.environ.get('IMVU_SAMPLE_PATH'), 'Amostra real não fornecida em IMVU_SAMPLE_PATH')
    def test_known_sample_maps_by_content_without_filename_or_index(self):
        data = Path(os.environ['IMVU_SAMPLE_PATH']).read_bytes()
        path = self.put('Cache/renamed-file', data)
        report = self.scan()
        self.assertEqual(len(report.matches), 1)
        self.assertEqual(report.matches[0].source, path)
        self.assertIn('amostra', report.matches[0].evidence)
        self.assertEqual(report.matches[0].pid, '74667074')


if __name__ == '__main__':
    unittest.main()
