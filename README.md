# SecondSightRE v0.6.2 Alpha — Second Sight RAW Header Profiler

A read-only desktop extractor for Second Sight / Free Radical `P4CK`, `P5CK`, and `P8CK` PAK archives, built as the first stage of a Second Sight -> DCC/Unreal asset pipeline.

This build **does not copy the whole `.pak` file as "extraction"**. It parses the archive header and directory table, validates each entry's offset/size, then reads and writes each entry individually.

## Current scope

Implemented now:

- Detect `P4CK`, `P5CK`, and `P8CK` archives by magic.
- Parse archive directory entries. For **Second Sight PC P4CK**, this build uses the real 16-byte file-info rows plus the trailing null-terminated filename table. A legacy 60-byte P4CK layout is retained only as an auto-detected fallback for related titles.
- Show archive contents before extraction: name/hash, offset, size, ID, and extra field.
- Extract one selected PAK, multiple selected PAKs, or every detected PAK below the game folder.
- Preserve named paths where the format provides them.
- For `P5CK`, try adjacent `.c2n` lookup files, optional embedded filename data, then safe hash-based fallback names.
- Write `__archive_manifest.json` beside every extracted archive.
- Protect output from unsafe archive paths (`..`, drive prefixes, invalid Windows characters).
- Binary analysis tab with signature, entropy, strings, and hex preview.
- CLI mode for repeatable/batch extraction.
- Second Sight PC RAW header profiler based on the real 611-file validation set: FF*8 sentinel, observed header fields at `0x08/0x0C/0x10`, bone count at `0x14`, mirrored bone count at `0x24`, and the dword table beginning at `0x3C`.

Not implemented yet:

- Production-ready decoding of Second Sight `.raw` payloads into bone transforms/keyframes. v0.6.2 fixes the header model first; payload semantics are still under reverse engineering.
- FBX/glTF export.
- Maya bridge.
- Unreal Level Instance importer.

Those are the next layer **after** PAK extraction. The archive extractor is intentionally kept separate from asset decoders so the same extracted data can feed Maya, Cascadeur, and Unreal workflows.

## Run the GUI

### Windows

1. Install Python 3.11+.
2. Double-click `run_windows.bat`, or run:

```bat
python main.py
```

No third-party Python packages are required for normal runtime; the GUI uses Tkinter from the standard Python installation.

### GUI workflow

1. Choose the Second Sight game folder.
2. Choose an output folder.
3. Click **Scan Game**.
4. Select a detected PAK and click **Inspect Selected PAK** to confirm that the directory is actually parsed.
5. Click **Extract Selected PAK(s)** or **Extract ALL Detected PAKs**.

Extracted data is stored under:

```text
<output>/PAK_Extracted/<original pak path without .pak>/
```

Each archive directory also contains:

```text
__archive_manifest.json
```

The manifest records archive magic/variant and every entry's original offset, size, ID/hash (when present), extra field, and output path.

## CLI examples

List an archive without extracting it:

```bat
python main.py --list "C:\Games\Second Sight\pak\anim\animg2.pak"
```

Extract one archive:

```bat
python main.py --extract "C:\Games\Second Sight\pak\anim\animg2.pak" -o "D:\SecondSightDump"
```

Scan a full game directory and extract every supported PAK:

```bat
python main.py --extract-all "C:\Games\Second Sight" -o "D:\SecondSightDump"
```

## Build a Windows EXE

Run:

```bat
build_exe_windows.bat
```

This installs PyInstaller if needed and builds a windowed one-file executable named `SecondSightExtractor.exe`.

## Research basis

The archive implementation was informed by public reverse-engineering work, especially:

- DKDave's QuickBMS `Second_Sight_(PC)_PAK.bms`, which documents the PC Second Sight P4CK layout as a 16-byte header, a file-info table made of 16-byte entries, and a filename string table.
- Game Extractor's `Plugin_PAK_P4CK`, which independently implements the same Second Sight PC layout.
- The archived XeNTaX Second Sight format discussion, which records the same four fields per file-info row: relative filename offset, file offset, file size, and unknown/extra.
- GoomiiV2 / TS-ReSplit for broader Free Radical PAK family research.
- Public Second Sight depot listings confirming dedicated PAKs for animation, story/level data, scripts, objects, sound, and related data.

References:

- https://github.com/DKDave/Scripts/blob/master/QuickBMS/PC/Second_Sight_(PC)_PAK.bms
- https://github.com/wattostudios/GameExtractor/blob/master/src/org/watto/ge/plugin/archive/Plugin_PAK_P4CK.java
- https://github.com/XeNTaXBackup/XeNTaXBackup.github.io/blob/main/markdown/Second%20Sight_1192.md
- https://github.com/GoomiiV2/TS-ReSplit
- https://steamdb.info/depot/11551/apps/

The extractor code in this package is an independent Python implementation. A copy of the TS-ReSplit MIT license is included in `THIRD_PARTY_NOTICES.txt` as attribution for the public research/code inspected while implementing the format reader.

## Important limitation

This build has byte-for-byte self-tests for the **Second Sight PC P4CK 16-byte directory layout** and the legacy P4CK fallback. Your screenshot exposed the old parser bug (`9488` was wrongly treated as a 60-byte table); v0.5 fixes that. The next validation step is to run **Inspect Selected PAK** on your real `animg2.pak` and verify that the file list appears.

No copyrighted game assets are included.

## Search (v0.5)

The GUI now has a live Search bar with three scopes:

- **Both**: filters the scanned game files and the currently inspected PAK entries.
- **Files**: filters only the left file/archive list.
- **PAK Contents**: filters only entries inside the inspected archive.

Search is case-insensitive and supports multiple space-separated terms (all terms must match).
You can search by path/name, extension/type, offsets, sizes, IDs/hashes and extra values.
Press **Ctrl+F** to focus Search and **Esc** to clear it.


## v0.5

- Added **Open extracted folder when extraction finishes** checkbox (enabled by default).
- Single-PAK extraction opens that PAK's exact output directory.
- Batch/Extract All opens the common `PAK_Extracted` directory.
- Added a manual **Open Extracted Folder** button.

## ANR1 RAW Inspector (v0.6)

The new **Inspect RAW / Bind Pose** action opens Free Radical `ANR1` animation-family RAW files and reports:

- magic/version and the two unknown header fields currently tracked for research;
- ID/sample count and bone/track count;
- every 32-byte track record (`unknown`, `flags`, `NumFrames`, `NumKeyframes`);
- observed track roles (`0 = bind`, `2 = child`, `8 = root`);
- payload offsets/sizes and remaining trailing bytes;
- packed 8-byte quaternion decoding (`4 x uint16` mapped from `[0,65535]` to `[-1,1]`);
- bind-pose frame decoding and root/child animation payload previews;
- JSON export of inspection metadata.

CLI example:

```bat
python main.py --inspect-raw "D:\SecondSightDump\anim\data\g2\stand_i.raw"
```

Batch-analyze an extracted RAW tree and write a compact JSON report:

```bat
python main.py --scan-raw "D:\SecondSightDump" -o "D:\SecondSightReports"
```

This code is deliberately **validation-first**. The layout is based on public Free Radical animation research in TS-ReSplit and synthetic byte-for-byte tests. Before Maya/FBX export, the next task is to run this inspector across the extracted Second Sight animation + skeleton samples and resolve any variant/trailing-data cases.

Additional research reference:

- https://github.com/GoomiiV2/TS-ReSplit/blob/master/TS%20ReSplit/Assets/Scripts/TSLoader/Ts2Animation.cs

### v0.6 test coverage

Run:

```bat
python raw_selftest.py
```

The synthetic test validates both a normal ANR1 animation (root + child tracks) and a bind pose, including packed quaternion decoding and exact payload consumption.


## v0.6.1 validation hotfix

The first v0.6 test against 611 real Second Sight RAW files showed that none use the assumed `ANR1` magic. v0.6.1 therefore stops treating `ANR1` as a requirement and adds a format-neutral RAW header/layout profiler. `Analyze RAW Folder...` now records signature counts, representative 96-byte headers, little-endian integer/float views, and an experimental TS2-style layout plausibility score. Unknown magic is only parsed when the structural checks are strong enough; otherwise the file is profiled, not guessed.


## v0.6.2 — real Second Sight RAW header model

The second real-game validation report changed the RAW work substantially. All 611 tested RAW files begin with eight `FF` bytes, not `ANR1`. The previous TS2-style probe also produced false positives because it read the dword at `0x3C` as a bone count; on most human animations that value is simply the first word of a table and equals `4`.

Representative real files show a stable Second Sight header pattern:

```text
0x00  FF FF FF FF FF FF FF FF   sentinel
0x08  u32 field_08              meaning not confirmed
0x0C  u32 field_0C              timeline/duration-like (tentative)
0x10  u32 field_10              sample/key-count-like (tentative)
0x14  u32 bone_count
0x18  12 bytes reserved/zero in common samples
0x24  u32 bone_count mirror
0x28  20 bytes reserved/zero in common samples
0x3C  dword table / payload metadata begins
```

Examples from the real validation set include human animation headers with `bone_count=21`, ragdoll bind-pose headers with `bone_count=23`, and weapon/static-pose data with `bone_count=8`. The GUI and CLI now report these observed fields directly and no longer label TS2-style false positives as decoded Second Sight animations.

The old `app/raw_animation.py` ANR1/TS2 parser is retained as a research reference, but it is no longer the default Second Sight RAW parser.

Run the new profiler:

```bat
python main.py --scan-raw "D:\\SecondSightDump\\PAK_Extracted\\pak\\anim" -o "D:\\SecondSightReports"
```

Run the header self-test:

```bat
python ssraw_selftest.py
```
