"""Diagnóstico limitado e somente leitura do HttpCache.

O JSON contém nomes relativos, tamanhos, assinaturas e offsets de pistas,
nunca trechos de arquivos, URLs, cabeçalhos HTTP ou valores de credenciais.
Uma menção ao PID não prova que uma malha pertence ao produto. Campos
``limits`` e ``complete_*`` distinguem ausência de pista de leitura incompleta.
"""

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from .converter import validate_pid

PROBE_BYTES = 64 * 1024
TOTAL_PROBE_BYTES = 256 * 1024 * 1024
TOTAL_HASH_BYTES = 256 * 1024 * 1024
MAX_HASH_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 100_000
MAX_ITEMS = 1000
MAX_OFFSETS = 128


def _kind(head):
    if head.startswith(b'CMF\0'):
        return 'CMF'
    if head.startswith(b'SQLite format 3\0'):
        return 'SQLite'
    if head.startswith((b'PK\x03\x04', b'PK\x05\x06')):
        return 'ZIP'
    if head.startswith(b'\x1f\x8b'):
        return 'gzip'
    if len(head) >= 2 and head[0] & 15 == 8 and (head[0] * 256 + head[1]) % 31 == 0:
        return 'zlib'
    if head.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'DDS ', b'BM')):
        return 'image'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image'
    if re.search(br'<MESH(?:\s|>)', head) and head.lstrip(b'\xef\xbb\xbf \r\n\t').startswith(b'<'):
        return 'XMF'
    return 'unknown'


def _pid_offsets(data, pid):
    ascii_matches = [m.start() for m in re.finditer(rb'(?<![0-9])' + pid.encode('ascii') + rb'(?![0-9])', data)]
    utf16 = pid.encode('utf-16le')
    utf16_matches = []
    start = 0
    while (offset := data.find(utf16, start)) >= 0:
        before, after = data[max(0, offset - 2):offset], data[offset + len(utf16):offset + len(utf16) + 2]
        is_digit = lambda pair: len(pair) == 2 and 48 <= pair[0] <= 57 and pair[1] == 0
        if not is_digit(before) and not is_digit(after):
            utf16_matches.append(offset)
        start = offset + 1
    return ascii_matches, utf16_matches


def collect_diagnostics(cache: Path, pid: str) -> dict:
    """Examina assinaturas e PID literal; não resolve PID→malha nem lê bancos."""
    cache = Path(cache).resolve()
    pid = validate_pid(pid)
    if pid is None:
        raise ValueError('Informe o PID para procurar pistas no cache.')
    if not cache.is_dir():
        raise ValueError('Pasta de cache não encontrada.')
    limits = dict(probe_per_file_bytes=PROBE_BYTES, max_probe_bytes=TOTAL_PROBE_BYTES,
                  max_hash_bytes=TOTAL_HASH_BYTES, max_hash_file_bytes=MAX_HASH_FILE_BYTES,
                  max_files=MAX_FILES, max_items_per_list=MAX_ITEMS,
                  probe_truncated_files=0, sqlite_signature_only_files=0,
                  probe_budget_exhausted=False, file_limit_reached=False,
                  hashes_not_computed=0, lists_truncated={})
    report = dict(schema_version=1, pid=pid, scanned_files=0, probe_bytes=0, hash_bytes=0,
                  complete_enumeration=True, complete_content_search=True, limits=limits,
                  file_samples=[], mesh_candidates=[], pid_mentions=[], possible_indexes=[],
                  warnings=[], types={}, safety_notice=(
                      'Relatório com nomes relativos e pistas; não contém conteúdo bruto dos arquivos. '
                      'Reveja os nomes antes de compartilhar. Menção ao PID não confirma associação à malha.'))
    types = Counter()

    def append(name, value):
        if len(report[name]) < MAX_ITEMS:
            report[name].append(value)
        else:
            limits['lists_truncated'][name] = True

    def warning(path, message):
        append('warnings', f'{path}: {message}')

    def walking_error(exc):
        report['complete_enumeration'] = False
        warning('pasta', f'Falha de leitura ({type(exc).__name__}); enumeração incompleta.')

    for root, directories, names in os.walk(cache, followlinks=False, onerror=walking_error):
        directories[:] = sorted(d for d in directories if not (Path(root) / d).is_symlink())
        for name in sorted(names):
            source = Path(root) / name
            if source.is_symlink():
                continue
            if report['scanned_files'] >= MAX_FILES:
                limits['file_limit_reached'] = True
                report['complete_enumeration'] = report['complete_content_search'] = False
                return report | {'types': dict(types)}
            relative = source.relative_to(cache).as_posix()
            report['scanned_files'] += 1
            try:
                size = source.stat().st_size
                remaining = max(0, TOTAL_PROBE_BYTES - report['probe_bytes'])
                amount = min(PROBE_BYTES, remaining)
                with source.open('rb') as stream:
                    head = stream.read(min(16, amount))
                    sqlite = head.startswith(b'SQLite format 3\0')
                    if not sqlite:
                        head += stream.read(max(0, amount - len(head)))
                report['probe_bytes'] += len(head)
                kind = _kind(head)
                types[kind] += 1
                record = dict(path=relative, size=size, type=kind, probe_bytes=len(head),
                              probe_complete=len(head) >= size)
                if len(head) < size:
                    limits['probe_truncated_files'] += 1
                    report['complete_content_search'] = False
                if sqlite:
                    limits['sqlite_signature_only_files'] += 1
                    record['probe_scope'] = 'signature_only; banco não examinado'
                if amount < min(PROBE_BYTES, size):
                    limits['probe_budget_exhausted'] = True
                    report['complete_content_search'] = False
                append('file_samples', record)
                index_name = re.search(r'^(?:index|headers?|metadata|manifest|cachemap)|\.(?:db|sqlite3?|dat|json|header)$', name, re.I)
                if kind == 'SQLite' or index_name:
                    append('possible_indexes', dict(path=relative, size=size, type=kind))
                if sqlite:
                    continue
                cmf_offsets = [m.start() for m in re.finditer(re.escape(b'CMF\0'), head)]
                if cmf_offsets or kind == 'XMF':
                    mesh = dict(record, cmf_offsets=cmf_offsets[:MAX_OFFSETS],
                                offsets_truncated=len(cmf_offsets) > MAX_OFFSETS)
                    if kind == 'CMF' and size <= MAX_HASH_FILE_BYTES and report['hash_bytes'] + size <= TOTAL_HASH_BYTES:
                        digest, read_bytes = hashlib.sha256(), 0
                        with source.open('rb') as stream:
                            while block := stream.read(min(1024 * 1024, MAX_HASH_FILE_BYTES + 1 - read_bytes)):
                                digest.update(block)
                                read_bytes += len(block)
                        report['hash_bytes'] += read_bytes
                        if read_bytes == size:
                            mesh['sha256'] = digest.hexdigest()
                        else:
                            warning(relative, 'Arquivo mudou durante o hash; SHA-256 omitido.')
                    elif kind == 'CMF':
                        limits['hashes_not_computed'] += 1
                    append('mesh_candidates', mesh)
                ascii_offsets, utf16_offsets = _pid_offsets(head, pid)
                if ascii_offsets or utf16_offsets:
                    append('pid_mentions', dict(path=relative, ascii_offsets=ascii_offsets[:MAX_OFFSETS],
                                               utf16le_offsets=utf16_offsets[:MAX_OFFSETS],
                                               offsets_truncated=max(len(ascii_offsets), len(utf16_offsets)) > MAX_OFFSETS,
                                               association_confirmed=False))
            except OSError as exc:
                report['complete_content_search'] = False
                warning(relative, f'Falha de leitura ({type(exc).__name__}).')
    report['types'] = dict(types)
    return report


def save_diagnostics(cache: Path, pid: str, output: Path) -> Path:
    """Salva JSON fora do cache, sem substituir nenhum arquivo existente."""
    cache, output = Path(cache).resolve(), Path(output).resolve()
    if output == cache or cache in output.parents:
        raise ValueError('Salve o diagnóstico fora da pasta do cache.')
    if output.exists():
        raise FileExistsError(f'O relatório já existe: {output}')
    report = collect_diagnostics(cache, pid)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return output
