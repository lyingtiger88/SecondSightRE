from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path


KNOWN_SIGNATURES = [
    (b'ANR1', 'Free Radical ANR1 animation / bind pose'),
    (b'P4CK', 'Free Radical PAK / P4CK'),
    (b'P5CK', 'Free Radical PAK / P5CK'),
    (b'P8CK', 'Free Radical PAK / P8CK'),
    (b'DDS ', 'DDS texture'),
    (b'\x89PNG\r\n\x1a\n', 'PNG image'),
    (b'RIFF', 'RIFF container / WAV / AVI'),
    (b'OggS', 'Ogg stream'),
    (b'PK\x03\x04', 'ZIP archive'),
    (b'BM', 'BMP image'),
    (b'ID3', 'MP3/ID3 audio'),
]


@dataclass
class FileInfo:
    path: str
    rel_path: str
    name: str
    ext: str
    size: int
    signature: str = ''
    entropy: float | None = None

    def to_dict(self):
        return asdict(self)


def human_size(size: int) -> str:
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f'{value:.2f} {unit}' if unit != 'B' else f'{int(value)} B'
        value /= 1024.0
    return f'{size} B'


def detect_signature(data: bytes) -> str:
    for magic, label in KNOWN_SIGNATURES:
        if data.startswith(magic):
            return label

    prefix = data[:8]
    printable = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in prefix)
    if prefix and sum(32 <= b <= 126 for b in prefix) >= 3:
        return f'Unknown / magic: {printable}'
    return 'Unknown binary'


def quick_signature(path: Path) -> str:
    try:
        with path.open('rb') as f:
            return detect_signature(f.read(16))
    except OSError:
        return 'Unreadable'


def is_supported_pak_signature(signature: str) -> bool:
    return signature.startswith('Free Radical PAK / P')


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    length = len(data)
    entropy = 0.0
    for count in counts:
        if count:
            p = count / length
            entropy -= p * math.log2(p)
    return entropy


def extract_ascii_strings(data: bytes, min_len: int = 4, limit: int = 300) -> list[str]:
    rx = re.compile(rb'[\x20-\x7e]{%d,}' % min_len)
    out = []
    for m in rx.finditer(data):
        try:
            out.append(m.group().decode('ascii', errors='ignore'))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def hex_dump(data: bytes, width: int = 16, max_bytes: int = 4096) -> str:
    data = data[:max_bytes]
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
        lines.append(f'{offset:08X}  {hex_part:<{width*3}}  {ascii_part}')
    return '\n'.join(lines)


def scan_folder(root: str) -> list[FileInfo]:
    root_path = Path(root)
    results: list[FileInfo] = []
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            p = Path(dirpath) / filename
            try:
                size = p.stat().st_size
                rel = str(p.relative_to(root_path))
                signature = quick_signature(p)
                results.append(FileInfo(
                    path=str(p),
                    rel_path=rel,
                    name=filename,
                    ext=p.suffix.lower(),
                    size=size,
                    signature=signature,
                ))
            except OSError:
                continue
    # Put supported PAKs first, then other files by size.
    results.sort(key=lambda x: (0 if is_supported_pak_signature(x.signature) else 1, -x.size, x.rel_path.lower()))
    return results


def analyze_file(path: str, sample_size: int = 1024 * 1024):
    p = Path(path)
    with p.open('rb') as f:
        data = f.read(sample_size)
    return {
        'signature': detect_signature(data),
        'entropy': shannon_entropy(data),
        'hex': hex_dump(data),
        'strings': extract_ascii_strings(data),
        'sample_size': len(data),
    }
