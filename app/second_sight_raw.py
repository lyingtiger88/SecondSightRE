from __future__ import annotations

import json
import math
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

SS_SENTINEL = b"\xff" * 8
HEADER_SIZE = 0x3C
TABLE_OFFSET = 0x3C


class SecondSightRawError(ValueError):
    pass


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def inspect_second_sight_raw(path: str | Path, table_extra_words: int = 8) -> SecondSightRawHeader:
    path = Path(path)
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise SecondSightRawError(f"RAW is too small: {len(data)} bytes.")

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

    # Real Second Sight animation examples commonly begin this table 4,8,12,...
    # We record this as evidence only; it is not required for classification.
    if kind == "Animation-like" and seq >= min(4, bone_count):
        score = min(100, score + 5)

    return SecondSightRawHeader(
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


def format_second_sight_raw_summary(raw: SecondSightRawHeader) -> str:
    spacing = "n/a" if raw.sample_spacing_guess is None else f"{raw.sample_spacing_guess:.6g}"
    preview = ", ".join(str(x) for x in raw.table_words[:24])
    if len(raw.table_words) > 24:
        preview += ", ..."
    lines = [
        f"File: {raw.path}",
        f"File size: {raw.file_size} bytes (0x{raw.file_size:X})",
        f"Second Sight sentinel FF*8: {'yes' if raw.sentinel_ok else 'no'}",
        f"Kind guess: {raw.kind_guess}",
        f"Confidence: {raw.confidence}/100",
        "",
        "Observed Second Sight PC RAW header:",
        f"  +0x08 field_08: {raw.field_08} (meaning not confirmed)",
        f"  +0x0C field_0C: {raw.field_0c} (timeline/duration-like; tentative)",
        f"  +0x10 field_10: {raw.field_10} (sample/key-count-like; tentative)",
        f"  +0x14 bone_count: {raw.bone_count}",
        f"  +0x24 bone_count mirror: {raw.bone_count_mirror}",
        f"  sample spacing guess field_0C/(field_10-1): {spacing}",
        f"  table @ 0x3C sequential 4-byte prefix: {raw.sequential_table_prefix} word(s)",
        f"  table preview: {preview}",
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

        key = f"{raw.kind_guess}|field08={raw.field_08}|bones={raw.bone_count}"
        if len(group_examples[key]) < 3:
            group_examples[key].append({
                "path": rel,
                "file_size": raw.file_size,
                "field_08": raw.field_08,
                "field_0c": raw.field_0c,
                "field_10": raw.field_10,
                "bone_count": raw.bone_count,
                "sample_spacing_guess": raw.sample_spacing_guess,
                "sequential_table_prefix": raw.sequential_table_prefix,
                "table_words": raw.table_words[:48],
                "confidence": raw.confidence,
            })

    return {
        "root": str(root),
        "total_raw_files": len(files),
        "parsed_headers": len(items),
        "parse_errors": len(errors),
        "sentinel_mismatch": sentinel_mismatch,
        "bone_count_mirror_mismatch": mirror_mismatch,
        "kind_counts": dict(sorted(kind_counts.items())),
        "bone_counts": {str(k): v for k, v in sorted(bone_counts.items())},
        "field08_counts": {str(k): v for k, v in sorted(field08_counts.items())},
        "sequential_table_prefix_counts": {
            str(k): v for k, v in sorted(seq_prefix_counts.items())
        },
        "group_examples": dict(group_examples),
        "errors": errors,
        "items": items,
    }


def format_second_sight_folder_report(report: dict[str, Any]) -> str:
    lines = [
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
    lines.append("Bone counts (+0x14):")
    for k, v in report["bone_counts"].items():
        lines.append(f"  {k}: {v}")
    lines.append("field_08 values:")
    for k, v in report["field08_counts"].items():
        lines.append(f"  {k}: {v}")
    lines.append("Sequential table-prefix lengths from 0x3C:")
    for k, v in report["sequential_table_prefix_counts"].items():
        lines.append(f"  {k}: {v}")
    if report["errors"]:
        lines.append("")
        lines.append("First parse errors:")
        for item in report["errors"][:20]:
            lines.append(f"  {item['path']}: {item['error']}")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
