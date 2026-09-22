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
POSE_DATA_OFFSET = 0x44
ANIM_TRACK_SIZE = 0x20


class SecondSightRawError(ValueError):
    pass


@dataclass
class SecondSightTrackDescriptor:
    index: int
    offset: int
    mode: int
    duration: float
    key_count: int
    reserved_hex: str
    duration_matches_header: bool
    key_count_matches_header: bool
    reserved_zero: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    # v0.6.3 animation structure
    time_ids: list[int] = field(default_factory=list)
    time_table_offset: int | None = None
    time_table_end: int | None = None
    time_table_valid: bool | None = None
    time_table_reason: str = ""
    track_table_offset: int | None = None
    track_table_end: int | None = None
    track_record_size: int | None = None
    track_descriptors: list[SecondSightTrackDescriptor] = field(default_factory=list)
    track_table_valid: bool | None = None
    payload_offset: int | None = None
    payload_size: int | None = None
    payload_prefix_hex: str = ""
    payload_tail_hex: str = ""

    # v0.6.3 pose profiling
    pose_data_offset: int | None = None
    pose_record_size_guess: int | None = None
    pose_size_formula_exact: bool | None = None
    pose_first_record_hex: str = ""

    @property
    def frame_count_guess(self) -> int:
        return self.field_0c

    @property
    def time_id_count_guess(self) -> int:
        return self.field_10

    @property
    def track_mode_counts(self) -> dict[int, int]:
        return dict(Counter(t.mode for t in self.track_descriptors))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["frame_count_guess"] = self.frame_count_guess
        out["time_id_count_guess"] = self.time_id_count_guess
        out["track_mode_counts"] = {str(k): v for k, v in sorted(self.track_mode_counts.items())}
        return out


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise SecondSightRawError(f"Need u32 at 0x{offset:X}, file is only {len(data)} bytes.")
    return struct.unpack_from("<I", data, offset)[0]


def _guess_kind(field_08: int, field_0c: int, field_10: int) -> str:
    if field_0c == 1 and field_10 == 1:
        if field_08 == 0:
            return "Bind Pose-like"
        return "Static / Pose-like"
    return "Animation-like"


def _sequential_prefix(words: list[int]) -> int:
    n = 0
    for i, value in enumerate(words):
        if value != (i + 1) * 4:
            break
        n += 1
    return n


def _validate_time_ids(ids: list[int], frame_count: int) -> tuple[bool, str]:
    if len(ids) < 2:
        return False, "Need at least two time IDs."
    if ids[-1] != 0:
        return False, f"Final time ID is {ids[-1]}, expected 0 terminator."
    if ids[-2] != frame_count - 1:
        return False, f"Penultimate time ID is {ids[-2]}, expected frame_count-1 ({frame_count - 1})."
    body = ids[:-2]
    if any(v <= 0 for v in body):
        return False, "Pre-terminal time IDs contain zero/negative values."
    if any(body[i] >= body[i + 1] for i in range(len(body) - 1)):
        return False, "Pre-terminal time IDs are not strictly increasing."
    if any(v >= frame_count for v in body):
        return False, "Pre-terminal time ID reaches/exceeds frame_count."
    return True, "strictly increasing; penultimate=frame_count-1; final=0"


def _decode_animation_layout(data: bytes, raw: SecondSightRawHeader) -> None:
    count = raw.field_10
    if count <= 0 or count > 1_000_000:
        raw.time_table_valid = False
        raw.track_table_valid = False
        raw.warnings.append(f"Implausible animation time-ID count: {count}.")
        return

    raw.time_table_offset = TABLE_OFFSET
    raw.time_table_end = TABLE_OFFSET + count * 4
    if raw.time_table_end > len(data):
        raw.time_table_valid = False
        raw.track_table_valid = False
        raw.warnings.append(
            f"Time-ID table exceeds file: end=0x{raw.time_table_end:X}, size=0x{len(data):X}."
        )
        return

    raw.time_ids = list(struct.unpack_from(f"<{count}I", data, TABLE_OFFSET))
    raw.time_table_valid, raw.time_table_reason = _validate_time_ids(raw.time_ids, raw.field_0c)
    if not raw.time_table_valid:
        raw.warnings.append(f"Animation time-ID validation failed: {raw.time_table_reason}")

    raw.track_table_offset = raw.time_table_end
    raw.track_record_size = ANIM_TRACK_SIZE
    raw.track_table_end = raw.track_table_offset + raw.bone_count * ANIM_TRACK_SIZE
    if raw.track_table_end > len(data):
        raw.track_table_valid = False
        raw.warnings.append(
            f"Track descriptor table exceeds file: end=0x{raw.track_table_end:X}, size=0x{len(data):X}."
        )
        return

    descriptors: list[SecondSightTrackDescriptor] = []
    valid = True
    for i in range(raw.bone_count):
        off = raw.track_table_offset + i * ANIM_TRACK_SIZE
        mode = _u32(data, off)
        duration = struct.unpack_from("<f", data, off + 4)[0]
        key_count = _u32(data, off + 8)
        reserved = data[off + 12:off + ANIM_TRACK_SIZE]
        duration_ok = math.isfinite(duration) and abs(duration - float(raw.field_0c)) < 1e-5
        key_ok = key_count == raw.field_10
        reserved_zero = reserved == b"\x00" * 20
        if not (duration_ok and key_ok and reserved_zero):
            valid = False
        descriptors.append(
            SecondSightTrackDescriptor(
                index=i,
                offset=off,
                mode=mode,
                duration=duration,
                key_count=key_count,
                reserved_hex=reserved.hex(),
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
            if not (d.duration_matches_header and d.key_count_matches_header and d.reserved_zero)
        ]
        raw.warnings.append(f"Track descriptor validation failed for bone indices: {bad[:24]}")

    raw.payload_offset = raw.track_table_end
    raw.payload_size = len(data) - raw.payload_offset
    if raw.payload_size < 0:
        raw.payload_size = None
        return
    raw.payload_prefix_hex = data[raw.payload_offset:raw.payload_offset + 128].hex()
    raw.payload_tail_hex = data[max(raw.payload_offset, len(data) - 64):].hex()


def _decode_pose_profile(data: bytes, raw: SecondSightRawHeader) -> None:
    raw.pose_data_offset = POSE_DATA_OFFSET
    if len(data) < POSE_DATA_OFFSET:
        raw.pose_size_formula_exact = False
        raw.warnings.append("Pose file is shorter than 0x44.")
        return
    body = len(data) - POSE_DATA_OFFSET
    if raw.bone_count > 0 and body % raw.bone_count == 0:
        raw.pose_record_size_guess = body // raw.bone_count
        raw.pose_size_formula_exact = True
    else:
        raw.pose_size_formula_exact = False
        raw.warnings.append(
            f"Pose body size {body} is not evenly divisible by {raw.bone_count} bones."
        )
    if raw.pose_record_size_guess:
        end = min(len(data), POSE_DATA_OFFSET + raw.pose_record_size_guess)
        raw.pose_first_record_hex = data[POSE_DATA_OFFSET:end].hex()
    raw.payload_offset = POSE_DATA_OFFSET
    raw.payload_size = len(data) - POSE_DATA_OFFSET
    raw.payload_prefix_hex = data[POSE_DATA_OFFSET:POSE_DATA_OFFSET + 128].hex()
    raw.payload_tail_hex = data[max(POSE_DATA_OFFSET, len(data) - 64):].hex()


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
        sample_spacing = field_0c / float(field_10 - 1)
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
        _decode_pose_profile(data, raw)

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
        f"  +0x10 time-ID/key-count-like: {raw.field_10}",
        f"  +0x14 bone_count: {raw.bone_count}",
        f"  +0x24 bone_count mirror: {raw.bone_count_mirror}",
        f"  legacy spacing metric field_0C/(field_10-1): {spacing}",
    ]

    if raw.kind_guess == "Animation-like":
        ids_preview = ", ".join(str(x) for x in raw.time_ids[:32])
        if len(raw.time_ids) > 32:
            ids_preview += ", ..."
        lines += [
            "",
            "Animation metadata:",
            f"  time IDs @ 0x{(raw.time_table_offset or 0):X}: {len(raw.time_ids)}",
            f"  time table valid: {raw.time_table_valid} ({raw.time_table_reason})",
            f"  time IDs preview: {ids_preview}",
            f"  track descriptors @ 0x{(raw.track_table_offset or 0):X}",
            f"  track record size: {raw.track_record_size}",
            f"  descriptors parsed: {len(raw.track_descriptors)} / {raw.bone_count}",
            f"  track table valid: {raw.track_table_valid}",
            f"  track mode counts: {raw.track_mode_counts}",
            f"  payload @ 0x{(raw.payload_offset or 0):X}, size={raw.payload_size}",
        ]
    else:
        lines += [
            "",
            "Pose profile:",
            f"  pose data @ 0x{(raw.pose_data_offset or 0):X}",
            f"  inferred bytes/bone: {raw.pose_record_size_guess}",
            f"  exact size formula: {raw.pose_size_formula_exact}",
            f"  payload size: {raw.payload_size}",
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
    seq_prefix_counts: Counter[int] = Counter()
    track_mode_counts: Counter[int] = Counter()
    pose_record_sizes: Counter[str] = Counter()
    animation_time_valid = 0
    animation_time_invalid = 0
    animation_tracks_valid = 0
    animation_tracks_invalid = 0
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
        seq_prefix_counts[raw.sequential_table_prefix] += 1
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
                animation_tracks_valid += 1
            else:
                animation_tracks_invalid += 1
            track_mode_counts.update(t.mode for t in raw.track_descriptors)
        elif raw.pose_record_size_guess is not None:
            pose_record_sizes[
                f"field08={raw.field_08}|{raw.kind_guess}|bytes_per_bone={raw.pose_record_size_guess}"
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
                "track_table_offset": raw.track_table_offset,
                "track_table_valid": raw.track_table_valid,
                "track_descriptors": [t.to_dict() for t in raw.track_descriptors[:8]],
                "track_mode_counts": {str(k): v for k, v in sorted(raw.track_mode_counts.items())},
                "payload_offset": raw.payload_offset,
                "payload_size": raw.payload_size,
                "payload_prefix_hex": raw.payload_prefix_hex,
                "payload_tail_hex": raw.payload_tail_hex,
                "pose_record_size_guess": raw.pose_record_size_guess,
                "pose_first_record_hex": raw.pose_first_record_hex,
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
        "animation_track_table_valid": animation_tracks_valid,
        "animation_track_table_invalid": animation_tracks_invalid,
        "track_mode_counts": {str(k): v for k, v in sorted(track_mode_counts.items())},
        "pose_record_sizes": dict(sorted(pose_record_sizes.items())),
        "sequential_table_prefix_counts": {
            str(k): v for k, v in sorted(seq_prefix_counts.items())
        },
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
        f"Animation track tables valid/invalid: {report['animation_track_table_valid']} / {report['animation_track_table_invalid']}",
        "Track mode counts:",
    ]
    for k, v in report["track_mode_counts"].items():
        lines.append(f"  mode {k}: {v}")

    lines.append("Pose record-size formulas:")
    for k, v in report["pose_record_sizes"].items():
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
