from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from app.animation_ir import build_animation_ir
from app.second_sight_raw import TRACK_PAYLOAD_SIZE_FORMULAS


def header(frame_count: int, key_count: int, bones: int) -> bytearray:
    b = bytearray(b"\xff" * 8)
    b += struct.pack("<IIII", 17, frame_count, key_count, bones)
    b += b"\x00" * 12
    b += struct.pack("<I", bones)
    b += b"\x00" * 20
    assert len(b) == 0x3C
    return b


def track_header(flags: int, frame_count: int, key_count: int) -> bytes:
    return struct.pack("<IIfI", 0, flags, float(frame_count), key_count) + b"\x00" * 16


def enc_pos6(p):
    vals = []
    for v in p:
        x = int(round((((v / 4.0) + 1.0) * 0.5) * 65535.0))
        vals.append(max(0, min(65535, x)))
    return struct.pack("<3H", *vals)


def enc_q6(q):
    vals = []
    for v in q[:3]:
        x = int(round(((v + 1.0) * 0.5) * 65535.0))
        vals.append(max(0, min(65535, x)))
    return struct.pack("<3H", *vals)


def enc_q4(q):
    q = list(q)
    n = math.sqrt(sum(v * v for v in q))
    q = [v / n for v in q]
    omitted = max(range(4), key=lambda i: abs(q[i]))
    if q[omitted] < 0:
        q = [-v for v in q]
    stored = [q[i] for i in range(4) if i != omitted]
    packed = omitted
    inv = math.sqrt(2.0)
    for i, value in enumerate(stored):
        normalized = max(-1.0, min(1.0, value * inv))
        code = int(round(((normalized + 1.0) * 0.5) * 1023.0))
        packed |= (code & 0x3FF) << (2 + i * 10)
    return struct.pack("<I", packed)


def close_vec(a, b, eps=3e-3):
    return all(abs(x - y) <= eps for x, y in zip(a, b))


def make_raw(path: Path):
    frame_count = 5
    key_count = 3
    flags = [0, 11, 12, 13]
    b = header(frame_count, key_count, len(flags))
    b += struct.pack("<2I", 2, 4)
    for flag in flags:
        b += track_header(flag, frame_count, key_count)

    static_p = (0.25, -0.5, 1.0)
    static_q = (0.0, 0.0, 0.0, 1.0)
    b += struct.pack("<3f", *static_p) + enc_q6(static_q)

    qseq = [
        (0.0, 0.0, 0.0, 1.0),
        (0.1, 0.0, 0.0, math.sqrt(0.99)),
        (0.0, 0.2, 0.0, math.sqrt(0.96)),
    ]
    pseq = [(0.0, 1.0, 0.0), (0.1, 1.1, 0.0), (0.2, 1.2, 0.1)]

    for p, q in zip(pseq, qseq):
        b += enc_pos6(p) + enc_q4(q)

    b += enc_pos6(static_p)
    for q in qseq:
        b += enc_q4(q)

    b += enc_q4(static_q)
    for p in pseq:
        b += enc_pos6(p)

    path.write_bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix="ss_anim_ir_test_") as td:
        path = Path(td) / "synthetic.raw"
        make_raw(path)
        ir = build_animation_ir(path)

        assert ir["schema"] == "secondsight.animation_ir.v1"
        assert ir["key_times"] == [0, 2, 4]
        assert ir["bone_count"] == 4
        assert [t["flags"] for t in ir["tracks"]] == [0, 11, 12, 13]

        t0, t11, t12, t13 = ir["tracks"]
        assert close_vec(t0["samples"][0]["position"], (0.25, -0.5, 1.0), 1e-6)
        assert close_vec(t11["samples"][1]["position"], (0.1, 1.1, 0.0))
        assert close_vec(t12["samples"][2]["position"], (0.25, -0.5, 1.0))
        assert close_vec(t13["samples"][2]["position"], (0.2, 1.2, 0.1))

        for track in ir["tracks"]:
            for sample in track["samples"]:
                q = sample["rotation"]
                norm = math.sqrt(sum(v * v for v in q))
                assert abs(norm - 1.0) < 1e-5

        print("SECOND SIGHT ANIMATION IR SELFTEST PASSED")


if __name__ == "__main__":
    main()
