"""Fixtures independentes do formato CFL público; nenhum asset IMVU incluído."""

import hashlib
import lzma
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from imvu_obj.cfl import CflError, is_cfl, read_cfl


def pack_lzma(data):
    alone = lzma.compress(data, format=lzma.FORMAT_ALONE,
                          filters=[dict(id=lzma.FILTER_LZMA1, dict_size=1 << 20, lc=3, lp=0, pb=2)])
    return alone[:5] + alone[13:]


def make_cfl(entries, *, magic=b'CFL3', compression=4, directory_compression=4):
    """Constrói arquivos conforme o layout documentado, sem chamar o parser."""
    data = bytearray(magic + b'\0' * 8)
    records = bytearray()
    for name, content in entries:
        encoded_name = name.encode('utf-8')
        block = pack_lzma(content) if compression == 4 else content
        offset = len(data)
        data += struct.pack('<I', len(block)) + block
        records += struct.pack('<iiiH', len(content), offset, compression, len(encoded_name)) + encoded_name
        if magic == b'DFL3':
            digest = hashlib.md5(content).hexdigest().encode('ascii')
            records += struct.pack('<i', len(digest)) + digest
    directory_offset = len(data)
    directory = pack_lzma(bytes(records)) if directory_compression == 4 else records
    data += struct.pack('<ii', directory_compression, len(directory)) + directory
    struct.pack_into('<II', data, 4, directory_offset, len(directory))
    return bytes(data)


def triangle():
    result = bytearray(b'CMF\0' + struct.pack('<2i', 919, 1))
    result += struct.pack('<7i', 0, 3, 1, 0, 0, 1, 0)
    for x, y in [(0, 0), (1, 0), (0, 1)]:
        result += struct.pack('<9f2i2fi', x, y, 0, 0, 0, 1, 1, 1, 1, -1, 0, x, y, 0)
    result += struct.pack('<3i', 0, 1, 2)
    return bytes(result)


class CflTests(unittest.TestCase):
    def test_cfl3_and_dfl3_round_trip_compressed_or_raw_entries_and_directory(self):
        expected = [('mesh.cmf', triangle()), ('index.xml', b'<Template/>')]
        for magic in (b'CFL3', b'DFL3'):
            for compression, directory_compression in [(0, 0), (4, 0), (0, 4), (4, 4)]:
                with self.subTest(magic=magic, compression=compression, directory=directory_compression):
                    data = make_cfl(expected, magic=magic, compression=compression,
                                    directory_compression=directory_compression)
                    self.assertTrue(is_cfl(data))
                    self.assertEqual(read_cfl(data), expected)
        self.assertFalse(is_cfl(b'CMF\0'))

    def test_truncated_directory_offsets_and_unknown_compression_rejected(self):
        valid = make_cfl([('mesh.cmf', triangle())], directory_compression=0)
        for size in (0, 4, 8, 15, len(valid) - 1):
            with self.subTest(size=size), self.assertRaises(CflError):
                read_cfl(valid[:size])
        for directory_offset in (0, 4, len(valid), 2**32 - 1):
            broken = bytearray(valid)
            struct.pack_into('<I', broken, 4, directory_offset)
            with self.assertRaises(CflError):
                read_cfl(bytes(broken))
        broken = bytearray(valid)
        offset = struct.unpack_from('<I', valid, 4)[0]
        struct.pack_into('<i', broken, offset, 99)
        with self.assertRaisesRegex(CflError, 'Compressão'):
            read_cfl(bytes(broken))

    def test_paths_duplicate_names_and_invalid_utf8_are_rejected(self):
        for name in ('../../mesh.cmf', '/absolute.cmf', 'C:\\mesh.cmf', 'a\0b', ''):
            with self.subTest(name=name), self.assertRaises(CflError):
                read_cfl(make_cfl([(name, b'abc')]))
        with self.assertRaisesRegex(CflError, 'duplicados'):
            read_cfl(make_cfl([('Mesh.cmf', b'abc'), ('mesh.cmf', b'def')]))
        broken = bytearray(make_cfl([('A.cmf', b'abc')], compression=0, directory_compression=0))
        offset = struct.unpack_from('<I', broken, 4)[0]
        broken[offset + 8 + 14] = 255
        with self.assertRaisesRegex(CflError, 'UTF-8'):
            read_cfl(bytes(broken))

    def test_dfl3_checksum_mismatch_is_rejected(self):
        broken = bytearray(make_cfl([('mesh.cmf', b'abc')], magic=b'DFL3', compression=0, directory_compression=0))
        broken[16] ^= 1
        with self.assertRaisesRegex(CflError, 'Checksum'):
            read_cfl(bytes(broken))

    def test_decompression_and_entry_count_limits_are_enforced(self):
        data = make_cfl([('large.bin', b'a' * 4096)])
        with patch('imvu_obj.cfl.MAX_FILE_BYTES', 1024):
            with self.assertRaisesRegex(CflError, 'limites'):
                read_cfl(data)
        with patch('imvu_obj.cfl.MAX_DIRECTORY_BYTES', 4):
            with self.assertRaisesRegex(CflError, 'limite'):
                read_cfl(data)
        with patch('imvu_obj.cfl.MAX_ENTRIES', 1):
            with self.assertRaisesRegex(CflError, 'entradas'):
                read_cfl(make_cfl([('a', b'1'), ('b', b'2')]))

    def test_integration_converts_mesh_in_cfl_and_finds_pid_filename(self):
        from imvu_obj.cache import export_product, scan_cache
        from imvu_obj.converter import convert_file
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / 'HttpCache'
            source = cache / '15' / '08' / 'product74667074_1508083db59c9bb708c5bd532bb5b85f'
            source.parent.mkdir(parents=True)
            original = make_cfl([('geometry/mesh.cmf', triangle()), ('index.xml', b'<Template/>')])
            source.write_bytes(original)
            direct = convert_file(source, root / 'direct')
            self.assertEqual(len(direct), 1)
            self.assertIn('f 1/1/1 2/2/2 3/3/3', direct[0].read_text())
            report = scan_cache(cache, '74667074', root / 'no_index.json')
            self.assertEqual(len(report.product_files), 1)
            self.assertEqual(report.product_files[0].format, 'CFL3')
            self.assertEqual(len(report.matches), 1)
            self.assertEqual(report.matches[0].member, 'geometry/mesh.cmf')
            exported = export_product(cache, '74667074', root / 'exported', index_path=root / 'no_index.json')
            self.assertEqual(len(exported), 1)
            self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
