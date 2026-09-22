from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app.extractor import extract_archive
from app.plugins import FreeRadicalPakPlugin


def make_second_sight_p4(path: Path):
    payloads = [
        ("anim/data/g2/walk.raw", b"ANIMATION_BYTES_123"),
        ("anim/data/g2/idle.raw", b"IDLE_BYTES_456789"),
    ]

    # Second Sight PC P4CK: 16-byte header, file payloads, then 16-byte
    # directory entries followed by a null-terminated filename string table.
    blob = bytearray(b"\0" * 16)
    rows = []
    for name, data in payloads:
        off = len(blob)
        blob.extend(data)
        # align payloads to 16 bytes like the original writer commonly does
        while len(blob) % 16:
            blob.append(0)
        rows.append([name, off, len(data), 0])

    directory_offset = len(blob)
    directory_size = len(rows) * 16

    # Build names after the directory. Each entry stores its name offset
    # relative to directory_offset, not relative to the filename table itself.
    name_blob = bytearray()
    for row in rows:
        row.append(directory_size + len(name_blob))
        name_blob.extend(row[0].encode("utf-8") + b"\0")

    for name, off, size, extra, name_rel in rows:
        blob.extend(struct.pack("<IIII", name_rel, off, size, extra))
    blob.extend(name_blob)

    struct.pack_into(
        "<4sIII", blob, 0, b"P4CK", directory_offset, directory_size, len(name_blob)
    )
    path.write_bytes(blob)
    return payloads


def make_legacy_p4(path: Path):
    payloads = [("levels/test.raw", b"HELLO_LEVEL_DATA"), ("anim/walk.raw", b"ANIMATION_BYTES")]
    cursor = 0x800
    rows = []
    blob = bytearray(b"\0" * cursor)
    for name, data in payloads:
        off = cursor
        blob.extend(data)
        cursor += len(data)
        rows.append((name, off, len(data), 0))
    directory_offset = cursor
    for name, off, size, extra in rows:
        raw_name = name.encode("utf-8")[:48].ljust(48, b"\0")
        blob.extend(struct.pack("<48sIII", raw_name, off, size, extra))
    struct.pack_into("<4sIII", blob, 0, b"P4CK", directory_offset, len(rows) * 60, 0)
    path.write_bytes(blob)
    return payloads


def verify(pak: Path, expected, out: Path, rel: str):
    plugin = FreeRadicalPakPlugin()
    header, entries = plugin.inspect(pak)
    assert header.magic == "P4CK"
    assert len(entries) == len(expected)
    extract_archive(pak, rel, out)
    base = out / "PAK_Extracted" / Path(rel).with_suffix("")
    for name, data in expected:
        assert (base / name).read_bytes() == data
    return header.variant


def main():
    with tempfile.TemporaryDirectory(prefix="ss_extractor_test_") as td:
        root = Path(td)
        out = root / "out"

        ss_pak = root / "secondsight.pak"
        ss_expected = make_second_sight_p4(ss_pak)
        ss_variant = verify(ss_pak, ss_expected, out, "pak/secondsight.pak")
        assert "Second Sight PC" in ss_variant

        legacy_pak = root / "legacy.pak"
        legacy_expected = make_legacy_p4(legacy_pak)
        legacy_variant = verify(legacy_pak, legacy_expected, out, "pak/legacy.pak")
        assert "legacy" in legacy_variant.lower()

        print("SELFTEST PASSED")
        print("  - Second Sight PC P4CK 16-byte rows + filename table: byte-for-byte OK")
        print("  - Legacy P4CK 60-byte rows: byte-for-byte OK")


if __name__ == "__main__":
    main()
