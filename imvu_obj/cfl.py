"""Leitor independente do contêiner local CFL3/DFL3 usado pelo IMVU.

Layout conferido em fontes públicas MIT (nenhum código delas é executado):
Toyz/LibCFL, commit 8541594e34e6e99ece4c42096663ade112335f4b,
Copyright (c) 2019 Ruin; e dhkatz/cfl-converter, commit
25c408ffd769410a44babdcc88f7397e8aea6b02, Copyright 2019 David Katz.
https://github.com/Toyz/LibCFL/blob/8541594e34e6e99ece4c42096663ade112335f4b/LibCFL/CFLHeader.cs
https://github.com/dhkatz/cfl-converter/blob/25c408ffd769410a44babdcc88f7397e8aea6b02/src/index.ts

Retorna entradas em memória; não executa arquivos nem acessa servidores IMVU.
Uma assinatura CFL não comprova que exista geometria dentro do contêiner.
"""

import hashlib
import lzma
from pathlib import PurePosixPath
import re
import struct

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_ENTRIES = 10_000
MAX_LZMA_DICTIONARY = 64 * 1024 * 1024
LZMA_MEMORY_LIMIT = 96 * 1024 * 1024


class CflError(ValueError):
    """Contêiner inválido, incompleto ou fora dos limites suportados."""


def is_cfl(data: bytes) -> bool:
    return data[:4] in (b'CFL3', b'DFL3')


def _decompress(payload, compression, limit):
    if compression == 0:
        if len(payload) > limit:
            raise CflError('Entrada CFL excede o limite descompactado.')
        return payload
    if compression != 4:
        raise CflError(f'Compressão CFL não suportada: {compression}.')
    if len(payload) < 6:
        raise CflError('Bloco LZMA CFL truncado.')
    dictionary = struct.unpack_from('<I', payload, 1)[0]
    if dictionary > MAX_LZMA_DICTIONARY:
        raise CflError('Dicionário LZMA excede o limite de memória.')
    # CFL guarda 5 properties + stream. FORMAT_ALONE requer também 8 bytes
    # de tamanho; ff indica desconhecido e mantém a verificação do marcador EOS.
    encoded = payload[:5] + b'\xff' * 8 + payload[5:]
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=LZMA_MEMORY_LIMIT)
        result = bytearray()
        for offset in range(0, len(encoded), 65536):
            if decoder.eof:
                raise CflError('Bytes inesperados após o fim do stream LZMA.')
            block = encoded[offset:offset + 65536]
            while True:
                result.extend(decoder.decompress(block, max_length=limit + 1 - len(result)))
                block = b''
                if len(result) > limit:
                    raise CflError('Entrada CFL excede o limite descompactado.')
                if decoder.eof:
                    if decoder.unused_data:
                        raise CflError('Bytes inesperados após o fim do stream LZMA.')
                    break
                if decoder.needs_input:
                    break
        if not decoder.eof:
            raise CflError('Stream LZMA CFL incompleto; marcador de fim ausente.')
        return bytes(result)
    except lzma.LZMAError as exc:
        raise CflError(f'Bloco LZMA CFL inválido: {exc}') from exc


def _safe_name(raw):
    try:
        name = raw.decode('utf-8')
    except UnicodeError as exc:
        raise CflError('Nome de entrada CFL não é UTF-8 válido.') from exc
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if (not normalized or '\x00' in normalized or path.is_absolute() or
            '..' in path.parts or ':' in normalized or normalized.endswith('/') or
            any(ord(c) < 32 for c in normalized)):
        raise CflError('Caminho de entrada CFL inseguro ou inválido.')
    if not path.parts:
        raise CflError('Nome de entrada CFL vazio.')
    return path.as_posix()


def read_cfl(data: bytes) -> list[tuple[str, bytes]]:
    """Decodifica CFL3/DFL3 validando offsets, tamanhos e hashes disponíveis."""
    if not is_cfl(data) or len(data) < 16:
        raise CflError('Assinatura CFL3/DFL3 ausente ou cabeçalho truncado.')
    if len(data) > MAX_FILE_BYTES:
        raise CflError('Contêiner CFL excede 64 MiB.')
    directory_offset = struct.unpack_from('<I', data, 4)[0]
    if directory_offset < 8 or directory_offset + 8 > len(data):
        raise CflError('Offset do diretório CFL fora do arquivo.')
    compression, length = struct.unpack_from('<ii', data, directory_offset)
    start = directory_offset + 8
    if length < 0 or start + length != len(data):
        raise CflError('Tamanho do diretório CFL inválido ou dados finais inesperados.')
    directory = _decompress(data[start:start + length], compression, MAX_DIRECTORY_BYTES)
    position, total = 0, 0
    seen, entries = set(), []
    while position < len(directory):
        if len(entries) >= MAX_ENTRIES:
            raise CflError('CFL excede o limite de 10.000 entradas.')
        if len(directory) - position < 14:
            raise CflError('Registro do diretório CFL truncado.')
        size, offset, method, name_length = struct.unpack_from('<iiiH', directory, position)
        position += 14
        if not 0 < name_length <= 4096 or position + name_length > len(directory):
            raise CflError('Comprimento de nome CFL inválido.')
        name = _safe_name(directory[position:position + name_length])
        position += name_length
        if name.casefold() in seen:
            raise CflError('Nomes de entrada CFL duplicados.')
        seen.add(name.casefold())
        expected_hash = None
        if data[:4] == b'DFL3':
            if position + 4 > len(directory):
                raise CflError('Hash DFL3 truncado.')
            hash_length = struct.unpack_from('<i', directory, position)[0]
            position += 4
            if hash_length != 32 or position + hash_length > len(directory):
                raise CflError('Comprimento do hash MD5 DFL3 inválido.')
            raw_hash = directory[position:position + hash_length]
            position += hash_length
            if not re.fullmatch(b'[0-9a-fA-F]{32}', raw_hash):
                raise CflError('Hash MD5 DFL3 inválido.')
            expected_hash = raw_hash.decode('ascii').lower()
        if size < 0 or size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            raise CflError('Tamanho descompactado CFL excede os limites.')
        if offset < 8 or offset + 4 > directory_offset:
            raise CflError('Offset de entrada CFL fora da região de dados.')
        packed_size = struct.unpack_from('<I', data, offset)[0]
        if offset + 4 + packed_size > directory_offset:
            raise CflError('Entrada CFL truncada ou sobrepõe o diretório.')
        payload = data[offset + 4:offset + 4 + packed_size]
        contents = _decompress(payload, method, size)
        if len(contents) != size:
            raise CflError('Tamanho da entrada CFL diverge do diretório.')
        if expected_hash is not None and hashlib.md5(contents).hexdigest() != expected_hash:
            raise CflError('Checksum MD5 da entrada DFL3 não confere.')
        entries.append((name, contents))
        total += size
    return entries
