"""Leitura de CMF segundo o layout público do Cal3D mantido pelo IMVU.

Referência: https://github.com/imvu/cal3d/blob/master/src/cal3d/loader.cpp
Implementação independente; não executa conteúdo do cache.
"""

from dataclasses import dataclass, field
import math
import struct
import xml.etree.ElementTree as ET


class MeshError(ValueError):
    """Arquivo não suportado, inválido ou incompleto."""


@dataclass
class Submesh:
    material: int
    vertices: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uvs: list = field(default_factory=list)
    faces: list = field(default_factory=list)


@dataclass
class Mesh:
    version: int
    submeshes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise MeshError(f"CMF truncado no byte {self.offset} (faltam dados).")
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def unpack(self, fmt):
        values = struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            raise MeshError(f"Número não finito no byte {self.offset}.")
        return values

    def integer(self):
        return self.unpack('i')[0]

    def count(self, name, maximum=2_000_000):
        value = self.integer()
        if not 0 <= value <= maximum:
            raise MeshError(f"Contagem inválida de {name}: {value}.")
        return value


def parse_cmf(data: bytes) -> Mesh:
    r = Reader(data)
    if r.take(4) != b'CMF\0':
        raise MeshError("Assinatura CMF ausente; este arquivo não é uma malha Cal3D binária.")
    version = r.integer()
    if not 699 <= version <= 919:
        raise MeshError(f"Versão CMF {version} não suportada (699–919).")
    result = Mesh(version)
    submesh_count = r.count('submalhas', 4096)
    for _ in range(submesh_count):
        sub = Submesh(r.integer())
        nv = r.count('vértices')
        nf = r.count('faces', 4_000_000)
        lod = r.count('níveis LOD')
        ns = r.count('molas')
        nt = r.count('canais UV', 32)
        nm = r.count('morph targets', 4096) if version >= 911 else 0
        minimum_vertex_size = 36 + (12 if version >= 911 else 0) + nt * 8 + (4 if ns else 0)
        if nv * minimum_vertex_size + nf * 12 + ns * 16 > len(data) - r.offset:
            raise MeshError("Contagens excedem os dados disponíveis no CMF.")
        if nt > 1:
            result.warnings.append('OBJ conserva somente o primeiro canal UV.')
        if lod:
            result.warnings.append('LOD ignorado: exportada a malha base completa.')
        for _vertex in range(nv):
            sub.vertices.append(r.unpack('3f'))
            sub.normals.append(r.unpack('3f'))
            if version >= 911:
                r.unpack('3f')  # Cores de vértice não fazem parte do OBJ padrão.
            r.unpack('2i')  # collapseId, faceCollapseCount
            for uv_channel in range(nt):
                uv = r.unpack('2f')
                if uv_channel == 0:
                    sub.uvs.append(uv)
            influences = r.count('influências por vértice', 65536)
            if influences * 8 > len(data) - r.offset:
                raise MeshError('Influências truncadas.')
            for _influence in range(influences):
                bone, weight = r.unpack('if')
                if bone < 0 or weight < 0:
                    raise MeshError('Influência de osso inválida.')
            if ns:
                r.unpack('f')
        for _spring in range(ns):
            a, b, coefficient, length = r.unpack('2i2f')
            if not (0 <= a < nv and 0 <= b < nv):
                raise MeshError('Mola referencia vértice inexistente.')
        if nm:
            result.warnings.append('Morph targets omitidos; exportada a geometria base.')
        # Layout sparse do loader Cal3D/IMVU: id seguinte após cada blend vertex.
        for _morph in range(nm):
            r.take(r.count('bytes do nome de morph', 1_048_576))
            blend_id = r.integer()
            for vertex_id in range(nv):
                if vertex_id >= blend_id:
                    r.unpack('6f')
                    for _channel in range(nt):
                        r.unpack('2f')
                    blend_id = r.integer()
        for _face in range(nf):
            face = r.unpack('3i')
            if any(i < 0 or i >= nv for i in face):
                raise MeshError(f'Face referencia vértice inexistente: {face}.')
            sub.faces.append(face)
        result.submeshes.append(sub)
    if r.offset != len(data):
        raise MeshError(f'CMF possui {len(data) - r.offset} bytes finais não reconhecidos; conversão interrompida.')
    if not any(s.vertices and s.faces for s in result.submeshes):
        raise MeshError('A malha não contém triângulos para exportar.')
    result.warnings.append('OBJ estático: ossos, animações, física e cores de vértice não são exportados.')
    return result


def parse_xmf(data: bytes) -> Mesh:
    """Aceita XML Cal3D, inclusive HEADER e MESH como elementos irmãos."""
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise MeshError('DTD/entidades XML não são aceitas.')
    try:
        text = data.decode('utf-8-sig').strip()
        if text.startswith('<?xml'):
            text = text[text.index('?>') + 2:]
        root = ET.fromstring('<CAL3D>' + text + '</CAL3D>')
        mesh_element = root.find('MESH')
        if mesh_element is None:
            raise MeshError('Elemento MESH ausente no XMF.')
        header = root.find('HEADER')
        version = int(header.get('VERSION', '0')) if header is not None else 0
        mesh = Mesh(version, warnings=['OBJ estático: esqueleto, animações e morph targets não são exportados.'])
        for element in mesh_element.findall('SUBMESH'):
            sub = Submesh(int(element.get('MATERIAL', '0')))
            vertices = element.findall('VERTEX')
            if len(vertices) > 2_000_000:
                raise MeshError('XMF excede o limite de vértices.')
            ids = {}
            has_uv = None
            for vertex in vertices:
                vertex_id = int(vertex.attrib['ID'])
                if vertex_id in ids:
                    raise MeshError('IDs de vértice duplicados no XMF.')
                ids[vertex_id] = len(sub.vertices)
                sub.vertices.append(_xml_vector(vertex.findtext('POS'), 3))
                sub.normals.append(_xml_vector(vertex.findtext('NORM'), 3))
                uv = vertex.find('TEXCOORD')
                if has_uv is not None and has_uv != (uv is not None):
                    raise MeshError('Canal UV incompleto no XMF.')
                has_uv = uv is not None
                if has_uv:
                    sub.uvs.append(_xml_vector(uv.text, 2))
            for face in element.findall('FACE'):
                indices = [int(x) for x in face.attrib['VERTEXID'].split()]
                if len(indices) != 3:
                    raise MeshError('Face XMF não triangular.')
                sub.faces.append(tuple(ids[i] for i in indices))
            for attr, actual in [('NUMVERTICES', len(vertices)), ('NUMFACES', len(sub.faces))]:
                if attr in element.attrib and int(element.attrib[attr]) != actual:
                    raise MeshError(f'Contagem {attr} incorreta no XMF.')
            mesh.submeshes.append(sub)
        if not any(s.vertices and s.faces for s in mesh.submeshes):
            raise MeshError('XMF sem geometria triangular.')
        return mesh
    except (ET.ParseError, UnicodeError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, MeshError):
            raise
        raise MeshError(f'XMF inválido: {exc}') from exc


def _xml_vector(text, size):
    values = tuple(float(v) for v in (text or '').split())
    if len(values) != size or not all(math.isfinite(v) for v in values):
        raise MeshError('Vetor ausente, inválido ou não finito no XMF.')
    return values
