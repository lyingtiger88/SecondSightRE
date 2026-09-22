from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .plugins import FreeRadicalPakPlugin


_WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *(f'COM{i}' for i in range(1, 10)),
    *(f'LPT{i}' for i in range(1, 10)),
}
_INVALID_COMPONENT = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_relative_path(name: str, fallback: str = 'entry.bin') -> Path:
    """Turn an archive path into a safe platform path below an output root."""
    name = (name or fallback).replace('\\', '/')
    p = PurePosixPath(name)
    safe_parts: list[str] = []
    for raw in p.parts:
        if raw in ('', '.', '/', '\\'):
            continue
        if raw == '..':
            continue
        # Strip drive-ish components and filesystem-hostile characters.
        part = raw.replace(':', '_')
        part = _INVALID_COMPONENT.sub('_', part).rstrip(' .')
        if not part:
            part = '_'
        stem_upper = part.split('.', 1)[0].upper()
        if stem_upper in _WINDOWS_RESERVED:
            part = '_' + part
        safe_parts.append(part)
    if not safe_parts:
        safe_parts = [fallback]
    return Path(*safe_parts)


def archive_output_dir(output_root: Path, archive_rel_path: str) -> Path:
    rel = safe_relative_path(archive_rel_path, 'archive.pak')
    # Preserve folder hierarchy, but make each .pak a directory named after the archive.
    if rel.suffix.lower() == '.pak':
        rel = rel.with_suffix('')
    return output_root / 'PAK_Extracted' / rel


def extract_archive(
    archive_path: Path,
    archive_rel_path: str,
    output_root: Path,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    plugin = FreeRadicalPakPlugin()
    header, entries = plugin.inspect(archive_path)
    out_dir = archive_output_dir(output_root, archive_rel_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    for i, entry in enumerate(entries, 1):
        rel = safe_relative_path(entry.name, f'entry_{i:05d}.bin')
        dest = out_dir / rel
        # defense-in-depth: resolved target must stay under resolved archive output dir.
        try:
            dest.resolve().relative_to(out_dir.resolve())
        except ValueError as exc:
            raise ValueError(f'Unsafe output path derived from {entry.name!r}') from exc

        plugin.extract_entry(archive_path, entry, dest)
        item = entry.to_dict()
        item['output_path'] = str(rel).replace(os.sep, '/')
        manifest_entries.append(item)
        if log:
            log(f'  [{i}/{len(entries)}] {entry.name} -> {item["output_path"]}')
        if progress:
            progress(i, len(entries))

    manifest = {
        'source_archive': str(archive_path),
        'archive_relative_path': archive_rel_path,
        'header': header.to_dict(),
        'entry_count': len(entries),
        'entries': manifest_entries,
    }
    (out_dir / '__archive_manifest.json').write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    return manifest
