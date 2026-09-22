from __future__ import annotations

import math
import struct
import tempfile
from pathlib import Path

from app.raw_animation import inspect_raw_animation, probe_animation_layout_bytes, scan_raw_folder


def qpack(x, y, z, w):
    def one(v):
        v = max(-1.0, min(1.0, v))
        return int(round((v + 1.0) * 65535.0 / 2.0))
    return struct.pack('<4H', *(one(v) for v in (x, y, z, w)))


def root_frame(pos, q=(0.0, 0.0, 0.0, 1.0)):
    return struct.pack('<3f', *pos) + qpack(*q)


def make_header(num_ids, tracks, version=1):
    b = bytearray(b'\0' * 88)
    b[0:5] = b'ANR1\0'
    struct.pack_into('<I', b, 5, version)
    struct.pack_into('<IIII', b, 48, 0x11111111, 0x22222222, num_ids, len(tracks))
    for i in range(num_ids):
        b.extend(struct.pack('<I', 1000 + i))
    for unk, flags, nframes, nkeys in tracks:
        b.extend(struct.pack('<IIfI', unk, flags, nframes, nkeys))
        b.extend(b'\0' * 16)
    return b


def make_animation(path: Path):
    n = 3
    tracks = [
        (10, 8, 3.0, 3),
        (11, 2, 3.0, 3),
        (12, 2, 3.0, 3),
    ]
    b = make_header(n, tracks)
    for i in range(n):
        b.extend(root_frame((float(i), 1.0, 2.0), (0.0, 0.0, 0.0, 1.0)))
    for base in (10.0, 20.0):
        b.extend(struct.pack('<3f', base, base + 1, base + 2))
        for i in range(n):
            b.extend(qpack(0.0, 0.0, 0.0, 1.0))
    path.write_bytes(b)


def make_bind(path: Path):
    tracks = [(i, 0, 1.0, 1) for i in range(4)]
    b = make_header(0, tracks, version=2)
    b.extend(b'\xAA' * 8)
    for i in range(4):
        b.extend(root_frame((i * 1.0, i * 2.0, i * 3.0), (0.0, 0.0, 0.0, 1.0)))
    path.write_bytes(b)


def main():
    with tempfile.TemporaryDirectory(prefix='ss_anr1_test_') as td:
        td = Path(td)
        ap = td / 'walk.raw'
        bp = td / 'human_bindpose.raw'
        make_animation(ap)
        make_bind(bp)

        a = inspect_raw_animation(ap)
        assert a.kind == 'Animation'
        assert a.num_ids == 3 and a.num_bones == 3
        assert a.flag_counts == {8: 1, 2: 2}
        assert len(a.tracks[0].root_frames) == 3
        assert len(a.tracks[1].rotations) == 3
        assert a.trailing_bytes == 0
        assert abs(a.tracks[0].root_frames[0].rotation.norm - 1.0) < 0.001

        b = inspect_raw_animation(bp)
        assert b.kind == 'Bind Pose'
        assert b.num_bones == 4 and b.bind_padding == 8
        assert all(t.bind_frame is not None for t in b.tracks)
        assert b.trailing_bytes == 0

        # Same known layout with a different magic: profiler should still identify it.
        up = td / 'secondsight_variant.raw'
        ub = bytearray(ap.read_bytes())
        ub[:5] = b'SSAN\0'
        up.write_bytes(ub)
        probe = probe_animation_layout_bytes(bytes(ub))
        assert probe['candidate'] and probe['score'] >= 75
        u = inspect_raw_animation(up, allow_unknown_magic=True)
        assert u.num_bones == 3 and u.kind == 'Animation'

        report = scan_raw_folder(td)
        assert report['total_raw_files'] == 3
        assert report['layout_candidates'] == 3
        assert report['parsed_layout'] == 3
        assert report['known_anr1_magic'] == 2

        print('RAW SELFTEST PASSED')
        print('  - ANR1 animation: header + IDs + root/child tracks + packed quaternions OK')
        print('  - ANR1 bind pose: track metadata + 8-byte pad + bind frames OK')
        print('  - Unknown-magic layout probe + folder profiler OK')


if __name__ == '__main__':
    main()
