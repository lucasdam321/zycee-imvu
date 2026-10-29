"""Testes de formato e exportação sem depender de assets de terceiros.

Para validar também a amostra fornecida pelo usuário, defina IMVU_SAMPLE_PATH.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import tempfile
import unittest

from imvu_obj.cal3d import MeshError, parse_cmf, parse_xmf
from imvu_obj.converter import convert_file


TRIANGLE = ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 4.0, 0.0))
NORMALS = ((0.0, 0.0, 1.0),) * 3
UVS = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))


def cmf_bytes(*, version=919, submeshes=None):
    """Escreve uma fixture seguindo o layout Cal3D, independentemente do parser."""
    if submeshes is None:
        submeshes = [{}]
    result = bytearray(b"CMF\0")
    result.extend(struct.pack("<2i", version, len(submeshes)))
    for submesh in submeshes:
        vertices = submesh.get("vertices", TRIANGLE)
        normals = submesh.get("normals", NORMALS)
        uvs = submesh.get("uvs", UVS)
        faces = submesh.get("faces", ((0, 1, 2),))
        material = submesh.get("material", 7)
        header = [material, len(vertices), len(faces), 0, 0, 1 if uvs else 0]
        if version >= 911:
            header.append(0)  # morph targets
        result.extend(struct.pack("<" + "i" * len(header), *header))
        for index, vertex in enumerate(vertices):
            result.extend(struct.pack("<6f", *vertex, *normals[index]))
            if version >= 911:
                result.extend(struct.pack("<3f", 1.0, 1.0, 1.0))
            result.extend(struct.pack("<2i", -1, 0))  # collapse id/count
            if uvs:
                result.extend(struct.pack("<2f", *uvs[index]))
            result.extend(struct.pack("<iif", 1, 4, 1.0))  # one bone influence
        for face in faces:
            result.extend(struct.pack("<3i", *face))
    return bytes(result)


def cmf_with_auxiliary_blocks(*, springs=False, morph=False, uv_channels=1):
    """Fixture CMF 919 com campos que precisam ser consumidos antes das faces."""
    binary = bytearray(b"CMF\0" + struct.pack("<2i", 919, 1))
    binary.extend(struct.pack("<7i", 7, 3, 1, 0, int(springs), uv_channels, int(morph)))
    for index, position in enumerate(TRIANGLE):
        binary.extend(struct.pack("<9f2i", *position, *NORMALS[index], 1, 1, 1, -1, 0))
        for channel in range(uv_channels):
            u, v = UVS[index]
            # Segundo canal muito diferente para detectar leitura incorreta.
            binary.extend(struct.pack("<2f", u + channel * 10, v + channel * 20))
        binary.extend(struct.pack("<iif", 1, 4, 1.0))
        if springs:
            binary.extend(struct.pack("<f", 0.25 + index))  # peso físico por vértice
    if springs:
        binary.extend(struct.pack("<2i2f", 0, 1, 0.75, 2.0))
    if morph:
        name = b"Smile\0"
        binary.extend(struct.pack("<i", len(name)) + name)
        binary.extend(struct.pack("<i", 1))  # somente o vértice 1 tem blend data
        binary.extend(struct.pack("<6f", 100, 200, 300, 0, 1, 0))
        for channel in range(uv_channels):
            binary.extend(struct.pack("<2f", 0.25 + channel, 0.75 + channel))
        binary.extend(struct.pack("<i", 4))  # sentinel nv + 1
    binary.extend(struct.pack("<3i", 2, 0, 1))
    return bytes(binary)


def read_obj(path):
    data = {"v": [], "vn": [], "vt": [], "f": [], "mtllib": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if not parts or parts[0] not in data:
            continue
        kind = parts[0]
        if kind in ("v", "vn", "vt"):
            data[kind].append(tuple(map(float, parts[1:])))
        elif kind == "f":
            data[kind].append([
                tuple(int(index) if index else None for index in ref.split("/"))
                for ref in parts[1:]
            ])
        else:
            data[kind].append(" ".join(parts[1:]))
    return data


class CmfParserTests(unittest.TestCase):
    def test_version_919_geometry_normals_and_uvs(self):
        mesh = parse_cmf(cmf_bytes())
        self.assertEqual(mesh.version, 919)
        self.assertEqual(len(mesh.submeshes), 1)
        submesh = mesh.submeshes[0]
        self.assertEqual(submesh.material, 7)
        self.assertEqual(list(map(tuple, submesh.vertices)), list(TRIANGLE))
        self.assertEqual(list(map(tuple, submesh.normals)), list(NORMALS))
        self.assertEqual(list(map(tuple, submesh.uvs)), list(UVS))
        self.assertEqual(list(map(tuple, submesh.faces)), [(0, 1, 2)])

    def test_older_versions_do_not_read_colors_or_morph_count(self):
        for version in (700, 900, 910):
            with self.subTest(version=version):
                mesh = parse_cmf(cmf_bytes(version=version))
                self.assertEqual(mesh.version, version)
                self.assertEqual(tuple(mesh.submeshes[0].vertices[1]), TRIANGLE[1])
                self.assertEqual(tuple(mesh.submeshes[0].faces[0]), (0, 1, 2))

    def test_multiple_submeshes_and_absent_uvs(self):
        mesh = parse_cmf(cmf_bytes(submeshes=[{}, {"material": 11, "uvs": ()}]))
        self.assertEqual(len(mesh.submeshes), 2)
        self.assertEqual(mesh.submeshes[1].material, 11)
        self.assertEqual(mesh.submeshes[1].uvs, [])
        self.assertEqual(tuple(mesh.submeshes[1].faces[0]), (0, 1, 2))

    def test_truncation_at_every_field_boundary_is_rejected(self):
        binary = cmf_bytes()
        for size in (0, 3, 4, 7, 8, 11, 12, 39, 40, 64, 99, len(binary) - 1):
            with self.subTest(size=size):
                with self.assertRaises(MeshError):
                    parse_cmf(binary[:size])

    def test_invalid_signature_and_unsupported_versions_are_rejected(self):
        with self.assertRaises(MeshError):
            parse_cmf(b"BAD\0" + cmf_bytes()[4:])
        for version in (1, 920, -1):
            with self.subTest(version=version):
                binary = bytearray(cmf_bytes())
                struct.pack_into("<i", binary, 4, version)
                with self.assertRaises(MeshError):
                    parse_cmf(bytes(binary))

    def test_negative_counts_are_rejected(self):
        # file submesh count; submesh vertex/face/LOD/spring/UV/morph counts
        for offset in (8, 16, 20, 24, 28, 32, 36):
            with self.subTest(offset=offset):
                binary = bytearray(cmf_bytes())
                struct.pack_into("<i", binary, offset, -1)
                with self.assertRaises(MeshError):
                    parse_cmf(bytes(binary))

    def test_out_of_range_and_negative_face_indices_are_rejected(self):
        for face in ((0, 1, 3), (-1, 1, 2)):
            with self.subTest(face=face):
                with self.assertRaises(MeshError):
                    parse_cmf(cmf_bytes(submeshes=[{"faces": (face,)}]))

    def test_nonfinite_geometry_is_rejected(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                vertices = ((value, 0, 0), TRIANGLE[1], TRIANGLE[2])
                with self.assertRaises(MeshError):
                    parse_cmf(cmf_bytes(submeshes=[{"vertices": vertices}]))

    def test_spring_weights_and_spring_block_are_consumed_before_faces(self):
        mesh = parse_cmf(cmf_with_auxiliary_blocks(springs=True))
        submesh = mesh.submeshes[0]
        self.assertEqual(list(map(tuple, submesh.vertices)), list(TRIANGLE))
        self.assertEqual(list(map(tuple, submesh.normals)), list(NORMALS))
        self.assertEqual(list(map(tuple, submesh.uvs)), list(UVS))
        self.assertEqual(list(map(tuple, submesh.faces)), [(2, 0, 1)])

    def test_sparse_morph_data_does_not_replace_base_geometry(self):
        mesh = parse_cmf(cmf_with_auxiliary_blocks(morph=True))
        submesh = mesh.submeshes[0]
        self.assertEqual(list(map(tuple, submesh.vertices)), list(TRIANGLE))
        self.assertEqual(list(map(tuple, submesh.normals)), list(NORMALS))
        self.assertEqual(list(map(tuple, submesh.uvs)), list(UVS))
        self.assertEqual(list(map(tuple, submesh.faces)), [(2, 0, 1)])

    def test_truncated_morph_payload_is_rejected(self):
        binary = cmf_with_auxiliary_blocks(morph=True)
        # Remover faces e metade do sentinel deixa o bloco morph incompleto.
        with self.assertRaises(MeshError):
            parse_cmf(binary[:-14])

    def test_multiple_uv_channels_with_springs_and_morph_keep_first_channel(self):
        mesh = parse_cmf(cmf_with_auxiliary_blocks(springs=True, morph=True, uv_channels=2))
        submesh = mesh.submeshes[0]
        self.assertEqual(list(map(tuple, submesh.vertices)), list(TRIANGLE))
        self.assertEqual(list(map(tuple, submesh.uvs)), list(UVS))
        self.assertEqual(list(map(tuple, submesh.faces)), [(2, 0, 1)])


class XmfParserTests(unittest.TestCase):
    XML = b'''<?xml version="1.0"?>
<HEADER MAGIC="XMF" VERSION="919"/>
<MESH NUMSUBMESH="1">
  <SUBMESH NUMVERTICES="3" NUMFACES="1" MATERIAL="2">
    <VERTEX ID="20"><POS>0 0 0</POS><NORM>0 0 1</NORM><TEXCOORD>0 0</TEXCOORD></VERTEX>
    <VERTEX ID="10"><POS>2 0 0</POS><NORM>0 0 1</NORM><TEXCOORD>1 0</TEXCOORD></VERTEX>
    <VERTEX ID="30"><POS>0 4 0</POS><NORM>0 0 1</NORM><TEXCOORD>0 1</TEXCOORD></VERTEX>
    <FACE VERTEXID="10 30 20"/>
  </SUBMESH>
</MESH>'''

    def test_xml_with_sibling_header_and_sparse_vertex_ids(self):
        mesh = parse_xmf(self.XML)
        self.assertEqual(mesh.version, 919)
        self.assertEqual(len(mesh.submeshes), 1)
        submesh = mesh.submeshes[0]
        self.assertEqual(submesh.material, 2)
        self.assertEqual(list(map(tuple, submesh.vertices)), list(TRIANGLE))
        self.assertEqual(list(map(tuple, submesh.uvs)), list(UVS))
        self.assertEqual(list(map(tuple, submesh.faces)), [(1, 2, 0)])

    def test_incomplete_uvs_and_unknown_vertex_ids_are_rejected(self):
        for bad_xml in (
            self.XML.replace(b"<TEXCOORD>1 0</TEXCOORD>", b""),
            self.XML.replace(b'VERTEXID="10 30 20"', b'VERTEXID="10 30 999"'),
            self.XML.replace(b'NUMVERTICES="3"', b'NUMVERTICES="4"'),
        ):
            with self.subTest(xml=bad_xml):
                with self.assertRaises(MeshError):
                    parse_xmf(bad_xml)

    def test_dtd_entities_are_rejected(self):
        bad_xml = b'<!DOCTYPE MESH [<!ENTITY x "0 0 0">]>' + self.XML
        with self.assertRaises(MeshError):
            parse_xmf(bad_xml)


class ObjExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "asset_sem_extensao"
        self.output = self.root / "exportado"

    def export(self, binary=None, **kwargs):
        self.source.write_bytes(cmf_bytes() if binary is None else binary)
        paths = convert_file(self.source, self.output, **kwargs)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].suffix.lower(), ".obj")
        self.assertTrue(paths[0].is_file())
        return paths[0], read_obj(paths[0])

    def test_exports_geometry_normals_uvs_and_material_reference(self):
        path, obj = self.export()
        self.assertEqual(obj["v"], list(TRIANGLE))
        self.assertEqual(obj["vn"], list(NORMALS))
        self.assertEqual(obj["vt"], list(UVS))
        self.assertEqual(obj["f"], [[(1, 1, 1), (2, 2, 2), (3, 3, 3)]])
        self.assertEqual(len(obj["mtllib"]), 1)
        self.assertTrue((path.parent / obj["mtllib"][0]).is_file())
        self.assertEqual(self.source.read_bytes(), cmf_bytes())

    def test_multiple_submeshes_use_global_face_indices(self):
        shifted = tuple((x + 10, y, z + 20) for x, y, z in TRIANGLE)
        _, obj = self.export(cmf_bytes(submeshes=[{}, {"vertices": shifted, "uvs": ()}]))
        self.assertEqual(obj["v"], list(TRIANGLE) + list(shifted))
        self.assertEqual(len(obj["f"]), 2)
        self.assertEqual([ref[0] for ref in obj["f"][1]], [4, 5, 6])
        self.assertEqual([ref[-1] for ref in obj["f"][1]], [4, 5, 6])
        for ref in obj["f"][1]:
            self.assertEqual(len(ref), 3)
            self.assertIsNone(ref[1])

    def test_center_and_scale_transform_positions_but_preserve_normals(self):
        _, obj = self.export(center=True, scale=0.5)
        self.assertEqual(obj["v"], [(-0.5, -1.0, 0.0), (0.5, -1.0, 0.0), (-0.5, 1.0, 0.0)])
        self.assertEqual(obj["vn"], list(NORMALS))
        for axis in range(3):
            values = [point[axis] for point in obj["v"]]
            self.assertAlmostEqual(min(values) + max(values), 0.0)

    def test_export_without_uvs_uses_valid_double_slash_references(self):
        _, obj = self.export(cmf_bytes(submeshes=[{"uvs": ()}]))
        self.assertEqual(obj["vt"], [])
        self.assertEqual(obj["f"], [[(1, None, 1), (2, None, 2), (3, None, 3)]])

    def test_existing_outputs_are_protected_by_default(self):
        path, _ = self.export()
        original = {item: item.read_bytes() for item in self.output.iterdir() if item.is_file()}
        with self.assertRaises((FileExistsError, ValueError)):
            convert_file(self.source, self.output)
        self.assertEqual(original, {item: item.read_bytes() for item in self.output.iterdir() if item.is_file()})
        self.assertIn(path, original)
        repeated = convert_file(self.source, self.output, overwrite=True)
        self.assertEqual(repeated, [path])

    def test_malformed_input_does_not_create_obj(self):
        self.source.write_bytes(cmf_bytes()[:-1])
        with self.assertRaises(MeshError):
            convert_file(self.source, self.output)
        self.assertEqual(list(self.output.glob("*.obj")), [])


@unittest.skipUnless(os.environ.get("IMVU_SAMPLE_PATH"), "IMVU_SAMPLE_PATH não definido; asset externo não incluído")
class UploadedSampleTests(unittest.TestCase):
    def test_real_sample_counts_bounds_and_obj_round_trip(self):
        sample = Path(os.environ["IMVU_SAMPLE_PATH"])
        mesh = parse_cmf(sample.read_bytes())
        self.assertEqual(mesh.version, 919)
        self.assertEqual(len(mesh.submeshes), 1)
        submesh = mesh.submeshes[0]
        self.assertEqual(len(submesh.vertices), 2079)
        self.assertEqual(len(submesh.faces), 3662)
        self.assertEqual(min(min(face) for face in submesh.faces), 0)
        self.assertEqual(max(max(face) for face in submesh.faces), 2078)
        self.assertAlmostEqual(min(vertex[2] for vertex in submesh.vertices), 1003.764, delta=0.001)
        self.assertAlmostEqual(max(vertex[2] for vertex in submesh.vertices), 1015.764, delta=0.001)
        with tempfile.TemporaryDirectory() as directory:
            paths = convert_file(sample, Path(directory), pid="74667074")
            self.assertEqual(len(paths), 1)
            obj = read_obj(paths[0])
            self.assertEqual(len(obj["v"]), 2079)
            self.assertEqual(len(obj["vt"]), 2079)
            self.assertEqual(len(obj["vn"]), 2079)
            self.assertEqual(len(obj["f"]), 3662)
            for actual, expected in zip(obj["v"], submesh.vertices):
                for left, right in zip(actual, expected):
                    self.assertAlmostEqual(left, right, places=5)
            for actual, expected in zip(obj["f"], submesh.faces):
                self.assertEqual(tuple(ref[0] - 1 for ref in actual), tuple(expected))


if __name__ == "__main__":
    unittest.main()
