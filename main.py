from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.animation_ir import write_animation_ir
from app.maya_preview import write_maya_preview_script_from_raw
from app.extractor import extract_archive
from app.plugins import FreeRadicalPakPlugin
from app.second_sight_raw import (format_second_sight_folder_report, format_second_sight_raw_summary, inspect_second_sight_raw, scan_second_sight_raw_folder)
from app.scanner import scan_folder, is_supported_pak_signature
from app.version import APP_NAME, __version__


def cli(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description='Second Sight / Free Radical PAK extractor')
    p.add_argument('--version', action='version', version=f'{APP_NAME} v{__version__}')
    p.add_argument('--extract', metavar='PAK', help='Extract one P4CK/P5CK/P8CK archive')
    p.add_argument('--extract-all', metavar='GAME_DIR', help='Scan a game folder and extract every supported PAK')
    p.add_argument('-o', '--output', default='SecondSight_Extracted', help='Output directory')
    p.add_argument('--list', metavar='PAK', help='List entries in one archive')
    p.add_argument('--inspect-raw', metavar='RAW', help='Inspect one Second Sight PC RAW header')
    p.add_argument('--scan-raw', metavar='DIR', help='Analyze Second Sight .raw files below a folder and write a versioned RAW report')
    p.add_argument('--export-raw-ir', metavar='RAW', help='Decode one Second Sight RAW into destination-neutral animation IR JSON')
    p.add_argument('--export-maya-preview', metavar='ANIM_RAW', help='Generate a Maya Python preview importer for one 21-bone animation RAW')
    p.add_argument('--bind-raw', metavar='BIND_RAW', help='Bind/static pose RAW used with --export-maya-preview')
    p.add_argument('--maya-unit-scale', type=float, default=100.0, help='Position scale for Maya preview export (default: 100)')
    args = p.parse_args(argv)

    plugin = FreeRadicalPakPlugin()
    out = Path(args.output)

    if args.export_maya_preview:
        if not args.bind_raw:
            p.error('--export-maya-preview requires --bind-raw')
        anim_path = Path(args.export_maya_preview)
        bind_path = Path(args.bind_raw)
        out.mkdir(parents=True, exist_ok=True)
        maya_path = out / f"{anim_path.stem}_maya_preview.py"
        meta = write_maya_preview_script_from_raw(
            bind_path,
            anim_path,
            maya_path,
            unit_scale=args.maya_unit_scale,
        )
        print(f"Maya preview script: {maya_path}")
        print(f"Resolved core joints: 19 / 21; unresolved Second Sight indices: 19,20")
        print(f"Bind max mirror error: {meta['bind_validation']['max_mirror_error']}")
        return 0

    if args.export_raw_ir:
        raw_path = Path(args.export_raw_ir)
        out.mkdir(parents=True, exist_ok=True)
        ir_path = out / f"{raw_path.stem}_animation_ir.json"
        ir = write_animation_ir(raw_path, ir_path)
        print(f"IR: {ir_path}")
        print(f"Kind: {ir['kind']} / bones={ir['bone_count']} / keys={ir['key_count']}")
        return 0

    if args.scan_raw:
        report = scan_second_sight_raw_folder(Path(args.scan_raw))
        out.mkdir(parents=True, exist_ok=True)
        report_path = out / f'raw_analysis_report_v{__version__}.json'
        import json
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        print(format_second_sight_folder_report(report), end='')
        print(f'Report: {report_path}')
        return 1 if report['parse_errors'] else 0

    if args.inspect_raw:
        raw = inspect_second_sight_raw(Path(args.inspect_raw))
        print(format_second_sight_raw_summary(raw), end='')
        return 0

    if args.list:
        path = Path(args.list)
        header, entries = plugin.inspect(path)
        print(f'{path}: {header.magic} / {header.variant} / {len(entries)} entries')
        for i, e in enumerate(entries):
            fid = '' if e.file_id is None else f' id=0x{e.file_id:08X}'
            print(f'{i:5d}  0x{e.offset:08X}  {e.size:10d}{fid}  {e.name}')
        return 0

    if args.extract:
        path = Path(args.extract)
        extract_archive(path, path.name, out, log=print)
        return 0

    if args.extract_all:
        root = Path(args.extract_all)
        files = scan_folder(str(root))
        paks = [f for f in files if is_supported_pak_signature(f.signature)]
        print(f'Detected {len(paks)} supported PAK archive(s).')
        failed = 0
        for i, f in enumerate(paks, 1):
            print(f'[{i}/{len(paks)}] {f.rel_path}')
            try:
                extract_archive(Path(f.path), f.rel_path, out, log=print)
            except Exception as exc:
                failed += 1
                print(f'ERROR: {exc}', file=sys.stderr)
        return 1 if failed else 0

    return -1


def gui() -> int:
    from app.gui import SecondSightExtractorApp
    app = SecondSightExtractorApp()
    app.mainloop()
    return 0


def main() -> int:
    # No args -> GUI; any args -> CLI.
    if len(sys.argv) == 1:
        return gui()
    rc = cli(sys.argv[1:])
    if rc == -1:
        return gui()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
