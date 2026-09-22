from __future__ import annotations

import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .version import REPORT_SCHEMA, __version__

SS_SENTINEL = b"\xff" * 8
HEADER_SIZE = 0x3C
TABLE_OFFSET = 0x3C
POSE_TRACK_TABLE_OFFSET = 0x44
ANIM_TRACK_SIZE = 0x20

# Empirically exact on all 578 animation-like files in the v0.6.4 validation report.
# size = base + per_key * key_count
TRACK_PAYLOAD_SIZE_FORMULAS: dict[int, tuple[int, int]] = {
    0: (18, 0),
    2: (12, 6),
    8: (0, 18),
    11: (0, 10),
    12: (6, 4),
    13: (4, 6),
}


class SecondSightRawError(ValueError):
    pass


@dataclass
class SecondSightTrackDescriptor:
    index: int
    offset: int
    unknown: int
    flags: int
    duration: float
    key_count: int
    reserved_hex: str
    unknown_zero: bool
    duration_matches_header: bool
    key_count_matches_header: bool
    reserved_zero: bool
    payload_offset: int | None = None
    payload_size: int | None = None
    payload_formula: str = ""
    payload_prefix_hex: str = ""
    payload_tail_hex: str = ""
    payload_full_hex: str = ""

    @property
    def mode(self) -> int:
        return self.flags

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["mode"] = self.flags
        return out


@dataclass
class SecondSightRawHeader:
    path: str
    file_size: int
    sentinel_ok: bool
    field_08: int
    field_0c: int
    field_10: int
    bone_count: int
    bone_count_mirror: int
    reserved_18_23_hex: str
    reserved_28_3b_hex: str
    kind_guess: str
    sample_spacing_guess: float | None
    table_words: list[int]
    sequential_table_prefix: int
    confidence: int
    warnings: list[str]

    time_ids: list[int] = field(default_factory=list)
    implicit_time_zero: bool = False
    time_table_offset: int | None = None
    time_table_end: int | None = None
    time_table_valid: bool | None = None
    time_table_reason: str = ""
    pretrack_prefix_hex: str = ""
    track_table_offset: int | None = None
    track_table_end: int | None = None
    track_record_size: int | None = None
    track_descriptors: list[SecondSightTrackDescriptor] = field(default_factory=list)
    track_table_valid: bool | None = None
    payload_offset: int | None = None
    payload_size: int | None = None
    payload_expected_size: int | None = None
    payload_size_valid: bool | None = None
    payload_prefix_hex: str = ""
    payload_tail_hex: str = ""

    pose_bytes_per_bone: int | None = None
    pose_payload_formula_exact: bool | None = None

    @property
    def frame_count_guess(self) -> int:
        return self.field_0c

    @property
    def key_count_guess(self) -> int:
        return self.field_10

    @property
    def stored_time_id_count(self) -> int:
        return len(self.time_ids)

    @property
    def track_mode_counts(self) -> dict[int, int]:
        return dict(Counter(t.flags for t in self.track_descriptors))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["frame_count_guess"] = self.frame_count_guess
        out["key_count_guess"] = self.key_count_guess
        out["stored_time_id_count"] = self.stored_time_id_count
        out["track_mode_counts"] = {str(k): v for k, v in sorted(self.track_mode_counts.items())}
        return out


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise SecondSightRawError(f"Need u32 at 0x{offset:X}, file is only {len(data)} bytes.")
    return struct.unpack_from("<I", data, offset)[0]


def _guess_kind(field_08: int, field_0c: int, field_10: int) -> str:
    if field_0c == 1 and field_10 == 1:
        return "Bind Pose-like" if field_08 == 0 else "Static / Pose-like"
    return "Animation-like"


def _sequential_prefix(words: list[int]) -> int:
    n = 0
    for i, value in enumerate(words):
        if value != (i + 1) * 4:
            break
        n += 1
    return n


def _validate_animation_time_ids(ids: list[int], frame_count: int, key_count: int) -> tuple[bool, str]:
    expected = key_count - 1
    if expected <= 0:
        return False, f"Animation key_count {key_count} does not leave stored time IDs."
    if len(ids) != expected:
        return False, f"Stored time-ID count is {len(ids)}, expected key_count-1 ({expected})."
    if not ids:
        return False, "No stored time IDs."
    if ids[-1] != frame_count - 1:
        return False, f"Last stored time ID is {ids[-1]}, expected frame_count-1 ({frame_count - 1})."
    if any(v <= 0 for v in ids):
        return False, "Stored time IDs contain zero/negative values."
    if any(ids[i] >= ids[i + 1] for i in range(len(ids) - 1)):
        return False, "Stored time IDs are not strictly increasing."
    if any(v >= frame_count for v in ids):
        return False, "Stored time ID reaches/exceeds frame_count."
    return True, "key 0 is implicit at time 0; stored IDs increase strictly and end at frame_count-1"


def _track_payload_size(flags: int, key_count: int) -> tuple[int | None, str]:
    formula = TRACK_PAYLOAD_SIZE_FORMULAS.get(flags)
    if formula is None:
        return None, f"unknown flags {flags}"
    base, per_key = formula
    size = base + per_key * key_count
    return size, f"{base} + {per_key}*K" if per_key else str(base)


def _parse_track_table(data: bytes, raw: SecondSightRawHeader, offset: int) -> None:
    raw.track_table_offset = offset
    raw.track_record_size = ANIM_TRACK_SIZE
    raw.track_table_end = offset + raw.bone_count * ANIM_TRACK_SIZE
    if raw.track_table_end > len(data):
        raw.track_table_valid = False
        raw.warnings.append(
            f"Track table exceeds file: end=0x{raw.track_table_end:X}, size=0x{len(data):X}."
        )
        return

    descriptors: list[SecondSightTrackDescriptor] = []
    valid = True
    for i in range(raw.bone_count):
        off = offset + i * ANIM_TRACK_SIZE
        unknown = _u32(data, off)
        flags = _u32(data, off + 4)
        duration = struct.unpack_from("<f", data, off + 8)[0]
        key_count = _u32(data, off + 12)
        reserved = data[off + 16:off + 32]
        unknown_zero = unknown == 0
        duration_ok = math.isfinite(duration) and abs(duration - float(raw.field_0c)) < 1e-5
        key_ok = key_count == raw.field_10
        reserved_zero = reserved == b"\x00" * 16
        if not (unknown_zero and duration_ok and key_ok and reserved_zero):
            valid = False
        descriptors.append(
            SecondSightTrackDescriptor(
                index=i,
                offset=off,
                unknown=unknown,
                flags=flags,
                duration=duration,
                key_count=key_count,
                reserved_hex=reserved.hex(),
                unknown_zero=unknown_zero,
                duration_matches_header=duration_ok,
                key_count_matches_header=key_ok,
                reserved_zero=reserved_zero,
            )
        )
    raw.track_descriptors = descriptors
    raw.track_table_valid = valid
    if not valid:
        bad = [
            d.index for d in descriptors
            if not (d.unknown_zero and d.duration_matches_header and d.key_count_matches_header and d.reserved_zero)
        ]
        raw.warnings.append(f"Track descriptor validation failed for bone indices: {bad[:24]}")


def _assign_track_payloads(data: bytes, raw: SecondSightRawHeader) -> None:
    if raw.track_table_end is None:
        return
    raw.payload_offset = raw.track_table_end
    raw.payload_size = len(data) - raw.payload_offset
    if raw.payload_size < 0:
        raw.payload_size = None
        return

    cursor = raw.payload_offset
    expected_total = 0
    unknown_formula = False

    for track in raw.track_descriptors:
        if raw.kind_guess == "Bind Pose-like" and raw.field_08 == 0:
            size, formula = 28, "28 (field_08=0 bind-pose)"
        else:
            size, formula = _track_payload_size(track.flags, track.key_count)

        track.payload_formula = formula
        if size is None:
            unknown_formula = True
            continue

        expected_total += size
        track.payload_offset = cursor
        track.payload_size = size

        end = cursor + size
        if end > len(data):
            raw.warnings.append(
                f"Track {track.index} payload exceeds file: 0x{cursor:X}+{size} > 0x{len(data):X}."
            )
            raw.payload_size_valid = False
            return

        chunk = data[cursor:end]
        track.payload_prefix_hex = chunk[:64].hex()
        track.payload_tail_hex = chunk[-32:].hex() if chunk else ""
        track.payload_full_hex = chunk.hex() if len(chunk) <= 256 else ""
        cursor = end

    raw.payload_expected_size = None if unknown_formula else expected_total
    if unknown_formula:
        raw.payload_size_valid = None
        raw.warnings.append("One or more track flags have no payload-size formula yet.")
    else:
        raw.payload_size_valid = expected_total == raw.payload_size
        if not raw.payload_size_valid:
            raw.warnings.append(
                f"Track payload formulas total {expected_total} bytes, actual payload is {raw.payload_size} bytes."
            )

    raw.payload_prefix_hex = data[raw.payload_offset:raw.payload_offset + 128].hex()
    raw.payload_tail_hex = data[max(raw.payload_offset, len(data) - 64):].hex()

    if raw.bone_count:
        if raw.kind_guess == "Bind Pose-like" and raw.field_08 == 0:
            raw.pose_bytes_per_bone = 28
        elif raw.kind_guess == "Static / Pose-like":
            sizes = {t.payload_size for t in raw.track_descriptors}
            if len(sizes) == 1:
                raw.pose_bytes_per_bone = next(iter(sizes))
        if raw.kind_guess != "Animation-like":
            raw.pose_payload_formula_exact = raw.payload_size_valid


def _decode_animation_layout(data: bytes, raw: SecondSightRawHeader) -> None:
    key_count = raw.field_10
    stored_count = key_count - 1
    if stored_count <= 0 or stored_count > 1_000_000:
        raw.time_table_valid = False
        raw.track_table_valid = False
        raw.warnings.append(f"Implausible animation key/time count: {key_count}.")
        return

    raw.implicit_time_zero = True
    raw.time_table_offset = TABLE_OFFSET
    raw.time_table_end = TABLE_OFFSET + stored_count * 4
    if raw.time_table_end > len(data):
        raw.time_table_valid = False
        raw.track_table_valid = False
        raw.warnings.append(
            f"Time-ID table exceeds file: end=0x{raw.time_table_end:X}, size=0x{len(data):X}."
        )
        return

    raw.time_ids = list(struct.unpack_from(f"<{stored_count}I", data, TABLE_OFFSET))
    raw.time_table_valid, raw.time_table_reason = _validate_animation_time_ids(
        raw.time_ids, raw.field_0c, raw.field_10
    )
    if not raw.time_table_valid:
        raw.warnings.append(f"Animation time-ID validation failed: {raw.time_table_reason}")

    _parse_track_table(data, raw, raw.time_table_end)
    _assign_track_payloads(data, raw)


def _decode_pose_layout(data: bytes, raw: SecondSightRawHeader) -> None:
    raw.pretrack_prefix_hex = data[TABLE_OFFSET:POSE_TRACK_TABLE_OFFSET].hex()
    _parse_track_table(data, raw, POSE_TRACK_TABLE_OFFSET)
    _assign_track_payloads(data, raw)


def inspect_second_sight_raw(path: str | Path, table_extra_words: int = 8) -> SecondSightRawHeader:
    path = Path(path)
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise SecondSightRawError(f"RAW is too small for the 0x3C header: {len(data)} bytes.")

    sentinel_ok = data[:8] == SS_SENTINEL
    field_08 = _u32(data, 0x08)
    field_0c = _u32(data, 0x0C)
    field_10 = _u32(data, 0x10)
    bone_count = _u32(data, 0x14)
    bone_count_mirror = _u32(data, 0x24)

    warnings: list[str] = []
    score = 0
    if sentinel_ok:
        score += 40
    else:
        warnings.append("First 8 bytes are not FF FF FF FF FF FF FF FF.")

    if 0 < bone_count <= 512:
        score += 25
    else:
        warnings.append(f"Implausible bone_count at 0x14: {bone_count}.")

    if bone_count_mirror == bone_count and bone_count != 0:
        score += 25
    else:
        warnings.append(
            f"bone_count mirror mismatch: 0x14={bone_count}, 0x24={bone_count_mirror}."
        )

    if field_0c > 0 and field_10 > 0:
        score += 5
    if data[0x18:0x24] == b"\x00" * 12:
        score += 3
    if data[0x28:0x3C] == b"\x00" * 20:
        score += 2

    word_count = min(max(bone_count + table_extra_words, 16), 96)
    available = max(0, (len(data) - TABLE_OFFSET) // 4)
    word_count = min(word_count, available)
    table_words = list(struct.unpack_from(f"<{word_count}I", data, TABLE_OFFSET)) if word_count else []

    sample_spacing = None
    if field_10 > 1:
        sample_spacing = (field_0c - 1) / float(field_10 - 1)
        if not math.isfinite(sample_spacing):
            sample_spacing = None

    kind = _guess_kind(field_08, field_0c, field_10)
    seq = _sequential_prefix(table_words)

    raw = SecondSightRawHeader(
        path=str(path),
        file_size=len(data),
        sentinel_ok=sentinel_ok,
        field_08=field_08,
        field_0c=field_0c,
        field_10=field_10,
        bone_count=bone_count,
        bone_count_mirror=bone_count_mirror,
        reserved_18_23_hex=data[0x18:0x24].hex(),
        reserved_28_3b_hex=data[0x28:0x3C].hex(),
        kind_guess=kind,
        sample_spacing_guess=sample_spacing,
        table_words=table_words,
        sequential_table_prefix=seq,
        confidence=min(score, 100),
        warnings=warnings,
    )

    if kind == "Animation-like":
        _decode_animation_layout(data, raw)
    else:
        _decode_pose_layout(data, raw)

    return raw


def format_second_sight_raw_summary(raw: SecondSightRawHeader) -> str:
    spacing = "n/a" if raw.sample_spacing_guess is None else f"{raw.sample_spacing_guess:.6g}"
    lines = [
        f"File: {raw.path}",
        f"File size: {raw.file_size} bytes (0x{raw.file_size:X})",
        f"Second Sight sentinel FF*8: {'yes' if raw.sentinel_ok else 'no'}",
        f"Kind guess: {raw.kind_guess}",
        f"Confidence: {raw.confidence}/100",
        "",
        "Observed Second Sight PC RAW header:",
        f"  +0x08 field_08: {raw.field_08}",
        f"  +0x0C frame_count/duration-like: {raw.field_0c}",
        f"  +0x10 key_count-like: {raw.field_10}",
        f"  +0x14 bone_count: {raw.bone_count}",
        f"  +0x24 bone_count mirror: {raw.bone_count_mirror}",
        f"  average stored-key spacing: {spacing}",
    ]

    if raw.kind_guess == "Animation-like":
        ids_preview = ", ".join(str(x) for x in raw.time_ids[:32])
        if len(raw.time_ids) > 32:
            ids_preview += ", ..."
        lines += [
            "",
            "Animation metadata:",
            f"  implicit key/time 0: {raw.implicit_time_zero}",
            f"  stored time IDs @ 0x{(raw.time_table_offset or 0):X}: {len(raw.time_ids)} (= key_count-1)",
            f"  time table valid: {raw.time_table_valid} ({raw.time_table_reason})",
            f"  time IDs preview: {ids_preview}",
        ]
    else:
        lines += [
            "",
            "Pose metadata:",
            f"  pre-track prefix @ 0x3C..0x43: {raw.pretrack_prefix_hex}",
            f"  inferred payload bytes/bone: {raw.pose_bytes_per_bone}",
            f"  payload formula exact: {raw.pose_payload_formula_exact}",
        ]

    lines += [
        "",
        f"Track descriptors @ 0x{(raw.track_table_offset or 0):X}",
        f"  record size: {raw.track_record_size}",
        f"  descriptors parsed: {len(raw.track_descriptors)} / {raw.bone_count}",
        f"  track table valid: {raw.track_table_valid}",
        f"  track flag counts: {raw.track_mode_counts}",
        f"Payload @ 0x{(raw.payload_offset or 0):X}: actual={raw.payload_size}, expected={raw.payload_expected_size}, exact={raw.payload_size_valid}",
    ]

    if raw.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {w}" for w in raw.warnings)
    return "\n".join(lines) + "\n"


def scan_second_sight_raw_folder(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    if not root.is_dir():
        raise SecondSightRawError(f"Folder does not exist: {root}")

    files = sorted(root.rglob("*.raw"))
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    kind_counts: Counter[str] = Counter()
    bone_counts: Counter[int] = Counter()
    field08_counts: Counter[int] = Counter()
    mirror_mismatch = 0
    sentinel_mismatch = 0
    track_flag_counts: Counter[int] = Counter()
    payload_formula_valid = 0
    payload_formula_invalid = 0
    payload_formula_unknown = 0
    animation_time_valid = 0
    animation_time_invalid = 0
    track_tables_valid = 0
    track_tables_invalid = 0
    pose_payload_sizes: Counter[str] = Counter()
    group_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for path in files:
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            raw = inspect_second_sight_raw(path)
        except Exception as exc:
            errors.append({"path": rel, "error": str(exc)})
            continue

        item = raw.to_dict()
        item["path"] = rel
        items.append(item)
        kind_counts[raw.kind_guess] += 1
        bone_counts[raw.bone_count] += 1
        field08_counts[raw.field_08] += 1
        track_flag_counts.update(t.flags for t in raw.track_descriptors)

        if not raw.sentinel_ok:
            sentinel_mismatch += 1
        if raw.bone_count != raw.bone_count_mirror:
            mirror_mismatch += 1

        if raw.kind_guess == "Animation-like":
            if raw.time_table_valid:
                animation_time_valid += 1
            else:
                animation_time_invalid += 1

        if raw.track_table_valid:
            track_tables_valid += 1
        else:
            track_tables_invalid += 1

        if raw.payload_size_valid is True:
            payload_formula_valid += 1
        elif raw.payload_size_valid is False:
            payload_formula_invalid += 1
        else:
            payload_formula_unknown += 1

        if raw.kind_guess != "Animation-like":
            pose_payload_sizes[
                f"field08={raw.field_08}|{raw.kind_guess}|bytes_per_bone={raw.pose_bytes_per_bone}"
            ] += 1

        key = f"{raw.kind_guess}|field08={raw.field_08}|bones={raw.bone_count}"
        if len(group_examples[key]) < 3:
            group_examples[key].append({
                "path": rel,
                "file_size": raw.file_size,
                "field_08": raw.field_08,
                "field_0c": raw.field_0c,
                "field_10": raw.field_10,
                "bone_count": raw.bone_count,
                "time_ids": raw.time_ids[:96],
                "time_table_valid": raw.time_table_valid,
                "pretrack_prefix_hex": raw.pretrack_prefix_hex,
                "track_table_offset": raw.track_table_offset,
                "track_table_valid": raw.track_table_valid,
                "track_descriptors": [t.to_dict() for t in raw.track_descriptors],
                "track_mode_counts": {str(k): v for k, v in sorted(raw.track_mode_counts.items())},
                "payload_offset": raw.payload_offset,
                "payload_size": raw.payload_size,
                "payload_expected_size": raw.payload_expected_size,
                "payload_size_valid": raw.payload_size_valid,
                "payload_prefix_hex": raw.payload_prefix_hex,
                "payload_tail_hex": raw.payload_tail_hex,
                "pose_bytes_per_bone": raw.pose_bytes_per_bone,
                "confidence": raw.confidence,
            })

    return {
        "tool_version": __version__,
        "report_schema": REPORT_SCHEMA,
        "parser": "app.second_sight_raw",
        "root": str(root),
        "total_raw_files": len(files),
        "parsed_headers": len(items),
        "parse_errors": len(errors),
        "sentinel_mismatch": sentinel_mismatch,
        "bone_count_mirror_mismatch": mirror_mismatch,
        "kind_counts": dict(sorted(kind_counts.items())),
        "bone_counts": {str(k): v for k, v in sorted(bone_counts.items())},
        "field08_counts": {str(k): v for k, v in sorted(field08_counts.items())},
        "animation_time_table_valid": animation_time_valid,
        "animation_time_table_invalid": animation_time_invalid,
        "track_table_valid": track_tables_valid,
        "track_table_invalid": track_tables_invalid,
        "track_flag_counts": {str(k): v for k, v in sorted(track_flag_counts.items())},
        "payload_formula_valid": payload_formula_valid,
        "payload_formula_invalid": payload_formula_invalid,
        "payload_formula_unknown": payload_formula_unknown,
        "track_payload_size_formulas": {
            str(k): {"base": v[0], "per_key": v[1]}
            for k, v in sorted(TRACK_PAYLOAD_SIZE_FORMULAS.items())
        },
        "pose_payload_sizes": dict(sorted(pose_payload_sizes.items())),
        "group_examples": dict(group_examples),
        "errors": errors,
        "items": items,
    }


def format_second_sight_folder_report(report: dict[str, Any]) -> str:
    lines = [
        f"Tool version: {report.get('tool_version', 'unknown')}",
        f"Report schema: {report.get('report_schema', 'unknown')}",
        f"RAW folder: {report['root']}",
        f"Total .raw files: {report['total_raw_files']}",
        f"Parsed Second Sight headers: {report['parsed_headers']}",
        f"Parse errors: {report['parse_errors']}",
        f"Sentinel mismatches: {report['sentinel_mismatch']}",
        f"Bone-count mirror mismatches: {report['bone_count_mirror_mismatch']}",
        "",
        "Kind guesses:",
    ]
    for k, v in report["kind_counts"].items():
        lines.append(f"  {k}: {v}")

    lines += [
        "",
        f"Animation time tables valid/invalid: {report['animation_time_table_valid']} / {report['animation_time_table_invalid']}",
        f"All track tables valid/invalid: {report['track_table_valid']} / {report['track_table_invalid']}",
        f"Payload formulas valid/invalid/unknown: {report['payload_formula_valid']} / {report['payload_formula_invalid']} / {report['payload_formula_unknown']}",
        "Track flag counts:",
    ]
    for k, v in report["track_flag_counts"].items():
        lines.append(f"  flag {k}: {v}")

    lines.append("Pose payload sizes:")
    for k, v in report["pose_payload_sizes"].items():
        lines.append(f"  {k}: {v} file(s)")

    lines.append("Bone counts (+0x14):")
    for k, v in report["bone_counts"].items():
        lines.append(f"  {k}: {v}")

    lines.append("field_08 values:")
    for k, v in report["field08_counts"].items():
        lines.append(f"  {k}: {v}")

    if report["errors"]:
        lines.append("")
        lines.append("First parse errors:")
        for item in report["errors"][:20]:
            lines.append(f"  {item['path']}: {item['error']}")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
