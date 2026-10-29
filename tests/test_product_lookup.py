"""Regressões do layout HttpCache observado: product<PID>_<hash32>."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from imvu_obj.__main__ import main
from imvu_obj.cache import export_product, scan_cache
from imvu_obj.cal3d import MeshError
from imvu_obj.converter import convert_file


PID = '74667074'
HASH = '1508083db59c9bb708c5bd532bb5b85f'
PRODUCT_NAME = f'product{PID}_{HASH}'


def triangle_cmf():
    """Fixture Cal3D independente do leitor, sem associação pré-cadastrada."""
    data = bytearray(b'CMF\0' + struct.pack('<2i', 919, 1))
    data += struct.pack('<7i', 3, 3, 1, 0, 0, 0, 0)
    for x, y in ((0, 0), (2, 0), (0, 3)):
        data += struct.pack('<9f3i', x, y, 0, 0, 0, 1,
                            1, 1, 1, -1, 0, 0)
    data += struct.pack('<3i', 0, 1, 2)
    return bytes(data)


class ProductLookupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cache = self.root / 'HttpCache'
        self.cache.mkdir()
        self.index = self.root / 'absent-index.json'
        self.output = self.root / 'output'

    def put(self, relative, data):
        path = self.cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self):
        return scan_cache(self.cache, PID, self.index)

    def test_observed_unknown_product_is_found_without_claiming_extractable_geometry(self):
        # O diagnóstico fornece caminho/tamanho, não o conteúdo real do arquivo.
        content = b'unknown-container-fixture'.ljust(301697, b'\0')
        source = self.put(f'15/08/{PRODUCT_NAME}', content)
        report = self.scan()
        self.assertEqual(report.matches, [])
        self.assertEqual(len(report.product_files), 1)
        product = report.product_files[0]
        self.assertEqual(product.source, source)
        self.assertEqual(product.pid, PID)
        self.assertEqual(product.size, 301697)
        self.assertEqual(product.format, 'unknown')
        self.assertEqual(source.read_bytes(), content)

    def test_unknown_product_export_reports_found_but_unsupported_and_writes_no_obj(self):
        content = b'not-a-known-container'
        source = self.put(f'15/08/{PRODUCT_NAME}', content)
        with self.assertRaises(ValueError) as caught:
            export_product(self.cache, PID, self.output, index_path=self.index)
        error = str(caught.exception).lower()
        self.assertIn('localizado', error)
        self.assertIn('formato', error)
        self.assertEqual(list(self.output.glob('*.obj')), [])
        self.assertEqual(source.read_bytes(), content)

    def test_pid_and_hash_are_matched_exactly_and_case_insensitively(self):
        good = self.put(f'aa/bb/PRODUCT{PID}_{HASH.upper()}', b'unknown')
        for name in (f'product1{PID}_{HASH}', f'product{PID}9_{HASH}',
                     f'product{PID}_{HASH[:-1]}', f'product{PID}_{HASH}0',
                     f'product{PID}_{HASH[:-1]}z'):
            self.put(f'others/{name}', b'unknown')
        report = self.scan()
        self.assertEqual([p.source for p in report.product_files], [good])
        self.assertEqual(report.matches, [])

    def test_cmf_with_product_hash_name_exports_without_manual_index(self):
        content = triangle_cmf()
        source = self.put(f'15/08/{PRODUCT_NAME}', content)
        report = self.scan()
        self.assertEqual(len(report.product_files), 1)
        self.assertEqual(report.product_files[0].format, 'CMF')
        self.assertEqual(len(report.matches), 1)
        self.assertEqual(report.matches[0].pid, PID)
        self.assertEqual(report.matches[0].source, source)
        outputs = export_product(self.cache, PID, self.output, index_path=self.index)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(sum(line.startswith('f ') for line in outputs[0].read_text().splitlines()), 1)
        self.assertFalse(self.index.exists())
        self.assertEqual(source.read_bytes(), content)

    def test_zip_product_name_associates_member_without_pid_in_member_name(self):
        source = self.cache / PRODUCT_NAME
        with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('geometry/mesh.cmf', triangle_cmf())
        before = source.read_bytes()
        report = self.scan()
        self.assertEqual(len(report.product_files), 1)
        self.assertEqual(report.product_files[0].format, 'ZIP')
        self.assertEqual(len(report.matches), 1)
        self.assertEqual(report.matches[0].member, 'geometry/mesh.cmf')
        self.assertEqual(report.matches[0].pid, PID)
        outputs = export_product(self.cache, PID, self.output, index_path=self.index)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.cache / 'geometry').exists())

    def test_cli_scan_json_exposes_found_product_even_without_convertible_match(self):
        source = self.put(f'15/08/{PRODUCT_NAME}', b'unknown')
        stream = io.StringIO()
        with redirect_stdout(stream):
            result = main(['scan', str(self.cache), '--pid', PID, '--index', str(self.index)])
        self.assertEqual(result, 0)
        report = json.loads(stream.getvalue())
        self.assertEqual(report['matches'], [])
        self.assertEqual(report['product_files'][0]['source'], str(source))
        self.assertEqual(report['product_files'][0]['pid'], PID)
        self.assertEqual(report['product_files'][0]['format'], 'unknown')

    def test_plain_hash_in_numeric_httpcache_shards_is_not_product_15(self):
        source = self.put(f'15/08/{HASH}', triangle_cmf())
        report = scan_cache(self.cache, '15', self.index)
        self.assertEqual(report.product_files, [])
        self.assertEqual(report.matches, [])
        self.assertEqual(len(report.unassigned), 1)
        self.assertEqual(report.unassigned[0].source, source)
        self.assertIsNone(report.unassigned[0].pid)

    def test_png_reports_image_instead_of_creating_fake_obj(self):
        source = self.put('image-without-extension', b'\x89PNG\r\n\x1a\n' + b'image-fixture')
        with self.assertRaisesRegex(MeshError, 'imagem PNG'):
            convert_file(source, self.output)
        self.assertEqual(list(self.output.glob('*.obj')), [])


if __name__ == '__main__':
    unittest.main()
