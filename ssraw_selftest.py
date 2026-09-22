from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app.second_sight_raw import inspect_second_sight_raw, scan_second_sight_raw_folder


def make_raw(path: Path, field08: int, field0c: int, field10: int, bones: int, sequence: bool = True):
    b = bytearray(b"\xff" * 8)
    b += struct.pack("<IIII", field08, field0c, field10, bones)
    b += b"\x00" * 12
    b += struct.pack("<I", bones)
    b += b"\x00" * 20
    words = [(i + 1) * 4 for i in range(bones + 8)] if sequence else [0] * (bones + 8)
    b += struct.pack(f"<{len(words)}I", *words)
    b += b"\x00" * 64
    path.write_bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix="ss_raw_header_test_") as td:
        root = Path(td)
        anim = root / "walk.raw"
        bind = root / "human_23_bindpose.raw"
        make_raw(anim, 17, 120, 31, 21, True)
        make_raw(bind, 0, 1, 1, 23, False)

        a = inspect_second_sight_raw(anim)
        assert a.sentinel_ok
        assert a.bone_count == 21 and a.bone_count_mirror == 21
        assert a.kind_guess == "Animation-like"
        assert a.sequential_table_prefix >= 21
        assert abs(a.sample_spacing_guess - 4.0) < 1e-6

        b = inspect_second_sight_raw(bind)
        assert b.bone_count == 23 and b.bone_count_mirror == 23
        assert b.kind_guess == "Bind Pose-like"

        report = scan_second_sight_raw_folder(root)
        assert report["total_raw_files"] == 2
        assert report["parsed_headers"] == 2
        assert report["sentinel_mismatch"] == 0
        assert report["bone_count_mirror_mismatch"] == 0

        print("SECOND SIGHT RAW HEADER SELFTEST PASSED")


if __name__ == "__main__":
    main()
