from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .base import ArchiveEntry, FormatPlugin

MAGICS = {b"P4CK", b"P5CK", b"P8CK"}


class PakFormatError(ValueError):
    pass


@dataclass
class PakHeader:
    magic: str
    directory_offset: int
    directory_size: int
    filenames_offset: int
    file_size: int
    variant: str = ""

    def to_dict(self):
        return self.__dict__.copy()


class FreeRadicalPakPlugin(FormatPlugin):
    """Read-only parser/extractor for Free Radical P4CK/P5CK/P8CK archives."""

    name = "Free Radical PAK (P4CK/P5CK/P8CK)"

    def probe(self, path: Path, head: bytes) -> int:
        return 100 if head[:4] in MAGICS else 0

    def read_header(self, path: Path) -> PakHeader:
        size = path.stat().st_size
        if size < 16:
            raise PakFormatError("File is too small to be a PAK.")
        with path.open("rb") as f:
            magic, doff, dsize, noff = struct.unpack("<4sIII", f.read(16))
        if magic not in MAGICS:
            raise PakFormatError(f"Unsupported PAK magic: {magic!r}")
        if doff >= size:
            raise PakFormatError(f"Directory offset 0x{doff:X} is outside the file.")
        return PakHeader(magic.decode("ascii"), doff, dsize, noff, size)

    def inspect(self, path: Path) -> tuple[PakHeader, list[ArchiveEntry]]:
        h = self.read_header(path)
        with path.open("rb") as f:
            if h.magic == "P4CK":
                entries, h.variant = self._p4(f, h)
            elif h.magic == "P5CK":
                entries, h.variant = self._p5(f, path, h)
            else:
                entries = self._p8(f, h)
                h.variant = "P8CK: 12-byte rows + filename table"
        if not entries:
            raise PakFormatError("No valid archive entries were found.")
        return h, entries

    def list_entries(self, path: Path) -> list[ArchiveEntry]:
        return self.inspect(path)[1]

    @staticmethod
    def _valid(h: PakHeader, off: int, size: int) -> bool:
        return off >= 0 and size >= 0 and off <= h.file_size and off + size <= h.file_size

    @staticmethod
    def _cstring(f: BinaryIO, off: int, limit: int) -> str:
        old = f.tell()
        try:
            f.seek(off)
            out = bytearray()
            while f.tell() < limit and len(out) <= 4096:
                b = f.read(1)
                if not b or b == b"\0":
                    break
                out += b
            return out.decode("utf-8", errors="replace")
        finally:
            f.seek(old)

    @staticmethod
    def _dedupe(name: str, used: set[str], i: int) -> str:
        if name.lower() not in used:
            return name
        p = PurePosixPath(name)
        alt = f"{p.stem or 'entry'}__{i:05d}{p.suffix}"
        return alt if str(p.parent) == "." else f"{p.parent}/{alt}"

    def _p4(self, f: BinaryIO, h: PakHeader) -> tuple[list[ArchiveEntry], str]:
        errors = []
        # Second Sight PC: 16-byte rows then null-terminated filename strings.
        if h.directory_size and h.directory_size % 16 == 0:
            count = h.directory_size // 16
            dend = h.directory_offset + h.directory_size
            nend = dend + h.filenames_offset if h.filenames_offset else h.file_size
            if dend <= h.file_size and nend <= h.file_size:
                try:
                    f.seek(h.directory_offset)
                    rows = [struct.unpack("<IIII", f.read(16)) for _ in range(count)]
                    entries, used = [], set()
                    for i, (name_rel, off, size, extra) in enumerate(rows):
                        if not self._valid(h, off, size):
                            raise PakFormatError(f"Invalid entry {i} range.")
                        name_off = h.directory_offset + name_rel
                        if not (dend <= name_off < nend):
                            raise PakFormatError(f"Invalid filename offset for entry {i}.")
                        name = self._cstring(f, name_off, nend).replace("\\", "/")
                        if not name:
                            raise PakFormatError(f"Empty filename for entry {i}.")
                        name = self._dedupe(name, used, i)
                        used.add(name.lower())
                        entries.append(ArchiveEntry(name=name, offset=off, size=size, extra=extra))
                    return entries, f"P4CK Second Sight PC: {count} x 16-byte rows + filename table"
                except (OSError, struct.error, PakFormatError) as exc:
                    errors.append(str(exc))

        # Related older layout: 48-byte fixed filename + offset/size/extra.
        if h.directory_size and h.directory_size % 60 == 0 and h.directory_offset + h.directory_size <= h.file_size:
            f.seek(h.directory_offset)
            count = h.directory_size // 60
            entries, used = [], set()
            for i in range(count):
                raw = f.read(60)
                if len(raw) != 60:
                    raise PakFormatError("Truncated legacy P4CK directory.")
                raw_name, off, size, extra = struct.unpack("<48sIII", raw)
                if not self._valid(h, off, size):
                    raise PakFormatError(f"Invalid legacy entry {i} range.")
                name = raw_name.split(b"\0", 1)[0].rstrip(b" ").decode("utf-8", errors="replace").replace("\\", "/")
                name = self._dedupe(name or f"entry_{i:05d}.bin", used, i)
                used.add(name.lower())
                entries.append(ArchiveEntry(name=name, offset=off, size=size, extra=extra))
            return entries, f"P4CK legacy: {count} x 60-byte rows"

        raise PakFormatError("Could not parse P4CK archive" + (": " + " | ".join(errors) if errors else "."))

    def _p5(self, f: BinaryIO, path: Path, h: PakHeader) -> tuple[list[ArchiveEntry], str]:
        candidates: list[tuple[int, int, str]] = []
        for stride in (16, 32):
            if h.directory_size and h.directory_size % stride == 0:
                count = h.directory_size // stride
                if h.directory_offset + count * stride <= h.file_size:
                    candidates.append((stride, count, f"directory_size bytes / {stride}"))
            if 0 < h.directory_size <= 2_000_000 and h.directory_offset + h.directory_size * stride <= h.file_size:
                candidates.append((stride, h.directory_size, f"directory_size as count / {stride}"))
        if not candidates:
            raise PakFormatError("No plausible P5CK directory layout fits the archive.")

        best = None
        for stride, count, desc in candidates:
            f.seek(h.directory_offset)
            rows, valid = [], []
            for _ in range(count):
                raw = f.read(stride)
                if len(raw) != stride:
                    break
                row = struct.unpack("<IIII", raw[:16])
                rows.append(row)
                if row[2] > 0 and self._valid(h, row[1], row[2]):
                    valid.append(row)
            score = len(valid) * 20 - (len(rows) - len(valid)) * 2
            item = (score, len(valid), stride, desc, rows, valid)
            if best is None or item[:2] > best[:2]:
                best = item
        if not best or not best[5]:
            raise PakFormatError("No valid P5CK file rows were found.")

        _, _, stride, desc, rows, valid_rows = best
        lookup = self._c2n(path)
        used, entries = set(), []
        for i, (file_id, off, size, extra) in enumerate(valid_rows):
            name = lookup.get(file_id)
            if not name:
                name = f"__hash/{file_id:08X}_{i:05d}{self._guess_ext(f, off, size)}"
            name = self._dedupe(name.replace("\\", "/"), used, i)
            used.add(name.lower())
            entries.append(ArchiveEntry(name=name, offset=off, size=size, file_id=file_id, extra=extra))
        ignored = len(rows) - len(valid_rows)
        variant = f"P5CK {stride}-byte stride ({desc}); names from " + (".c2n" if lookup else "hash IDs")
        if ignored:
            variant += f"; ignored {ignored} non-file row(s)"
        return entries, variant

    @staticmethod
    def _c2n(path: Path) -> dict[int, str]:
        p = path.with_suffix(".c2n")
        if not p.exists():
            return {}
        out = {}
        try:
            for line in p.read_text("utf-8", errors="replace").splitlines():
                m = re.match(r"^(?:0x)?([0-9A-Fa-f]{1,8})\s+(.+?)\s*$", line.strip())
                if m:
                    out[int(m.group(1), 16)] = m.group(2).replace("\\", "/")
        except OSError:
            pass
        return out

    @staticmethod
    def _guess_ext(f: BinaryIO, off: int, size: int) -> str:
        old = f.tell()
        try:
            f.seek(off)
            head = f.read(min(size, 16))
        finally:
            f.seek(old)
        for magic, ext in ((b"DDS ", ".dds"), (b"OggS", ".ogg"), (b"VAGp", ".vag"), (b"RIFF", ".riff"), (b"BM", ".bmp"), (b"\x89PNG\r\n\x1a\n", ".png")):
            if head.startswith(magic):
                return ext
        return ".bin"

    def _p8(self, f: BinaryIO, h: PakHeader) -> list[ArchiveEntry]:
        count = h.directory_size
        if not (0 < count <= 2_000_000):
            raise PakFormatError(f"Implausible P8CK file count: {count}")
        if h.directory_offset + count * 12 > h.file_size:
            raise PakFormatError("P8CK directory exceeds file size.")
        if not (0 < h.filenames_offset < h.file_size):
            raise PakFormatError("Invalid P8CK filename table offset.")

        names = []
        f.seek(h.filenames_offset)
        for _ in range(count):
            buf = bytearray()
            while f.tell() < h.file_size:
                b = f.read(1)
                if not b or b == b"\0":
                    break
                if len(buf) > 4096:
                    raise PakFormatError("Implausibly long P8CK filename.")
                buf += b
            names.append(buf.decode("utf-8", errors="replace"))

        f.seek(h.directory_offset)
        entries, used = [], set()
        for i in range(count):
            raw = f.read(12)
            if len(raw) != 12:
                raise PakFormatError("Truncated P8CK directory.")
            extra, size, off = struct.unpack("<III", raw)
            if not self._valid(h, off, size):
                raise PakFormatError(f"Invalid P8CK entry {i} range.")
            name = self._dedupe(names[i].replace("\\", "/") or f"entry_{i:05d}.bin", used, i)
            used.add(name.lower())
            entries.append(ArchiveEntry(name=name, offset=off, size=size, extra=extra))
        return entries

    def extract_entry(self, path: Path, entry: ArchiveEntry, output_path: Path) -> None:
        size = path.stat().st_size
        if entry.offset < 0 or entry.size < 0 or entry.offset + entry.size > size:
            raise PakFormatError("Entry range points outside archive.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as src, output_path.open("wb") as dst:
            src.seek(entry.offset)
            remaining = entry.size
            while remaining:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise PakFormatError("Unexpected end of archive during extraction.")
                dst.write(chunk)
                remaining -= len(chunk)
