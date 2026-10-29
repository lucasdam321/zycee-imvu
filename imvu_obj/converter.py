"""Conversão local: nenhum download, extração no cache ou acesso à conta."""

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile
import zlib

from .cal3d import MeshError, parse_cmf, parse_xmf

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ZIP_BYTES = 128 * 1024 * 1024


@dataclass
class Asset:
    member: str | None
    data: bytes
    format: str


def detect_format(data: bytes) -> str | None:
    if data.startswith(b'CMF\0'):
        return 'CMF'
    head = data[:65536].lstrip(b'\xef\xbb\xbf \r\n\t')
    if head.startswith(b'<') and re.search(br'<MESH(?:\s|>)', head):
        return 'XMF'
    return None


def read_assets(source: Path) -> list[Asset]:
    source = Path(source)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise MeshError(f'Arquivo excede o limite de {MAX_FILE_BYTES // 1024 // 1024} MiB: {source}')
    with source.open('rb') as stream:
        head = stream.read(65536)
    fmt = detect_format(head)
    if fmt:
        with source.open('rb') as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise MeshError('Arquivo cresceu durante a leitura; tente novamente com o IMVU fechado.')
        return [Asset(None, data, fmt)]
    if head.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) > 10000 or sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                    raise MeshError('ZIP excede os limites de tamanho/quantidade de arquivos.')
                assets = []
                for info in infos:
                    if info.is_dir():
                        continue
                    if info.flag_bits & 1:
                        raise MeshError('ZIP criptografado não suportado.')
                    if info.file_size > MAX_FILE_BYTES:
                        raise MeshError('Entrada ZIP excede 64 MiB.')
                    with archive.open(info) as stream:
                        member_head = stream.read(65536)
                        member_format = detect_format(member_head)
                        if member_format:
                            data = member_head + stream.read(MAX_FILE_BYTES + 1 - len(member_head))
                            if len(data) > MAX_FILE_BYTES:
                                raise MeshError('Entrada ZIP excede o limite de leitura.')
                            assets.append(Asset(info.filename, data, member_format))
                return assets
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
            raise MeshError(f'ZIP inválido ou não suportado: {exc}') from exc
    return []


def validate_pid(pid: str | None) -> str | None:
    if pid is not None and not re.fullmatch(r'[1-9][0-9]{0,19}', str(pid)):
        raise ValueError('PID deve conter somente dígitos, sem zeros à esquerda.')
    return str(pid) if pid is not None else None


def _safe_name(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', value)[:48].strip('_') or 'mesh'


def prepare_outputs(source: Path, asset: Asset, output_dir: Path, *, pid=None,
                    center=False, scale=1.0, evidence='arquivo selecionado'):
    pid = validate_pid(pid)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Escala deve ser um número finito maior que zero.')
    mesh = parse_cmf(asset.data) if asset.format == 'CMF' else parse_xmf(asset.data)
    positions = [v for sub in mesh.submeshes for v in sub.vertices]
    lo = [min(v[i] for v in positions) for i in range(3)]
    hi = [max(v[i] for v in positions) for i in range(3)]
    origin = [(a + b) / 2 for a, b in zip(lo, hi)] if center else [0.0] * 3
    sha = hashlib.sha256(asset.data).hexdigest()
    stem = Path(asset.member or source.name).stem
    name = (f'PID_{pid}_' if pid else '') + _safe_name(stem) + '_' + sha[:12]
    obj_path = Path(output_dir) / (name + '.obj')
    mtl_path = obj_path.with_suffix('.mtl')
    report_path = obj_path.with_suffix('.json')
    obj = ['# IMVU OBJ: geometria local Cal3D, UV e normais', f'mtllib {mtl_path.name}']
    mtl = ['# Materiais neutros; nenhuma textura foi inferida.']
    vertex_offset = uv_offset = normal_offset = 0
    materials = set()
    for sub_id, sub in enumerate(mesh.submeshes):
        material = 'material_' + str(sub.material).replace('-', 'minus_')
        obj += [f'o submesh_{sub_id}', f'usemtl {material}']
        if material not in materials:
            mtl += [f'newmtl {material}', 'Ka 0.2 0.2 0.2', 'Kd 0.8 0.8 0.8', 'Ks 0 0 0', 'd 1', '']
            materials.add(material)
        for vertex in sub.vertices:
            value = tuple((vertex[i] - origin[i]) * scale for i in range(3))
            if not all(math.isfinite(x) for x in value):
                raise MeshError('Transformação produziu coordenadas não finitas.')
            obj.append('v ' + ' '.join(format(x, '.9g') for x in value))
        for uv in sub.uvs:
            obj.append('vt ' + ' '.join(format(x, '.9g') for x in uv))
        for normal in sub.normals:
            obj.append('vn ' + ' '.join(format(x, '.9g') for x in normal))
        for face in sub.faces:
            refs = []
            for i in face:
                v, n = i + vertex_offset + 1, i + normal_offset + 1
                refs.append(f'{v}/{i + uv_offset + 1}/{n}' if sub.uvs else f'{v}//{n}')
            obj.append('f ' + ' '.join(refs))
        vertex_offset += len(sub.vertices)
        uv_offset += len(sub.uvs)
        normal_offset += len(sub.normals)
    report = {
        'source': str(Path(source).resolve()), 'zip_member': asset.member,
        'sha256': sha, 'pid': pid, 'pid_evidence': evidence,
        'format': asset.format, 'version': mesh.version,
        'vertices': len(positions), 'triangles': sum(len(s.faces) for s in mesh.submeshes),
        'submeshes': len(mesh.submeshes), 'source_bounds': [lo, hi],
        'transform': {'subtract_origin': origin, 'scale': scale, 'axes': 'unchanged'},
        'warnings': list(dict.fromkeys(mesh.warnings)),
        'textures': 'Não fornecidas: MTL neutro, sem vínculo presumido com imagens do cache.',
    }
    return obj_path, {
        obj_path: '\n'.join(obj) + '\n',
        mtl_path: '\n'.join(mtl) + '\n',
        report_path: json.dumps(report, indent=2, ensure_ascii=False) + '\n',
    }


def write_outputs(outputs: dict, *, overwrite=False):
    """Valida tudo antes de escrever; por padrão, nunca substitui uma saída."""
    for path in outputs:
        if path.exists() and not overwrite:
            raise FileExistsError(f'Saída já existe: {path}. Escolha outra pasta ou use --overwrite.')
    created = []
    try:
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('w' if overwrite else 'x', encoding='utf-8', newline='\n') as stream:
                created.append(path)
                stream.write(content)
    except OSError:
        if not overwrite:
            for path in created:
                path.unlink(missing_ok=True)
        raise


def convert_file(source: Path, output_dir: Path, *, pid: str | None = None,
                 center=False, scale=1.0, overwrite=False) -> list[Path]:
    source = Path(source)
    assets = read_assets(source)
    if not assets:
        raise MeshError('Nenhuma malha CMF/XMF identificada. O arquivo pode ser imagem, metadado ou um contêiner não suportado.')
    outputs, objects = {}, []
    for asset in assets:
        obj, files = prepare_outputs(source, asset, output_dir, pid=pid, center=center,
                                     scale=scale, evidence='PID informado pelo usuário' if pid else 'sem associação de PID')
        if obj in outputs:
            continue
        outputs.update(files)
        objects.append(obj)
    write_outputs(outputs, overwrite=overwrite)
    return objects
