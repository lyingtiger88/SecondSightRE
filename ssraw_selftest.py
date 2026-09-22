from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app.second_sight_raw import inspect_second_sight_raw, scan_second_sight_raw_folder


def make_header(field08: int, frame_count: int, id_count: int, bones: int) -> bytearray:
    b = bytearray(b"\xff" * 8)
    b += struct.pack("<IIII", field08, frame_count, id_count, bones)
    b += b"\x00" * 12
    b += struct.pack("<I", bones)
    b += b"\x00" * 20
    assert len(b) == 0x3C
    return b


def make_animation(path: Path, bones: int = 21):
    frame_count = 45
    ids = list(range(4, 45, 4)) + [0]
    assert len(ids) == 12 and ids[-2] == frame_count - 1
    b = make_header(17, frame_count, len(ids), bones)
    b += struct.pack(f"<{len(ids)}I", *ids)
    for i in range(bones):
        mode = 8 if i == 0 else (11 if i == 1 else 12)
        b += struct.pack("<IfI", mode, float(frame_count), len(ids))
        b += b"\x00" * 20
    b += bytes(range(128))
    path.write_bytes(b)


def make_pose(path: Path, field08: int, bones: int, bytes_per_bone: int):
    b = make_header(field08, 1, 1, bones)
    b += b"\x00" * 8
    for i in range(bones):
        rec = bytearray(bytes_per_bone)
        rec[0:4] = struct.pack("<I", i)
        b += rec
    path.write_bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix="ss_raw_v063_test_") as td:
        root = Path(td)
        anim = root / "walk.raw"
        static = root / "human_21_bindpose.raw"
        bind = root / "human_23_bindpose.raw"

        make_animation(anim)
        make_pose(static, 17, 21, 50)
        make_pose(bind, 0, 23, 60)

        a = inspect_second_sight_raw(anim)
        assert a.kind_guess == "Animation-like"
        assert a.bone_count == 21 and a.bone_count_mirror == 21
        assert a.time_table_valid is True
        assert a.time_ids[-2:] == [44, 0]
        assert a.track_table_valid is True
        assert len(a.track_descriptors) == 21
        assert a.track_descriptors[0].mode == 8
        assert a.track_descriptors[0].duration == 45.0
        assert a.track_descriptors[0].key_count == 12
        assert a.payload_size == 128

        s = inspect_second_sight_raw(static)
        assert s.kind_guess == "Static / Pose-like"
        assert s.pose_data_offset == 0x44
        assert s.pose_record_size_guess == 50
        assert s.pose_size_formula_exact is True

        b = inspect_second_sight_raw(bind)
        assert b.kind_guess == "Bind Pose-like"
        assert b.pose_record_size_guess == 60
        assert b.pose_size_formula_exact is True

        report = scan_second_sight_raw_folder(root)
        assert report["total_raw_files"] == 3
        assert report["parsed_headers"] == 3
        assert report["animation_time_table_valid"] == 1
        assert report["animation_track_table_valid"] == 1
        assert report["parse_errors"] == 0
        assert report["sentinel_mismatch"] == 0
        assert report["bone_count_mirror_mismatch"] == 0

        print("SECOND SIGHT RAW v0.6.3 SELFTEST PASSED")


if __name__ == "__main__":
    main()
