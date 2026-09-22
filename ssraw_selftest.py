from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app.second_sight_raw import inspect_second_sight_raw, scan_second_sight_raw_folder


def make_header(field08: int, frame_count: int, key_count: int, bones: int) -> bytearray:
    b = bytearray(b"\xff" * 8)
    b += struct.pack("<IIII", field08, frame_count, key_count, bones)
    b += b"\x00" * 12
    b += struct.pack("<I", bones)
    b += b"\x00" * 20
    assert len(b) == 0x3C
    return b


def track_header(flags: int, duration: int, key_count: int) -> bytes:
    return struct.pack("<IIfI", 0, flags, float(duration), key_count) + b"\x00" * 16


def make_animation(path: Path):
    frame_count = 45
    key_count = 12
    flags = [11, 12, 0]
    b = make_header(17, frame_count, key_count, len(flags))
    time_ids = list(range(4, 45, 4))
    assert len(time_ids) == key_count - 1 and time_ids[-1] == frame_count - 1
    b += struct.pack(f"<{len(time_ids)}I", *time_ids)
    for flag in flags:
        b += track_header(flag, frame_count, key_count)
    # v0.6.5 exact payload formulas:
    sizes = {11: 10 * key_count, 12: 6 + 4 * key_count, 0: 18}
    for i, flag in enumerate(flags):
        b += bytes([i + 1]) * sizes[flag]
    path.write_bytes(b)


def make_pose(path: Path, field08: int, bones: int, bytes_per_bone: int):
    b = make_header(field08, 1, 1, bones)
    b += b"\x00" * 8
    for _ in range(bones):
        b += track_header(0, 1, 1)
    for i in range(bones):
        b += bytes([i + 1]) * bytes_per_bone
    path.write_bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix="ss_raw_v065_test_") as td:
        root = Path(td)
        anim = root / "walk.raw"
        static = root / "human_21_bindpose.raw"
        bind = root / "human_23_bindpose.raw"

        make_animation(anim)
        make_pose(static, 17, 2, 18)
        make_pose(bind, 0, 2, 28)

        a = inspect_second_sight_raw(anim)
        assert a.kind_guess == "Animation-like"
        assert a.time_table_valid is True
        assert a.implicit_time_zero is True
        assert a.time_ids == [4,8,12,16,20,24,28,32,36,40,44]
        assert a.track_table_offset == 0x3C + (12 - 1) * 4
        assert a.track_table_valid is True
        assert [t.flags for t in a.track_descriptors] == [11,12,0]
        assert all(t.unknown == 0 and t.reserved_zero for t in a.track_descriptors)
        assert a.payload_size_valid is True
        assert a.payload_size == 120 + 54 + 18
        assert [t.payload_size for t in a.track_descriptors] == [120,54,18]

        s = inspect_second_sight_raw(static)
        assert s.kind_guess == "Static / Pose-like"
        assert s.pretrack_prefix_hex == "0000000000000000"
        assert s.track_table_offset == 0x44
        assert s.track_table_valid is True
        assert s.pose_bytes_per_bone == 18
        assert s.payload_size_valid is True

        b = inspect_second_sight_raw(bind)
        assert b.kind_guess == "Bind Pose-like"
        assert b.track_table_offset == 0x44
        assert b.track_table_valid is True
        assert b.pose_bytes_per_bone == 28
        assert b.payload_size_valid is True

        report = scan_second_sight_raw_folder(root)
        assert report["total_raw_files"] == 3
        assert report["parsed_headers"] == 3
        assert report["animation_time_table_valid"] == 1
        assert report["track_table_valid"] == 3
        assert report["payload_formula_valid"] == 3
        assert report["parse_errors"] == 0

        print("SECOND SIGHT RAW v0.6.5 SELFTEST PASSED")


if __name__ == "__main__":
    main()
