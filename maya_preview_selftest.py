from __future__ import annotations

from app.human21_profile import HUMAN21_BONES, skeleton_profile_metadata
from app.maya_preview import build_maya_preview_script


def make_track(index: int, kind: str):
    pos = [0.0, 0.1 if index < 5 else 0.05, 0.0]
    if index == 0:
        pos = [0.0, 1.15, 0.0]
    samples = [{"time": 0, "position": pos, "rotation": [0.0, 0.0, 0.0, 1.0]}]
    if kind == "Animation-like":
        samples.append({"time": 4, "position": pos, "rotation": [0.0, 0.0, 0.0, 1.0]})
    return {
        "bone_index": index,
        "flags": 8 if index == 0 and kind == "Animation-like" else 12,
        "unknown": 0,
        "position_codec": "synthetic",
        "rotation_codec": "synthetic",
        "position_animated": index == 0 and kind == "Animation-like",
        "rotation_animated": kind == "Animation-like",
        "samples": samples,
    }


def make_ir(kind: str):
    animation = kind == "Animation-like"
    return {
        "schema": "secondsight.animation_ir.v1",
        "source_format": "Second Sight PC RAW",
        "source_path": "synthetic.raw",
        "kind": kind,
        "field_08": 17,
        "frame_count": 5 if animation else 1,
        "key_count": 2 if animation else 1,
        "key_times": [0, 4] if animation else [0],
        "bone_count": 21,
        "tracks": [make_track(i, kind) for i in range(21)],
    }


def main():
    profile = skeleton_profile_metadata()
    assert len(HUMAN21_BONES) == 21
    assert profile["bones"][0]["name"] == "Root"
    assert profile["bones"][5]["parent"] == 2
    assert profile["bones"][13]["parent"] == 0
    assert profile["unresolved_indices"] == [19, 20]

    bind_ir = make_ir("Static / Pose-like")
    anim_ir = make_ir("Animation-like")
    script = build_maya_preview_script(bind_ir, anim_ir)
    compile(script, "generated_maya_preview.py", "exec")

    assert "SS_Right_Shoulder_1" in script
    assert "indices 19 and 20 are intentionally omitted" in script
    assert "UNIT_SCALE" in script
    assert "build_preview()" in script

    print("SECOND SIGHT MAYA PREVIEW SELFTEST PASSED")


if __name__ == "__main__":
    main()
