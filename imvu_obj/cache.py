"""Busca conservadora por PID. Hashes de nome não são tratados como PIDs."""

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from .converter import (MAX_FILE_BYTES, Asset, prepare_outputs, read_assets,
                        validate_pid, write_outputs)

# Associação fornecida pelo usuário e fixada pelo CONTEÚDO, não pelo nome.
KNOWN_SAMPLES = {
    'e435d146055e2dfe91a94b890abcda5717bddd14317e960d53ed38cedfeb042a': '74667074',
}


@dataclass
class Candidate:
    source: Path
    member: str | None
    format: str
    pid: str | None
    evidence: str
    sha256: str


@dataclass
class ScanReport:
    matches: list = field(default_factory=list)
    unassigned: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    scanned_files: int = 0


def default_index_path():
    return Path.home() / 'IMVU-OBJ' / 'product-index.json'


def _load_index(index_path=None):
    path = Path(index_path) if index_path else default_index_path()
    if not path.exists():
        return {'version': 1, 'sha256_to_pid': {}}
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Índice de produtos excede 8 MiB.')
    try:
        content = json.loads(path.read_text(encoding='utf-8'))
        mapping = content['sha256_to_pid']
        if content.get('version') != 1 or not isinstance(mapping, dict):
            raise ValueError('Esquema de índice desconhecido.')
        for sha, pid in mapping.items():
            if not re.fullmatch('[0-9a-f]{64}', sha):
                raise ValueError('Hash inválido no índice.')
            if not isinstance(pid, str):
                raise ValueError('PID no índice deve ser uma string numérica.')
            validate_pid(pid)
        return content
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f'Índice de produtos inválido: {path}: {exc}') from exc


def associate(source: Path, pid: str, index_path=None):
    """Registra apenas uma associação declarada explicitamente pelo usuário."""
    pid = validate_pid(pid)
    if pid is None:
        raise ValueError('Informe um PID para cadastrar a associação.')
    assets = read_assets(Path(source))
    if not assets:
        raise ValueError('Arquivo não contém CMF/XMF identificável.')
    content = _load_index(index_path)
    for asset in assets:
        sha = hashlib.sha256(asset.data).hexdigest()
        previous = content['sha256_to_pid'].get(sha) or KNOWN_SAMPLES.get(sha)
        if previous is not None and previous != pid:
            raise ValueError(f'Esta malha já está associada ao PID {previous}; associação conflitante recusada.')
        content['sha256_to_pid'][sha] = pid
    path = Path(index_path) if index_path else default_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # O índice fica fora do cache. replace evita deixar JSON pela metade.
    import tempfile
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.index-', suffix='.tmp', delete=False) as temp:
        temporary = Path(temp.name)
        json.dump(content, temp, indent=2)
        temp.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _path_pids(relative):
    found = set()
    for part in PurePosixPath(str(relative).replace('\\', '/')).parts:
        # Somente componentes inequivocamente rotulados: 123, 123.cmf, product_123.
        match = re.fullmatch(r'(?:product[_-]?)?([1-9][0-9]{0,19})(?:\.(?:cmf|xmf|zip))?', part, re.I)
        if match:
            found.add(match.group(1))
    return found


def scan_cache(cache: Path, pid: str | None = None, index_path=None) -> ScanReport:
    cache = Path(cache).resolve()
    pid = validate_pid(pid)
    if not cache.is_dir():
        raise ValueError(f'Pasta de cache não encontrada: {cache}')
    mapping = dict(KNOWN_SAMPLES)
    for sha, indexed_pid in _load_index(index_path)['sha256_to_pid'].items():
        if sha in mapping and mapping[sha] != indexed_pid:
            raise ValueError('Índice conflita com a amostra conhecida; corrija a associação.')
        mapping[sha] = indexed_pid
    report = ScanReport()

    def walking_error(exc):
        report.warnings.append(f'Não foi possível ler uma pasta: {exc}')

    for root, directories, names in os.walk(cache, followlinks=False, onerror=walking_error):
        directories[:] = sorted(d for d in directories if not (Path(root) / d).is_symlink())
        for name in sorted(names):
            source = Path(root) / name
            if source.is_symlink():
                continue
            report.scanned_files += 1
            try:
                # Arquivos grandes são relatados, não alocados integralmente.
                if source.stat().st_size > MAX_FILE_BYTES:
                    report.warnings.append(f'Ignorado (maior que 64 MiB): {source}')
                    continue
                assets = read_assets(source)
                for asset in assets:
                    sha = hashlib.sha256(asset.data).hexdigest()
                    path_pids = _path_pids(source.relative_to(cache))
                    # Um cache selecionado diretamente na pasta do produto também funciona.
                    path_pids |= _path_pids(cache.name)
                    if asset.member:
                        path_pids |= _path_pids(asset.member)
                    associated = mapping.get(sha)
                    evidence = 'SHA-256 da amostra informada pelo usuário' if sha in KNOWN_SAMPLES else 'SHA-256 cadastrado pelo usuário'
                    if associated is not None and path_pids and path_pids != {associated}:
                        report.warnings.append(f'PID do caminho diverge do hash cadastrado: {source}; prevalece a associação explícita do conteúdo.')
                    if associated is None:
                        if len(path_pids) == 1:
                            associated = next(iter(path_pids))
                            evidence = 'PID explícito no nome de arquivo/pasta (confirme a organização do cache)'
                        else:
                            evidence = 'PID não identificado' if not path_pids else 'PIDs conflitantes no caminho'
                    candidate = Candidate(source, asset.member, asset.format, associated, evidence, sha)
                    if associated is None:
                        report.unassigned.append(candidate)
                    elif pid is None or associated == pid:
                        report.matches.append(candidate)
            except (OSError, ValueError) as exc:
                report.warnings.append(f'{source}: {exc}')
    return report


def export_product(cache: Path, pid: str, output_dir: Path, *, center=False,
                   scale=1.0, overwrite=False, index_path=None) -> list[Path]:
    if validate_pid(pid) is None:
        raise ValueError('Informe o PID do produto que deseja exportar.')
    report = scan_cache(cache, pid, index_path)
    if not report.matches:
        detail = f' {len(report.unassigned)} malha(s) encontrada(s) sem PID.' if report.unassigned else ''
        raise ValueError(f'Não foi encontrada malha associada ao PID {pid}.{detail} '
                         'PIDs não podem ser deduzidos de um nome hash. Use conversão direta ou cadastre uma associação confirmada.')
    outputs, objects, seen = {}, [], set()
    for candidate in report.matches:
        if candidate.sha256 in seen:
            continue
        assets = read_assets(candidate.source)
        asset = next((a for a in assets if a.member == candidate.member and
                      hashlib.sha256(a.data).hexdigest() == candidate.sha256), None)
        if asset is None:
            raise ValueError('O cache mudou durante a busca; feche o IMVU e tente novamente.')
        obj, files = prepare_outputs(candidate.source, asset, output_dir, pid=pid,
                                     center=center, scale=scale, evidence=candidate.evidence)
        outputs.update(files)
        objects.append(obj)
        seen.add(candidate.sha256)
    write_outputs(outputs, overwrite=overwrite)
    return objects
