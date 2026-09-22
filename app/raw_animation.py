from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ANR1_MAGIC = b"ANR1"
KNOWN_TRACK_FLAGS = {0, 2, 8}
HEADER_CORE_END = 64
POST_HEADER_PADDING = 24
IDS_OFFSET = HEADER_CORE_END + POST_HEADER_PADDING  # 0x58 / 88
TRACK_SIZE = 32
ROOT_FRAME_SIZE = 20
QUAT_SIZE = 8

ROOT_BONE_FLAG = 8
CHILD_BONE_FLAG = 2
BIND_BONE_FLAG = 0


class RawAnimationError(ValueError):
    pass


@dataclass
class Quaternion16:
    x: float
    y: float
    z: float
    w: float
    raw_hex: str = ""

    @property
    def norm(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z + self.w * self.w)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["norm"] = self.norm
        return d


@dataclass
class RootFrame:
    x: float
    y: float
    z: float
    rotation: Quaternion16

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": [self.x, self.y, self.z],
            "rotation": self.rotation.to_dict(),
        }


@dataclass
class BoneTrack:
    index: int
    unknown: int
    flags: int
    num_frames: float
    num_keyframes: int
    reserved_hex: str
    payload_offset: int | None = None
    payload_size: int | None = None
    decoded_kind: str = "metadata-only"
    position: tuple[float, float, float] | None = None
    root_frames: list[RootFrame] = field(default_factory=list)
    rotations: list[Quaternion16] = field(default_factory=list)
    bind_frame: RootFrame | None = None

    @property
    def flag_name(self) -> str:
        return {
            BIND_BONE_FLAG: "Bind",
            CHILD_BONE_FLAG: "Child",
            ROOT_BONE_FLAG: "Root",
        }.get(self.flags, f"Unknown({self.flags})")

    def to_dict(self, include_samples: bool = False) -> dict[str, Any]:
        out = {
            "index": self.index,
            "unknown": self.unknown,
            "flags": self.flags,
            "flag_name": self.flag_name,
            "num_frames": self.num_frames,
            "num_keyframes": self.num_keyframes,
            "reserved_hex": self.reserved_hex,
            "payload_offset": self.payload_offset,
            "payload_size": self.payload_size,
            "decoded_kind": self.decoded_kind,
            "position": list(self.position) if self.position else None,
        }
        if include_samples:
            out["root_frames"] = [x.to_dict() for x in self.root_frames]
            out["rotations"] = [x.to_dict() for x in self.rotations]
            out["bind_frame"] = self.bind_frame.to_dict() if self.bind_frame else None
        else:
            out["root_frame_count"] = len(self.root_frames)
            out["rotation_count"] = len(self.rotations)
            out["has_bind_frame"] = self.bind_frame is not None
        return out


@dataclass
class RawAnimation:
    path: str
    file_size: int
    magic4: str
    magic5_hex: str
    version: int
    unknown1: int
    unknown2: int
    num_ids: int
    num_bones: int
    ids: list[int]
    tracks: list[BoneTrack]
    ids_offset: int
    tracks_offset: int
    payload_offset: int
    payload_end: int
    trailing_bytes: int
    kind: str
    bind_padding: int = 0
    decode_complete: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def flag_counts(self) -> dict[int, int]:
        return dict(Counter(t.flags for t in self.tracks))

    @property
    def max_num_frames(self) -> float:
        values = [t.num_frames for t in self.tracks if math.isfinite(t.num_frames)]
        return max(values) if values else 0.0

    @property
    def max_num_keyframes(self) -> int:
        return max((t.num_keyframes for t in self.tracks), default=0)

    def to_dict(self, include_samples: bool = False) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_size": self.file_size,
            "magic": self.magic4,
            "magic5_hex": self.magic5_hex,
            "version": self.version,
            "unknown1": self.unknown1,
            "unknown2": self.unknown2,
            "num_ids": self.num_ids,
            "num_bones": self.num_bones,
            "kind": self.kind,
            "ids_offset": self.ids_offset,
            "tracks_offset": self.tracks_offset,
            "payload_offset": self.payload_offset,
            "payload_end": self.payload_end,
            "trailing_bytes": self.trailing_bytes,
            "bind_padding": self.bind_padding,
            "decode_complete": self.decode_complete,
            "flag_counts": self.flag_counts,
            "max_num_frames": self.max_num_frames,
            "max_num_keyframes": self.max_num_keyframes,
            "ids": self.ids,
            "tracks": [t.to_dict(include_samples=include_samples) for t in self.tracks],
            "warnings": self.warnings,
        }


def _need(data: bytes, offset: int, size: int, what: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise RawAnimationError(
            f"{what} exceeds file bounds: need 0x{offset:X}+0x{size:X}, file is 0x{len(data):X}."
        )


def _u32(data: bytes, offset: int) -> int:
    _need(data, offset, 4, "u32")
    return struct.unpack_from("<I", data, offset)[0]


def _f32(data: bytes, offset: int) -> float:
    _need(data, offset, 4, "f32")
    return struct.unpack_from("<f", data, offset)[0]


def _decode_quaternion(data: bytes, offset: int) -> Quaternion16:
    _need(data, offset, QUAT_SIZE, "quaternion")
    raw = data[offset:offset + QUAT_SIZE]
    vals = struct.unpack("<4H", raw)
    # Free Radical's packed quaternion mapping used by TS-ReSplit:
    # unsigned 16-bit [0..65535] -> float [-1..1].
    scale = 2.0 / 65535.0
    q = [v * scale - 1.0 for v in vals]
    return Quaternion16(q[0], q[1], q[2], q[3], raw.hex())


def _decode_root_frame(data: bytes, offset: int) -> RootFrame:
    _need(data, offset, ROOT_FRAME_SIZE, "root/bind frame")
    x, y, z = struct.unpack_from("<3f", data, offset)
    return RootFrame(x, y, z, _decode_quaternion(data, offset + 12))


def _plausible_counts(num_ids: int, num_bones: int, file_size: int) -> None:
    if num_bones <= 0 or num_bones > 4096:
        raise RawAnimationError(f"Implausible bone/track count: {num_bones}")
    if num_ids > 1_000_000:
        raise RawAnimationError(f"Implausible ID/sample count: {num_ids}")
    minimum = IDS_OFFSET + num_ids * 4 + num_bones * TRACK_SIZE
    if minimum > file_size:
        raise RawAnimationError(
            f"Header counts require at least {minimum} bytes, but file has only {file_size}."
        )


def _ascii_preview(data: bytes) -> str:
    return ''.join(chr(b) if 32 <= b <= 126 else '.' for b in data)


def profile_raw_header(path: str | Path, sample_len: int = 96) -> dict[str, Any]:
    path = Path(path)
    data = path.read_bytes()
    head = data[:sample_len]
    u16 = []
    for off in range(0, min(len(head), 64) - 1, 2):
        u16.append({'offset': off, 'value': struct.unpack_from('<H', head, off)[0]})
    u32 = []
    f32 = []
    for off in range(0, min(len(head), 64) - 3, 4):
        u32.append({'offset': off, 'value': struct.unpack_from('<I', head, off)[0]})
        fv = struct.unpack_from('<f', head, off)[0]
        f32.append({'offset': off, 'value': fv if math.isfinite(fv) else None})
    probe = probe_animation_layout_bytes(data)
    return {
        'path': str(path),
        'file_size': len(data),
        'prefix4_hex': data[:4].hex(),
        'prefix5_hex': data[:5].hex(),
        'prefix8_hex': data[:8].hex(),
        'prefix16_hex': data[:16].hex(),
        'prefix16_ascii': _ascii_preview(data[:16]),
        'header96_hex': head.hex(),
        'u16_le_0_63': u16,
        'u32_le_0_63': u32,
        'f32_le_0_63': f32,
        'layout_probe': probe,
    }


def probe_animation_layout_bytes(data: bytes) -> dict[str, Any]:
    """Score the TS2-style Free Radical animation layout without trusting magic.

    This is intentionally a profiler, not proof that a file is an animation.
    It lets us test whether Second Sight retained the same field offsets while
    changing the signature/version family.
    """
    out: dict[str, Any] = {
        'candidate': False,
        'score': 0,
        'reason': [],
        'version_at_5': None,
        'unknown1_at_48': None,
        'unknown2_at_52': None,
        'num_ids_at_56': None,
        'num_bones_at_60': None,
        'minimum_size': None,
        'known_flag_ratio': None,
        'flag_counts': {},
    }
    if len(data) < IDS_OFFSET:
        out['reason'].append('file shorter than 0x58 header/ID offset')
        return out

    try:
        version = _u32(data, 5)
        unk1 = _u32(data, 48)
        unk2 = _u32(data, 52)
        num_ids = _u32(data, 56)
        num_bones = _u32(data, 60)
    except Exception as exc:
        out['reason'].append(str(exc))
        return out

    out.update({
        'version_at_5': version,
        'unknown1_at_48': unk1,
        'unknown2_at_52': unk2,
        'num_ids_at_56': num_ids,
        'num_bones_at_60': num_bones,
    })

    score = 0
    if 0 < num_bones <= 512:
        score += 25
    else:
        out['reason'].append(f'num_bones {num_bones} outside 1..512')
    if 0 <= num_ids <= 200000:
        score += 15
    else:
        out['reason'].append(f'num_ids {num_ids} implausible')

    minimum = IDS_OFFSET + num_ids * 4 + num_bones * TRACK_SIZE if num_bones <= 4096 and num_ids <= 1000000 else None
    out['minimum_size'] = minimum
    if minimum is not None and minimum <= len(data):
        score += 25
    else:
        out['reason'].append('counts do not fit file size')

    if version <= 0x10000:
        score += 5

    if minimum is not None and minimum <= len(data) and 0 < num_bones <= 512:
        tracks_offset = IDS_OFFSET + num_ids * 4
        flags = []
        finite_frames = 0
        sane_keys = 0
        for i in range(num_bones):
            off = tracks_offset + i * TRACK_SIZE
            try:
                _, fl, nf, nk = struct.unpack_from('<IIfI', data, off)
            except struct.error:
                break
            flags.append(fl)
            if math.isfinite(nf) and abs(nf) < 10_000_000:
                finite_frames += 1
            if nk <= 10_000_000:
                sane_keys += 1
        if len(flags) == num_bones:
            counts = Counter(flags)
            out['flag_counts'] = {str(k): v for k, v in sorted(counts.items())}
            ratio = sum(1 for x in flags if x in KNOWN_TRACK_FLAGS) / max(1, len(flags))
            out['known_flag_ratio'] = ratio
            score += int(round(ratio * 20))
            if finite_frames == num_bones:
                score += 5
            if sane_keys == num_bones:
                score += 5
            if ratio < 0.5:
                out['reason'].append(f'only {ratio:.1%} track flags are 0/2/8')
        else:
            out['reason'].append('track table truncated')

    out['score'] = score
    out['candidate'] = score >= 75
    if out['candidate']:
        out['reason'].append('layout is plausible enough for experimental parse')
    return out


def inspect_raw_animation(path: str | Path, decode_payload: bool = True, allow_unknown_magic: bool = False) -> RawAnimation:
    path = Path(path)
    data = path.read_bytes()
    if len(data) < IDS_OFFSET:
        raise RawAnimationError(f"File is too small for an ANR1 header ({len(data)} bytes).")
    if data[:4] != ANR1_MAGIC:
        got = data[:4].decode("ascii", errors="replace")
        if not allow_unknown_magic:
            raise RawAnimationError(f"Unsupported RAW magic {got!r}; expected 'ANR1'.")
        probe = probe_animation_layout_bytes(data)
        if not probe.get('candidate'):
            raise RawAnimationError(
                f"Unknown RAW magic {got!r} and TS2-style layout probe scored {probe.get('score', 0)}/100."
            )

    # Public Free Radical research reads five magic chars first, then an unaligned
    # little-endian version u32 at offset 5. Keep byte 4 visible because variants
    # may use NUL or another marker there.
    version = _u32(data, 5)
    unknown1 = _u32(data, 48)
    unknown2 = _u32(data, 52)
    num_ids = _u32(data, 56)
    num_bones = _u32(data, 60)
    _plausible_counts(num_ids, num_bones, len(data))

    ids_offset = IDS_OFFSET
    tracks_offset = ids_offset + num_ids * 4
    payload_offset = tracks_offset + num_bones * TRACK_SIZE

    ids = list(struct.unpack_from(f"<{num_ids}I", data, ids_offset)) if num_ids else []
    tracks: list[BoneTrack] = []
    for i in range(num_bones):
        off = tracks_offset + i * TRACK_SIZE
        _need(data, off, TRACK_SIZE, f"track {i}")
        unknown, flags, num_frames, num_keyframes = struct.unpack_from("<IIfI", data, off)
        tracks.append(BoneTrack(
            index=i,
            unknown=unknown,
            flags=flags,
            num_frames=num_frames,
            num_keyframes=num_keyframes,
            reserved_hex=data[off + 16:off + 32].hex(),
        ))

    flags = [t.flags for t in tracks]
    if flags and all(f == BIND_BONE_FLAG for f in flags):
        kind = "Bind Pose"
    elif any(f == ROOT_BONE_FLAG for f in flags) or any(f == CHILD_BONE_FLAG for f in flags):
        kind = "Animation"
    else:
        kind = "ANR1 / Unknown track flags"

    result = RawAnimation(
        path=str(path),
        file_size=len(data),
        magic4=data[:4].decode("ascii", errors="replace"),
        magic5_hex=data[:5].hex(),
        version=version,
        unknown1=unknown1,
        unknown2=unknown2,
        num_ids=num_ids,
        num_bones=num_bones,
        ids=ids,
        tracks=tracks,
        ids_offset=ids_offset,
        tracks_offset=tracks_offset,
        payload_offset=payload_offset,
        payload_end=payload_offset,
        trailing_bytes=len(data) - payload_offset,
        kind=kind,
    )

    if decode_payload:
        _decode_payload(data, result)
    return result


def _decode_payload(data: bytes, result: RawAnimation) -> None:
    cursor = result.payload_offset

    if result.kind == "Bind Pose":
        # TS-ReSplit observes 8 bytes before bind-pose frame data. To make the
        # inspector useful on variants, choose 8 only when it fits cleanly; fall
        # back to 0 if that is the only valid layout.
        expected = result.num_bones * ROOT_FRAME_SIZE
        fits8 = cursor + 8 + expected <= len(data)
        fits0 = cursor + expected <= len(data)
        if fits8:
            result.bind_padding = 8
            cursor += 8
        elif fits0:
            result.bind_padding = 0
            result.warnings.append("Bind pose decoded without the usual 8-byte pre-frame padding.")
        else:
            raise RawAnimationError("Bind-pose frame payload does not fit inside the file.")

        for t in result.tracks:
            start = cursor
            t.bind_frame = _decode_root_frame(data, cursor)
            cursor += ROOT_FRAME_SIZE
            t.payload_offset = start
            t.payload_size = ROOT_FRAME_SIZE
            t.decoded_kind = "bind-frame"

    else:
        for t in result.tracks:
            start = cursor
            if t.flags == ROOT_BONE_FLAG:
                bytes_needed = result.num_ids * ROOT_FRAME_SIZE
                if cursor + bytes_needed > len(data):
                    result.decode_complete = False
                    result.warnings.append(
                        f"Root track {t.index} expected {result.num_ids} samples but payload is truncated."
                    )
                    break
                t.root_frames = [_decode_root_frame(data, cursor + i * ROOT_FRAME_SIZE) for i in range(result.num_ids)]
                cursor += bytes_needed
                t.decoded_kind = "root-track"
            elif t.flags == CHILD_BONE_FLAG:
                bytes_needed = 12 + result.num_ids * QUAT_SIZE
                if cursor + bytes_needed > len(data):
                    result.decode_complete = False
                    result.warnings.append(
                        f"Child track {t.index} expected {result.num_ids} rotations but payload is truncated."
                    )
                    break
                t.position = struct.unpack_from("<3f", data, cursor)
                cursor += 12
                t.rotations = [_decode_quaternion(data, cursor + i * QUAT_SIZE) for i in range(result.num_ids)]
                cursor += result.num_ids * QUAT_SIZE
                t.decoded_kind = "child-track"
            elif t.flags == BIND_BONE_FLAG:
                if cursor + ROOT_FRAME_SIZE > len(data):
                    result.decode_complete = False
                    result.warnings.append(f"Mixed bind track {t.index} is truncated.")
                    break
                t.bind_frame = _decode_root_frame(data, cursor)
                cursor += ROOT_FRAME_SIZE
                t.decoded_kind = "mixed-bind-frame"
                result.warnings.append(
                    f"Track {t.index} uses bind flag 0 inside a non-bind animation; decoded conservatively."
                )
            else:
                result.decode_complete = False
                result.warnings.append(
                    f"Track {t.index} has unsupported flag {t.flags}; payload decoding stopped at 0x{cursor:X}."
                )
                break
            t.payload_offset = start
            t.payload_size = cursor - start

    result.payload_end = cursor
    result.trailing_bytes = len(data) - cursor
    if result.trailing_bytes < 0:
        raise RawAnimationError("Decoded payload exceeded file size.")
    if result.trailing_bytes:
        result.warnings.append(f"{result.trailing_bytes} trailing byte(s) remain after decoded payload.")


def format_raw_summary(raw: RawAnimation) -> str:
    counts = ", ".join(f"{flag}:{count}" for flag, count in sorted(raw.flag_counts.items())) or "none"
    lines = [
        f"File: {raw.path}",
        f"Type: {raw.kind}",
        f"Magic: {raw.magic4} (first 5 bytes: {raw.magic5_hex})",
        f"Version: {raw.version}",
        f"File size: {raw.file_size} bytes (0x{raw.file_size:X})",
        f"Unknown header fields: 0x{raw.unknown1:08X}, 0x{raw.unknown2:08X}",
        f"ID/sample count: {raw.num_ids}",
        f"Bone/track count: {raw.num_bones}",
        f"Track flags: {counts}  [0=bind, 2=child, 8=root]",
        f"Max track NumFrames field: {raw.max_num_frames:g}",
        f"Max NumKeyframes field: {raw.max_num_keyframes}",
        f"IDs @ 0x{raw.ids_offset:X}",
        f"Tracks @ 0x{raw.tracks_offset:X} ({TRACK_SIZE} bytes each)",
        f"Payload @ 0x{raw.payload_offset:X} -> 0x{raw.payload_end:X}",
        f"Trailing bytes: {raw.trailing_bytes}",
        f"Decode complete: {'yes' if raw.decode_complete else 'no'}",
    ]
    if raw.kind == "Bind Pose":
        lines.append(f"Bind pre-frame padding: {raw.bind_padding} bytes")
    if raw.ids:
        preview = ", ".join(f"0x{x:08X}" for x in raw.ids[:16])
        if len(raw.ids) > 16:
            preview += ", ..."
        lines.append(f"IDs preview: {preview}")
    if raw.warnings:
        lines.append("\nWarnings:")
        lines.extend(f"- {w}" for w in raw.warnings)
    return "\n".join(lines) + "\n"


def scan_raw_folder(root: str | Path, decode_payload: bool = True) -> dict[str, Any]:
    """Profile every .raw file and experimentally parse plausible animation layouts."""
    root = Path(root)
    if not root.is_dir():
        raise RawAnimationError(f"RAW scan folder does not exist: {root}")

    raw_files = sorted(root.rglob('*.raw'))
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    non_matching: list[str] = []
    kind_counts: Counter[str] = Counter()
    bone_counts: Counter[int] = Counter()
    flag_patterns: Counter[str] = Counter()
    magic4_counts: Counter[str] = Counter()
    prefix16_counts: Counter[str] = Counter()
    profile_examples: dict[str, dict[str, Any]] = {}
    trailing_files = 0
    known_magic_candidates = 0
    layout_candidates = 0

    for path in raw_files:
        rel = str(path.relative_to(root)).replace('\\', '/')
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append({'path': rel, 'error': str(exc)})
            continue

        magic_hex = data[:4].hex()
        p16 = data[:16].hex()
        magic4_counts[magic_hex] += 1
        prefix16_counts[p16] += 1
        if p16 not in profile_examples:
            prof = profile_raw_header(path)
            prof['path'] = rel
            profile_examples[p16] = prof

        if data[:4] == ANR1_MAGIC:
            known_magic_candidates += 1
        probe = probe_animation_layout_bytes(data)
        if probe.get('candidate'):
            layout_candidates += 1
        else:
            non_matching.append(rel)
            continue

        try:
            raw = inspect_raw_animation(path, decode_payload=decode_payload, allow_unknown_magic=True)
        except Exception as exc:
            errors.append({'path': rel, 'error': str(exc), 'probe': probe})
            continue

        kind_counts[raw.kind] += 1
        bone_counts[raw.num_bones] += 1
        pattern = ','.join(f'{flag}:{count}' for flag, count in sorted(raw.flag_counts.items()))
        flag_patterns[pattern or 'none'] += 1
        if raw.trailing_bytes:
            trailing_files += 1
        item = raw.to_dict(include_samples=False)
        item['path'] = rel
        item['layout_probe_score'] = probe.get('score')
        items.append(item)

    common_headers = []
    for p16, count in prefix16_counts.most_common(24):
        ex = profile_examples[p16].copy()
        ex['count'] = count
        common_headers.append(ex)

    return {
        'root': str(root),
        'total_raw_files': len(raw_files),
        'known_anr1_magic': known_magic_candidates,
        'layout_candidates': layout_candidates,
        'parsed_layout': len(items),
        # compatibility with v0.6 report readers
        'anr1_candidates': known_magic_candidates,
        'parsed_anr1': sum(1 for x in items if x.get('magic') == 'ANR1'),
        'non_anr1_raw': len(raw_files) - known_magic_candidates,
        'non_matching_layout': len(non_matching),
        'parse_errors': len(errors),
        'files_with_trailing_bytes': trailing_files,
        'kind_counts': dict(sorted(kind_counts.items())),
        'bone_counts': {str(k): v for k, v in sorted(bone_counts.items())},
        'flag_patterns': dict(sorted(flag_patterns.items())),
        'magic4_hex_counts': dict(magic4_counts.most_common()),
        'common_header_profiles': common_headers,
        'errors': errors,
        'non_matching_paths': non_matching,
        'items': items,
    }

def format_raw_folder_report(report: dict[str, Any]) -> str:
    lines = [
        f"RAW folder: {report['root']}",
        f"Total .raw files: {report['total_raw_files']}",
        f"Known ANR1 magic: {report['known_anr1_magic']}",
        f"TS2-style layout candidates: {report['layout_candidates']}",
        f"Experimentally parsed layouts: {report['parsed_layout']}",
        f"Non-matching layout: {report['non_matching_layout']}",
        f"Parse errors: {report['parse_errors']}",
        f"Parsed files with trailing bytes: {report['files_with_trailing_bytes']}",
        '',
        '4-byte signature counts (hex):',
    ]
    for k, v in list(report['magic4_hex_counts'].items())[:20]:
        lines.append(f"  {k or '(empty)'}: {v}")
    lines.append('Kinds:')
    for k, v in report['kind_counts'].items():
        lines.append(f"  {k}: {v}")
    lines.append('Bone/track counts:')
    for k, v in report['bone_counts'].items():
        lines.append(f"  {k}: {v} file(s)")
    lines.append('Track flag patterns:')
    for k, v in report['flag_patterns'].items():
        lines.append(f"  {k}: {v} file(s)")
    if report['common_header_profiles']:
        lines.append('\nCommon 16-byte header profiles:')
        for item in report['common_header_profiles'][:12]:
            lines.append(
                f"  x{item['count']} {item['prefix16_hex']}  {item['prefix16_ascii']}  "
                f"probe={item['layout_probe']['score']}"
            )
    if report['errors']:
        lines.append('\nFirst parse errors:')
        for item in report['errors'][:20]:
            lines.append(f"  {item['path']}: {item['error']}")
    return '\n'.join(lines) + '\n'
