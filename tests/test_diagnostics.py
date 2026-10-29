"""Diagnóstico é uma coleta de pistas, sem copiar conteúdo nem inferir PID."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from imvu_obj.diagnostics import collect_diagnostics, save_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'HttpCache'
        self.cache.mkdir()

    def put(self, name, data):
        path = self.cache / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def collect(self):
        return collect_diagnostics(self.cache, '74667074')

    def test_mesh_magic_offsets_and_hash_are_evidence_not_pid_association(self):
        data = b'CMF\0' + bytes(range(64))
        self.put('hash', data)
        self.put('wrapped', b'wrapper' + data)
        report = self.collect()
        meshes = {m['path']: m for m in report['mesh_candidates']}
        self.assertEqual(meshes['hash']['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(meshes['wrapped']['cmf_offsets'], [7])
        self.assertNotIn('sha256', meshes['wrapped'])
        self.assertEqual(report['pid_mentions'], [])
        self.assertEqual(report['types'], {'CMF': 1, 'unknown': 1})

    def test_ascii_and_utf16_pid_offsets_do_not_copy_sensitive_content(self):
        self.put('metadata', b'TOP_SECRET_COOKIE\n' + b'74667074' + b' end ' + '74667074'.encode('utf-16le'))
        report = self.collect()
        mention = report['pid_mentions'][0]
        self.assertEqual(mention['ascii_offsets'], [18])
        self.assertTrue(mention['utf16le_offsets'])
        self.assertFalse(mention['association_confirmed'])
        self.assertNotIn('TOP_SECRET_COOKIE', json.dumps(report))
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_pid_substring_inside_another_number_does_not_match(self):
        self.put('no_match', b'174667074 746670749 ' + '174667074 746670749'.encode('utf-16le'))
        self.assertEqual(self.collect()['pid_mentions'], [])

    def test_sqlite_is_only_identified_not_searched_or_opened_as_database(self):
        self.put('index.db', b'SQLite format 3\0' + b'74667074 PRIVATE_DATABASE_ROWS')
        report = self.collect()
        self.assertEqual(report['probe_bytes'], 16)
        self.assertEqual(report['possible_indexes'][0]['path'], 'index.db')
        self.assertEqual(report['pid_mentions'], [])
        self.assertFalse(report['complete_content_search'])
        self.assertEqual(report['limits']['sqlite_signature_only_files'], 1)

    def test_limits_are_reported_without_claiming_absence(self):
        for i in range(3):
            self.put(f'file{i}', b'x' * 40)
        with patch('imvu_obj.diagnostics.PROBE_BYTES', 16), patch('imvu_obj.diagnostics.TOTAL_PROBE_BYTES', 20), patch('imvu_obj.diagnostics.MAX_ITEMS', 1):
            report = self.collect()
        self.assertEqual(report['probe_bytes'], 20)
        self.assertTrue(report['limits']['probe_budget_exhausted'])
        self.assertTrue(report['limits']['lists_truncated']['file_samples'])
        self.assertFalse(report['complete_content_search'])
        with patch('imvu_obj.diagnostics.MAX_FILES', 1):
            report = self.collect()
        self.assertFalse(report['complete_enumeration'])
        self.assertTrue(report['limits']['file_limit_reached'])

    def test_save_is_outside_cache_and_preserves_originals_and_existing_report(self):
        original = b'74667074 DO_NOT_COPY'
        source = self.put('metadata', original)
        output = self.root / 'diagnostico.json'
        self.assertEqual(save_diagnostics(self.cache, '74667074', output), output)
        self.assertEqual(source.read_bytes(), original)
        saved = output.read_bytes()
        self.assertNotIn(b'DO_NOT_COPY', saved)
        with self.assertRaises(FileExistsError):
            save_diagnostics(self.cache, '74667074', output)
        self.assertEqual(output.read_bytes(), saved)
        with self.assertRaises(ValueError):
            save_diagnostics(self.cache, '74667074', self.cache / 'new.json')
        self.assertFalse((self.cache / 'new.json').exists())

    def test_symlinks_are_not_traversed_and_cannot_redirect_output_into_cache(self):
        external = self.root / 'external'
        external.mkdir()
        (external / 'metadata').write_bytes(b'74667074')
        try:
            (self.cache / 'link').symlink_to(external, target_is_directory=True)
            (self.root / 'output_link').symlink_to(self.cache, target_is_directory=True)
        except OSError:
            self.skipTest('Criação de symlink indisponível neste sistema.')
        self.assertEqual(self.collect()['scanned_files'], 0)
        with self.assertRaises(ValueError):
            save_diagnostics(self.cache, '74667074', self.root / 'output_link' / 'report.json')


if __name__ == '__main__':
    unittest.main()
