"""python -m imvu_obj --help"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .cache import associate, export_product, scan_cache
from .converter import convert_file, detect_format, read_assets
from .cal3d import parse_cmf, parse_xmf


def main(argv=None):
    parser = argparse.ArgumentParser(description='Converter malhas locais Cal3D do IMVU para OBJ; sem acessar sua conta.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('gui', help='Abrir interface desktop')
    inspect = commands.add_parser('inspect', help='Identificar geometria de um arquivo local')
    inspect.add_argument('source', type=Path)
    scan = commands.add_parser('scan', help='Buscar malhas no cache e suas associações de PID')
    scan.add_argument('cache', type=Path)
    scan.add_argument('--pid')
    scan.add_argument('--index', type=Path)
    diagnose = commands.add_parser('diagnose', help='Gerar diagnóstico do HttpCache sem copiar seu conteúdo')
    diagnose.add_argument('cache', type=Path)
    diagnose.add_argument('--pid', required=True)
    diagnose.add_argument('-o', '--output', type=Path, required=True, help='Arquivo JSON fora da pasta de cache')
    bind = commands.add_parser('associate', help='Cadastrar associação arquivo→PID confirmada por você')
    bind.add_argument('source', type=Path)
    bind.add_argument('--pid', required=True)
    bind.add_argument('--index', type=Path)
    convert = commands.add_parser('convert', help='Converter arquivo direto, mesmo sem saber o PID')
    convert.add_argument('source', type=Path)
    convert.add_argument('--pid', help='Rótulo informado por você; não valida o produto')
    export = commands.add_parser('export', help='Buscar e converter malhas associadas a um PID')
    export.add_argument('cache', type=Path)
    export.add_argument('--pid', required=True)
    export.add_argument('--index', type=Path)
    for command in (convert, export):
        command.add_argument('-o', '--output', type=Path, required=True)
        command.add_argument('--center', action='store_true', help='Centralizar o bbox na origem')
        command.add_argument('--scale', type=float, default=1.0)
        command.add_argument('--overwrite', action='store_true', help='Substituir saídas existentes explicitamente')
    args = parser.parse_args(argv)
    try:
        if args.command in (None, 'gui'):
            try:
                from .gui import main as gui_main
                gui_main()
            except ImportError as exc:
                raise ValueError('Interface requer Python com Tcl/Tk. No terminal, use python -m imvu_obj --help.') from exc
            except Exception as exc:
                if exc.__class__.__name__ == 'TclError':
                    raise ValueError(f'Não foi possível abrir a interface gráfica: {exc}. Use os comandos de terminal.') from exc
                raise
        elif args.command == 'inspect':
            assets = read_assets(args.source)
            if not assets:
                raise ValueError('Nenhuma malha CMF/XMF identificada neste arquivo.')
            details = []
            for asset in assets:
                mesh = parse_cmf(asset.data) if asset.format == 'CMF' else parse_xmf(asset.data)
                details.append({'member': asset.member, 'format': asset.format, 'version': mesh.version,
                                'vertices': sum(len(s.vertices) for s in mesh.submeshes),
                                'triangles': sum(len(s.faces) for s in mesh.submeshes),
                                'submeshes': len(mesh.submeshes), 'warnings': list(dict.fromkeys(mesh.warnings))})
            print(json.dumps(details, ensure_ascii=False, indent=2))
        elif args.command == 'scan':
            result = scan_cache(args.cache, args.pid, args.index)
            print(json.dumps(asdict(result), default=str, ensure_ascii=False, indent=2))
        elif args.command == 'associate':
            print(f'Associação cadastrada em: {associate(args.source, args.pid, args.index)}')
        elif args.command == 'diagnose':
            from .diagnostics import save_diagnostics
            path = save_diagnostics(args.cache, args.pid, args.output)
            print(f'Diagnóstico salvo: {path}')
            print('O relatório contém nomes relativos, tamanhos e pistas; não contém conteúdo bruto nem confirma associação ao PID.')
        else:
            options = dict(center=args.center, scale=args.scale, overwrite=args.overwrite)
            paths = (convert_file(args.source, args.output, pid=args.pid, **options)
                     if args.command == 'convert' else
                     export_product(args.cache, args.pid, args.output, index_path=args.index, **options))
            for path in paths:
                print(f'Salvo: {path}')
            print('MTL neutro e relatório JSON salvos junto de cada OBJ. Texturas e animações não foram recuperadas.')
        return 0
    except (ValueError, OSError) as exc:
        print(f'Erro: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Operação interrompida.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
