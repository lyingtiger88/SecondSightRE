# SecondSightRE v0.5 Alpha

A read-only desktop extractor and reverse-engineering toolkit for Second Sight / Free Radical `P4CK`, `P5CK`, and `P8CK` PAK archives, built as the first stage of a Second Sight -> DCC/Unreal asset pipeline.

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

Not implemented yet:

- Decoding Second Sight proprietary `.raw`/asset payloads into meshes, skeletons, animations, materials, or level scene graphs.
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

## Validation status

- Byte-for-byte self-tests cover the **Second Sight PC P4CK 16-byte directory layout** and the legacy P4CK fallback.
- The P4CK parser has also been validated against a real Second Sight PC `animg2.pak`, where the archive directory and named animation entries were successfully enumerated and extracted.
- Proprietary `.raw` payload decoding is still in development.

No copyrighted game assets are included in this repository.

## Roadmap

- [x] Second Sight PC P4CK extraction
- [x] Archive browser
- [x] Search/filter
- [x] Batch extraction
- [x] Auto-open extracted folder
- [ ] RAW animation parser
- [ ] Bind-pose / skeleton parser
- [ ] Maya bridge
- [ ] Mesh parser
- [ ] Level parser
- [ ] Unreal Engine bridge
- [ ] Level Instance generation

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
