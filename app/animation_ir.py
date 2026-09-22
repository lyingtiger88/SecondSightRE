from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .second_sight_raw import SecondSightRawError, inspect_second_sight_raw

POS16_SCALE = 4.0
INV_SQRT2 = 1.0 / math.sqrt(2.0)


@dataclass
class DecodedTransform:
    time: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "position": list(self.position),
            "rotation": list(self.rotation),
        }


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _normalize_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    n = math.sqrt(sum(c * c for c in q))
    if n <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / n for c in q)  # type: ignore[return-value]


def decode_position_f32(data: bytes, offset: int) -> tuple[float, float, float]:
    return struct.unpack_from("<3f", data, offset)


def decode_position_u16x3(data: bytes, offset: int) -> tuple[float, float, float]:
    x, y, z = struct.unpack_from("<3H", data, offset)

    def cv(v: int) -> float:
        return ((v * 2.0 / 65535.0) - 1.0) * POS16_SCALE

    return cv(x), cv(y), cv(z)


def decode_quaternion_u16x3(data: bytes, offset: int) -> tuple[float, float, float, float]:
    values = struct.unpack_from("<3H", data, offset)
    xyz = [((v * 2.0 / 65535.0) - 1.0) for v in values]
    missing_sq = max(0.0, 1.0 - sum(c * c for c in xyz))
    q = (_clamp_unit(xyz[0]), _clamp_unit(xyz[1]), _clamp_unit(xyz[2]), math.sqrt(missing_sq))
    return _normalize_quat(q)


def decode_quaternion_smallest3_32(data: bytes, offset: int) -> tuple[float, float, float, float]:
    packed = struct.unpack_from("<I", data, offset)[0]
    omitted = packed & 0x3
    values = [(packed >> (2 + i * 10)) & 0x3FF for i in range(3)]
    stored = [(((v * 2.0 / 1023.0) - 1.0) * INV_SQRT2) for v in values]
    missing_sq = max(0.0, 1.0 - sum(c * c for c in stored))
    missing = math.sqrt(missing_sq)

    out: list[float] = []
    src = 0
    for component in range(4):
        if component == omitted:
            out.append(missing)
        else:
            out.append(stored[src])
            src += 1
    return _normalize_quat(tuple(out))  # type: ignore[arg-type]


def decode_quaternion_f32x4(data: bytes, offset: int) -> tuple[float, float, float, float]:
    return _normalize_quat(struct.unpack_from("<4f", data, offset))


def _norm4(q: tuple[float, float, float, float]) -> float:
    return math.sqrt(sum(c * c for c in q))


def _track_times(raw) -> list[int]:
    if raw.kind_guess == "Animation-like":
        times = [0] + list(raw.time_ids)
        if len(times) != raw.field_10:
            raise SecondSightRawError(
                f"Expected {raw.field_10} key times but reconstructed {len(times)}."
            )
        return times
    return [0]


def _read_payload(data: bytes, track) -> bytes:
    if track.payload_offset is None or track.payload_size is None:
        raise SecondSightRawError(f"Track {track.index} has no payload range.")
    start = track.payload_offset
    end = start + track.payload_size
    if start < 0 or end > len(data):
        raise SecondSightRawError(
            f"Track {track.index} payload 0x{start:X}..0x{end:X} exceeds file."
        )
    return data[start:end]


def decode_track(data: bytes, raw, track) -> dict[str, Any]:
    payload = _read_payload(data, track)
    times = _track_times(raw)
    k = len(times)
    flags = track.flags

    position_codec = ""
    rotation_codec = ""
    position_animated = False
    rotation_animated = False
    positions: list[tuple[float, float, float]] = []
    rotations: list[tuple[float, float, float, float]] = []

    if raw.kind_guess == "Bind Pose-like" and raw.field_08 == 0:
        if len(payload) != 28:
            raise SecondSightRawError(
                f"Bind track {track.index} expected 28 bytes, got {len(payload)}."
            )
        positions = [decode_position_f32(payload, 0)]
        rotations = [decode_quaternion_f32x4(payload, 12)]
        position_codec = "float32x3"
        rotation_codec = "float32x4"
    elif flags == 0:
        if len(payload) != 18:
            raise SecondSightRawError(f"Flag 0 track expected 18 bytes, got {len(payload)}.")
        positions = [decode_position_f32(payload, 0)]
        rotations = [decode_quaternion_u16x3(payload, 12)]
        position_codec = "float32x3"
        rotation_codec = "quat_xyz_u16_reconstruct_w"
    elif flags == 2:
        expected = 12 + 6 * k
        if len(payload) != expected:
            raise SecondSightRawError(f"Flag 2 track expected {expected} bytes, got {len(payload)}.")
        p = decode_position_f32(payload, 0)
        positions = [p] * k
        rotations = [decode_quaternion_u16x3(payload, 12 + 6 * i) for i in range(k)]
        position_codec = "float32x3"
        rotation_codec = "quat_xyz_u16_reconstruct_w"
        rotation_animated = True
    elif flags == 8:
        expected = 18 * k
        if len(payload) != expected:
            raise SecondSightRawError(f"Flag 8 track expected {expected} bytes, got {len(payload)}.")
        for i in range(k):
            off = 18 * i
            positions.append(decode_position_f32(payload, off))
            rotations.append(decode_quaternion_u16x3(payload, off + 12))
        position_codec = "float32x3"
        rotation_codec = "quat_xyz_u16_reconstruct_w"
        position_animated = True
        rotation_animated = True
    elif flags == 11:
        expected = 10 * k
        if len(payload) != expected:
            raise SecondSightRawError(f"Flag 11 track expected {expected} bytes, got {len(payload)}.")
        for i in range(k):
            off = 10 * i
            positions.append(decode_position_u16x3(payload, off))
            rotations.append(decode_quaternion_smallest3_32(payload, off + 6))
        position_codec = "position_u16x3_scale4"
        rotation_codec = "quat_smallest3_10_10_10_index2"
        position_animated = True
        rotation_animated = True
    elif flags == 12:
        expected = 6 + 4 * k
        if len(payload) != expected:
            raise SecondSightRawError(f"Flag 12 track expected {expected} bytes, got {len(payload)}.")
        p = decode_position_u16x3(payload, 0)
        positions = [p] * k
        rotations = [decode_quaternion_smallest3_32(payload, 6 + 4 * i) for i in range(k)]
        position_codec = "position_u16x3_scale4"
        rotation_codec = "quat_smallest3_10_10_10_index2"
        rotation_animated = True
    elif flags == 13:
        expected = 4 + 6 * k
        if len(payload) != expected:
            raise SecondSightRawError(f"Flag 13 track expected {expected} bytes, got {len(payload)}.")
        q = decode_quaternion_smallest3_32(payload, 0)
        rotations = [q] * k
        positions = [decode_position_u16x3(payload, 4 + 6 * i) for i in range(k)]
        position_codec = "position_u16x3_scale4"
        rotation_codec = "quat_smallest3_10_10_10_index2"
        position_animated = True
    else:
        raise SecondSightRawError(f"Unsupported track flags {flags} on bone {track.index}.")

    if len(positions) == 1 and k > 1:
        positions *= k
    if len(rotations) == 1 and k > 1:
        rotations *= k
    if len(positions) != k or len(rotations) != k:
        raise SecondSightRawError(
            f"Track {track.index} decoded {len(positions)} positions and {len(rotations)} rotations for {k} keys."
        )

    samples = [
        DecodedTransform(times[i], positions[i], rotations[i]).to_dict()
        for i in range(k)
    ]
    norms = [_norm4(q) for q in rotations]

    return {
        "bone_index": track.index,
        "flags": flags,
        "unknown": track.unknown,
        "position_codec": position_codec,
        "rotation_codec": rotation_codec,
        "position_animated": position_animated,
        "rotation_animated": rotation_animated,
        "payload_offset": track.payload_offset,
        "payload_size": track.payload_size,
        "key_count": k,
        "quaternion_norm_min": min(norms) if norms else None,
        "quaternion_norm_max": max(norms) if norms else None,
        "samples": samples,
    }


def build_animation_ir(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    raw = inspect_second_sight_raw(path)
    data = path.read_bytes()

    decoded_tracks = [decode_track(data, raw, t) for t in raw.track_descriptors]
    times = _track_times(raw)

    return {
        "schema": "secondsight.animation_ir.v1",
        "source_format": "Second Sight PC RAW",
        "source_path": str(path),
        "kind": raw.kind_guess,
        "field_08": raw.field_08,
        "frame_count": raw.field_0c,
        "key_count": raw.field_10,
        "key_times": times,
        "bone_count": raw.bone_count,
        "bone_names_known": False,
        "coordinate_system_known": False,
        "units_known": False,
        "tracks": decoded_tracks,
    }


def write_animation_ir(path: str | Path, output_path: str | Path) -> dict[str, Any]:
    ir = build_animation_ir(path)
    Path(output_path).write_text(json.dumps(ir, indent=2, ensure_ascii=False), encoding="utf-8")
    return ir
