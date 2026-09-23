from __future__ import annotations

import math
from typing import Any

# Provisional Second Sight 21-bone human topology.
#
# Indices 0..18 are strongly supported by:
#   1) the decoded human_21 bind pose geometry and bilateral symmetry, and
#   2) the closely related Free Radical TS2 human skeleton order, shifted by
#      one track because Second Sight stores an explicit root track.
#
# Indices 19 and 20 are present in Second Sight but their semantic names and
# parents are not yet proven, so they stay unresolved on purpose.

HUMAN21_BONES: list[dict[str, Any]] = [
    {"index": 0, "name": "Root", "parent": None, "confidence": "high"},
    {"index": 1, "name": "Hips", "parent": 0, "confidence": "high"},
    {"index": 2, "name": "Waist", "parent": 1, "confidence": "high"},
    {"index": 3, "name": "Neck", "parent": 2, "confidence": "high"},
    {"index": 4, "name": "Head", "parent": 3, "confidence": "high"},
    {"index": 5, "name": "Right_Shoulder_1", "parent": 2, "confidence": "high"},
    {"index": 6, "name": "Right_Shoulder_2", "parent": 5, "confidence": "high"},
    {"index": 7, "name": "Right_Elbow", "parent": 6, "confidence": "high"},
    {"index": 8, "name": "Right_Wrist", "parent": 7, "confidence": "high"},
    {"index": 9, "name": "Left_Shoulder_1", "parent": 2, "confidence": "high"},
    {"index": 10, "name": "Left_Shoulder_2", "parent": 9, "confidence": "high"},
    {"index": 11, "name": "Left_Elbow", "parent": 10, "confidence": "high"},
    {"index": 12, "name": "Left_Wrist", "parent": 11, "confidence": "high"},
    {"index": 13, "name": "Right_Hip", "parent": 0, "confidence": "high"},
    {"index": 14, "name": "Right_Knee", "parent": 13, "confidence": "high"},
    {"index": 15, "name": "Right_Foot", "parent": 14, "confidence": "high"},
    {"index": 16, "name": "Left_Hip", "parent": 0, "confidence": "high"},
    {"index": 17, "name": "Left_Knee", "parent": 16, "confidence": "high"},
    {"index": 18, "name": "Left_Foot", "parent": 17, "confidence": "high"},
    {"index": 19, "name": "Unresolved_19", "parent": None, "confidence": "unknown"},
    {"index": 20, "name": "Unresolved_20", "parent": None, "confidence": "unknown"},
]

CORE_BONE_COUNT = 19
FULL_BONE_COUNT = 21
MIRROR_PAIRS = [(5, 9), (6, 10), (7, 11), (8, 12), (13, 16), (14, 17), (15, 18)]


def _qmul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _qnorm(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    n = math.sqrt(sum(v * v for v in q))
    if n <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(v / n for v in q)  # type: ignore[return-value]


def _qrotate(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> tuple[float, float, float]:
    q = _qnorm(q)
    p = (v[0], v[1], v[2], 0.0)
    qc = (-q[0], -q[1], -q[2], q[3])
    r = _qmul(_qmul(q, p), qc)
    return (r[0], r[1], r[2])


def _vadd(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vdist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def skeleton_profile_metadata() -> dict[str, Any]:
    return {
        "name": "SecondSight_Human21_Provisional",
        "provisional": True,
        "core_indices_resolved": list(range(CORE_BONE_COUNT)),
        "unresolved_indices": [19, 20],
        "source_basis": [
            "Second Sight human_21 bind-pose geometry and bilateral symmetry",
            "Free Radical TS2 human skeleton ordering as a related-engine cross-check",
        ],
        "bones": HUMAN21_BONES,
    }


def compute_core_world_bind(ir: dict[str, Any]) -> dict[int, dict[str, list[float]]]:
    if ir.get("bone_count") != FULL_BONE_COUNT:
        raise ValueError(f"Expected a 21-bone IR, got {ir.get('bone_count')}.")

    tracks = {int(t["bone_index"]): t for t in ir["tracks"]}
    out: dict[int, dict[str, list[float]]] = {}

    for bone in HUMAN21_BONES[:CORE_BONE_COUNT]:
        idx = bone["index"]
        track = tracks[idx]
        sample = track["samples"][0]
        local_p = tuple(float(x) for x in sample["position"])
        local_q = _qnorm(tuple(float(x) for x in sample["rotation"]))
        parent = bone["parent"]

        if parent is None:
            world_p = local_p
            world_q = local_q
        else:
            parent_world = out[parent]
            pp = tuple(parent_world["position"])
            pq = tuple(parent_world["rotation"])
            world_p = _vadd(pp, _qrotate(pq, local_p))
            world_q = _qnorm(_qmul(pq, local_q))

        out[idx] = {
            "position": [float(x) for x in world_p],
            "rotation": [float(x) for x in world_q],
        }

    return out


def validate_human21_bind_geometry(ir: dict[str, Any]) -> dict[str, Any]:
    world = compute_core_world_bind(ir)

    mirror_errors: dict[str, float] = {}
    for right, left in MIRROR_PAIRS:
        rp = tuple(world[right]["position"])
        lp = tuple(world[left]["position"])
        mirrored_left = (-lp[0], lp[1], lp[2])
        mirror_errors[f"{right}:{left}"] = _vdist(rp, mirrored_left)

    ys = [world[i]["position"][1] for i in range(CORE_BONE_COUNT)]
    head_y = world[4]["position"][1]
    foot_y = (world[15]["position"][1] + world[18]["position"][1]) * 0.5

    return {
        "profile": skeleton_profile_metadata(),
        "world_bind_core": {str(k): v for k, v in world.items()},
        "mirror_errors": mirror_errors,
        "max_mirror_error": max(mirror_errors.values()) if mirror_errors else None,
        "root_y": world[0]["position"][1],
        "head_y": head_y,
        "average_foot_y": foot_y,
        "head_to_foot_height": head_y - foot_y,
        "y_extent": max(ys) - min(ys),
        "axis_hint": {
            "up": "+Y",
            "lateral": "X",
            "forward": "+Z (confirmed by run root motion, not bind pose alone)",
        },
        "unit_hint": {
            "guess": "meters-like",
            "reason": "resolved core bind skeleton height is approximately human-scale (~1.58 units head-to-foot)",
            "confirmed": False,
        },
    }
