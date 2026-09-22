from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .extractor import archive_output_dir, extract_archive
from .plugins import FreeRadicalPakPlugin
from .raw_animation import RawAnimationError, format_raw_folder_report, format_raw_summary, inspect_raw_animation, scan_raw_folder
from .scanner import analyze_file, human_size, is_supported_pak_signature, scan_folder


class SecondSightExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SecondSightRE v0.6.1 Alpha - RAW Layout Profiler")
        self.geometry("1450x900")
        self.minsize(1100, 700)

        self.files = []
        self.current_entries = []
        self.current_raw = None
        self.current_raw_path: Path | None = None
        self.plugin = FreeRadicalPakPlugin()
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.pending_open: Path | None = None

        self.game_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")
        self.search_var = tk.StringVar()
        self.scope_var = tk.StringVar(value="Both")
        self.count_var = tk.StringVar()
        self.auto_open_var = tk.BooleanVar(value=True)

        self._ui()
        self.search_var.trace_add("write", lambda *_: self._filter())
        self.scope_var.trace_add("write", lambda *_: self._filter())
        self.bind_all("<Control-f>", self._focus_search)
        self.bind_all("<Escape>", self._clear_search_event)
        self.after(80, self._pump)

    def _ui(self):
        top = ttk.LabelFrame(self, text="Second Sight project")
        top.pack(fill="x", padx=8, pady=(8, 4))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="Game folder:").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(top, textvariable=self.game_var).grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        ttk.Button(top, text="Browse Game Folder", command=self._choose_game).grid(row=0, column=2, padx=6, pady=5)
        ttk.Label(top, text="Output folder:").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Entry(top, textvariable=self.out_var).grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        ttk.Button(top, text="Browse Output Folder", command=self._choose_out).grid(row=1, column=2, padx=6, pady=5)
        ttk.Checkbutton(top, text="Open extracted folder when extraction finishes", variable=self.auto_open_var).grid(row=2, column=1, sticky="w", padx=6, pady=(0, 6))
        ttk.Button(top, text="Open Extracted Folder", command=self.open_extracted_folder).grid(row=2, column=2, padx=6, pady=(0, 6))

        bar = ttk.Frame(self); bar.pack(fill="x", padx=8, pady=4)
        self.action_buttons = []
        for text, cmd in (
            ("1. Scan Game", self.start_scan),
            ("2. Inspect Selected PAK", self.inspect_selected),
            ("3. Extract Selected PAK(s)", self.extract_selected),
            ("Extract ALL Detected PAKs", self.extract_all),
            ("Binary Analyze", self.analyze_selected),
            ("Inspect RAW / Bind Pose", self.inspect_raw),
            ("Analyze RAW Folder...", self.analyze_raw_folder),
            ("Export Scan Manifest", self.export_manifest),
        ):
            b = ttk.Button(bar, text=text, command=cmd); b.pack(side="left", padx=(0, 6)); self.action_buttons.append(b)

        status = ttk.Frame(self); status.pack(fill="x", padx=8, pady=(0, 4))
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100); self.progress.pack(side="left", fill="x", expand=True)
        ttk.Label(status, textvariable=self.status_var, width=34, anchor="e").pack(side="right", padx=(8, 0))

        search = ttk.Frame(self); search.pack(fill="x", padx=8, pady=(0, 5))
        ttk.Label(search, text="Search:").pack(side="left")
        self.search_entry = ttk.Entry(search, textvariable=self.search_var); self.search_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(search, text="Scope:").pack(side="left")
        ttk.Combobox(search, textvariable=self.scope_var, state="readonly", width=14, values=("Both", "Files", "PAK Contents")).pack(side="left", padx=6)
        ttk.Button(search, text="Clear", command=self.clear_search).pack(side="left")
        ttk.Label(search, textvariable=self.count_var, width=26, anchor="e").pack(side="right")

        paned = ttk.Panedwindow(self, orient="horizontal"); paned.pack(fill="both", expand=True, padx=8, pady=4)
        left, right = ttk.Frame(paned), ttk.Frame(paned); paned.add(left, weight=3); paned.add(right, weight=2)
        cols = ("path", "ext", "size", "type", "entropy")
        self.file_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")
        for c, label, width in (("path","Relative Path",430),("ext","Ext",60),("size","Size",100),("type","Detected Type",210),("entropy","Entropy",80)):
            self.file_tree.heading(c, text=label); self.file_tree.column(c, width=width, stretch=c in ("path","type"))
        ys = ttk.Scrollbar(left, orient="vertical", command=self.file_tree.yview); xs = ttk.Scrollbar(left, orient="horizontal", command=self.file_tree.xview)
        self.file_tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.file_tree.grid(row=0,column=0,sticky="nsew"); ys.grid(row=0,column=1,sticky="ns"); xs.grid(row=1,column=0,sticky="ew")
        left.rowconfigure(0, weight=1); left.columnconfigure(0, weight=1)

        self.tabs = ttk.Notebook(right); self.tabs.pack(fill="both", expand=True)
        pakf, rawf, detf, hexf, strf = (ttk.Frame(self.tabs) for _ in range(5))
        for f, t in ((pakf,"PAK Contents"),(rawf,"RAW Inspector"),(detf,"Details"),(hexf,"Hex Preview"),(strf,"Strings")): self.tabs.add(f, text=t)
        pcols = ("idx","name","offset","size","id","extra")
        self.pak_tree = ttk.Treeview(pakf, columns=pcols, show="headings")
        for c,label,width in (("idx","#",50),("name","Name / Hash",320),("offset","Offset",105),("size","Size",90),("id","ID",105),("extra","Extra",105)):
            self.pak_tree.heading(c,text=label); self.pak_tree.column(c,width=width,stretch=c=="name")
        pys = ttk.Scrollbar(pakf, orient="vertical", command=self.pak_tree.yview); pxs = ttk.Scrollbar(pakf, orient="horizontal", command=self.pak_tree.xview)
        self.pak_tree.configure(yscrollcommand=pys.set, xscrollcommand=pxs.set)
        self.pak_tree.grid(row=0,column=0,sticky="nsew"); pys.grid(row=0,column=1,sticky="ns"); pxs.grid(row=1,column=0,sticky="ew")
        pakf.rowconfigure(0, weight=1); pakf.columnconfigure(0, weight=1)
        self.raw_tab = rawf
        rawbar = ttk.Frame(rawf); rawbar.pack(fill="x", padx=4, pady=4)
        ttk.Button(rawbar, text="Open RAW...", command=self.inspect_raw).pack(side="left", padx=(0,6))
        ttk.Button(rawbar, text="Analyze RAW Folder...", command=self.analyze_raw_folder).pack(side="left", padx=(0,6))
        ttk.Button(rawbar, text="Export Current RAW JSON", command=self.export_current_raw_json).pack(side="left")
        self.raw_summary = self._text(rawf, wrap="word", font=("Consolas",9))
        self.details = self._text(detf, wrap="word")
        self.hex_view = self._text(hexf, wrap="none", font=("Consolas",9))
        self.strings_view = self._text(strf, wrap="none", font=("Consolas",9))

        lf = ttk.LabelFrame(self, text="Log"); lf.pack(fill="x", padx=8, pady=(4,8))
        self.log = tk.Text(lf, height=9, wrap="word", state="disabled"); lys = ttk.Scrollbar(lf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lys.set); self.log.pack(side="left", fill="both", expand=True); lys.pack(side="right", fill="y")
        self._log("Ready. Real PAK extraction is enabled for P4CK/P5CK/P8CK.")
        self._log("v0.6.1 RAW profiler: profiles real Second Sight RAW headers without requiring ANR1 magic.")

    @staticmethod
    def _text(parent, **kw):
        f = ttk.Frame(parent); f.pack(fill="both", expand=True)
        t = tk.Text(f, **kw); ys = ttk.Scrollbar(f, orient="vertical", command=t.yview); xs = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set); t.grid(row=0,column=0,sticky="nsew"); ys.grid(row=0,column=1,sticky="ns"); xs.grid(row=1,column=0,sticky="ew")
        f.rowconfigure(0,weight=1); f.columnconfigure(0,weight=1); return t

    def _log(self, s):
        self.log.configure(state="normal"); self.log.insert("end", s+"\n"); self.log.see("end"); self.log.configure(state="disabled")

    @staticmethod
    def _set_text(w, s):
        w.delete("1.0","end"); w.insert("1.0",s)

    def _choose_game(self):
        p = filedialog.askdirectory(title="Select Second Sight game folder")
        if p:
            self.game_var.set(p)
            if not self.out_var.get().strip(): self.out_var.set(str(Path(p).parent / "SecondSight_Extracted"))

    def _choose_out(self):
        p = filedialog.askdirectory(title="Select output folder")
        if p: self.out_var.set(p)

    @staticmethod
    def _open_folder(path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"): os.startfile(str(path))
        elif sys.platform == "darwin": subprocess.Popen(["open", str(path)])
        else: subprocess.Popen(["xdg-open", str(path)])

    def open_extracted_folder(self):
        out = self.out_var.get().strip()
        if not out: return messagebox.showwarning("Missing output", "Select an output folder first.")
        target = Path(out)/"PAK_Extracted"
        if not target.exists(): target = Path(out)
        try: self._open_folder(target)
        except Exception as exc: messagebox.showerror("Open folder failed", str(exc))

    def _set_busy(self, busy, label=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in self.action_buttons: b.configure(state=state)
        if busy: self.progress.configure(mode="indeterminate"); self.progress.start(12)
        else: self.progress.stop(); self.progress.configure(mode="determinate", value=100)
        self.status_var.set(label or ("Working..." if busy else "Ready"))

    def start_scan(self):
        root = self.game_var.get().strip()
        if not root or not Path(root).is_dir(): return messagebox.showwarning("Missing game folder", "Select the Second Sight game folder.")
        if self.busy: return
        self._set_busy(True,"Scanning..."); self._log(f"Scanning: {root}")
        def work():
            try: self.q.put(("scan_done", scan_folder(root)))
            except Exception as exc: self.q.put(("error", f"Scan failed: {exc}"))
        threading.Thread(target=work,daemon=True).start()

    def _scan_done(self, files):
        self.files = files; self.current_entries = []; self._set_busy(False, f"Scanned {len(files)} files")
        self._filter(); self._log(f"Detected {sum(is_supported_pak_signature(f.signature) for f in files)} supported PAK(s).")

    def _terms(self): return [x for x in self.search_var.get().lower().split() if x]
    def _file_match(self, f):
        text = f"{f.rel_path} {f.name} {f.ext} {f.signature} {f.size}".lower(); return all(t in text for t in self._terms())
    def _entry_match(self, i, e):
        text = f"{i} {e.name} 0x{e.offset:x} {e.offset} {e.size} {e.file_id} {e.extra}".lower(); return all(t in text for t in self._terms())

    def _refresh_files(self):
        selected = set(self.file_tree.selection()); self.file_tree.delete(*self.file_tree.get_children()); shown = 0
        use = self.scope_var.get() in ("Both","Files")
        for i,f in enumerate(self.files):
            if use and not self._file_match(f): continue
            iid=str(i); self.file_tree.insert("","end",iid=iid,values=(f.rel_path,f.ext or "(none)",human_size(f.size),f.signature,"" if f.entropy is None else f"{f.entropy:.3f}"))
            if iid in selected: self.file_tree.selection_add(iid)
            shown += 1
        return shown

    def _refresh_entries(self):
        self.pak_tree.delete(*self.pak_tree.get_children()); shown=0; use=self.scope_var.get() in ("Both","PAK Contents")
        for i,e in enumerate(self.current_entries):
            if use and not self._entry_match(i,e): continue
            self.pak_tree.insert("","end",values=(i,e.name,f"0x{e.offset:08X}",e.size,"" if e.file_id is None else f"0x{e.file_id:08X}","" if e.extra is None else f"0x{e.extra:08X}")); shown+=1
        return shown

    def _filter(self):
        if not hasattr(self,"file_tree"): return
        a,b=self._refresh_files(),self._refresh_entries(); q=self.search_var.get().strip(); scope=self.scope_var.get()
        if not q: self.count_var.set("")
        elif scope=="Files": self.count_var.set(f"{a}/{len(self.files)} file(s)")
        elif scope=="PAK Contents": self.count_var.set(f"{b}/{len(self.current_entries)} entry(s)")
        else: self.count_var.set(f"{a}/{len(self.files)} files | {b}/{len(self.current_entries)} entries")

    def clear_search(self): self.search_var.set(""); self.search_entry.focus_set()
    def _focus_search(self,event=None): self.search_entry.focus_set(); self.search_entry.selection_range(0,"end"); return "break"
    def _clear_search_event(self,event=None):
        if self.search_var.get(): self.clear_search(); return "break"
    def _selected_indices(self): return [int(x) for x in self.file_tree.selection()]
    def _first(self):
        ids=self._selected_indices(); return self.files[ids[0]] if ids else None

    def inspect_selected(self):
        f=self._first()
        if not f: return messagebox.showinfo("Nothing selected","Select a PAK archive.")
        try: h,entries=self.plugin.inspect(Path(f.path))
        except Exception as exc: self._log(f"PAK parse failed: {f.rel_path}: {exc}"); return messagebox.showerror("PAK parse failed",str(exc))
        self.current_entries=entries; self._filter(); self.tabs.select(0)
        self._set_text(self.details, f"Archive: {f.path}\nMagic: {h.magic}\nVariant: {h.variant}\nFile size: {h.file_size} bytes ({human_size(h.file_size)})\nDirectory offset: 0x{h.directory_offset:X}\nDirectory size/count: {h.directory_size}\nFilenames offset: 0x{h.filenames_offset:X}\nParsed entries: {len(entries)}\n\nEach extracted output is read from its own archive offset and size.\n")
        self._log(f"Inspected {f.rel_path}: {h.magic}, {len(entries)} entries.")

    def inspect_raw(self):
        f = self._first()
        path = Path(f.path) if f and Path(f.path).suffix.lower() == ".raw" else None
        if path is None:
            chosen = filedialog.askopenfilename(
                title="Select Second Sight RAW",
                initialdir=self.out_var.get().strip() or self.game_var.get().strip() or None,
                filetypes=[("RAW files","*.raw"),("All files","*.*")],
            )
            if not chosen: return
            path = Path(chosen)
        try:
            raw = inspect_raw_animation(path, decode_payload=True, allow_unknown_magic=True)
        except RawAnimationError as exc:
            self._log(f"RAW parse failed: {path}: {exc}")
            return messagebox.showerror("RAW parse failed", str(exc))
        except Exception as exc:
            self._log(f"RAW inspect error: {path}: {exc}")
            return messagebox.showerror("RAW inspect error", str(exc))
        self.current_raw = raw
        self.current_raw_path = path
        self._set_text(self.raw_summary, format_raw_summary(raw))
        self.tabs.select(self.raw_tab)
        self._log(f"RAW inspected: {path.name} -> {raw.kind}, {raw.num_bones} track(s), {raw.num_ids} ID/sample(s)")

    def analyze_raw_folder(self):
        chosen = filedialog.askdirectory(
            title="Select folder containing extracted RAW files",
            initialdir=self.out_var.get().strip() or self.game_var.get().strip() or None,
        )
        if not chosen: return
        try:
            report = scan_raw_folder(Path(chosen), decode_payload=True)
        except Exception as exc:
            self._log(f"RAW folder analysis failed: {exc}")
            return messagebox.showerror("RAW folder analysis failed", str(exc))
        out_root = Path(self.out_var.get().strip()) if self.out_var.get().strip() else Path(chosen)
        out_root.mkdir(parents=True, exist_ok=True)
        report_path = out_root / "raw_analysis_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        self.current_raw = None
        self.current_raw_path = None
        self._set_text(self.raw_summary, format_raw_folder_report(report) + f"\nReport saved: {report_path}\n")
        self.tabs.select(self.raw_tab)
        self._log(f"RAW folder analyzed: {report['layout_candidates']} layout candidate(s), {report['parsed_layout']} parsed / {report['total_raw_files']} RAW -> {report_path}")
        messagebox.showinfo(
            "RAW folder analysis complete",
            f"Layout candidates: {report['layout_candidates']}\nParsed layouts: {report['parsed_layout']}\n"
            f"Total RAW: {report['total_raw_files']}\nParse errors: {report['parse_errors']}\n\nReport: {report_path}"
        )

    def export_current_raw_json(self):
        if self.current_raw is None:
            return messagebox.showinfo("No RAW loaded", "Inspect a RAW file first.")
        chosen = filedialog.asksaveasfilename(
            title="Export RAW inspection JSON",
            defaultextension=".json",
            initialfile=(self.current_raw_path.stem if self.current_raw_path else "raw") + "_raw.json",
            filetypes=[("JSON","*.json"),("All files","*.*")],
        )
        if not chosen: return
        Path(chosen).write_text(json.dumps(self.current_raw.to_dict(include_samples=False), indent=2, ensure_ascii=False), encoding="utf-8")
        self._log(f"RAW inspection JSON saved: {chosen}")
        messagebox.showinfo("RAW JSON saved", chosen)

    def analyze_selected(self):
        ids=self._selected_indices()
        if not ids: return messagebox.showinfo("Nothing selected","Select one or more files.")
        for n,i in enumerate(ids):
            f=self.files[i]
            try:
                r=analyze_file(f.path); f.signature=r["signature"]; f.entropy=r["entropy"]
                if self.file_tree.exists(str(i)): self.file_tree.item(str(i),values=(f.rel_path,f.ext or "(none)",human_size(f.size),f.signature,f"{f.entropy:.3f}"))
                if n==0:
                    self._set_text(self.details,f"Path: {f.path}\nSize: {f.size} bytes ({human_size(f.size)})\nDetected type: {f.signature}\nSample entropy: {f.entropy:.4f} / 8.0\nAnalyzed sample: {r['sample_size']} bytes\n")
                    self._set_text(self.hex_view,r["hex"]); self._set_text(self.strings_view,"\n".join(r["strings"])); self.tabs.select(1)
            except Exception as exc: self._log(f"Analyze failed: {f.rel_path}: {exc}")

    def _out(self):
        out=self.out_var.get().strip()
        if not out: messagebox.showwarning("Missing output","Select an output folder."); return None
        Path(out).mkdir(parents=True,exist_ok=True); return Path(out)

    def extract_selected(self):
        ids=self._selected_indices()
        if not ids: return messagebox.showinfo("Nothing selected","Select one or more PAK archives.")
        tasks=[(self.files[i].path,self.files[i].rel_path) for i in ids if is_supported_pak_signature(self.files[i].signature)]
        if not tasks: return messagebox.showwarning("No supported archives","Selection has no P4CK/P5CK/P8CK archive.")
        self._start_extract(tasks)

    def extract_all(self):
        tasks=[(f.path,f.rel_path) for f in self.files if is_supported_pak_signature(f.signature)]
        if not tasks: return messagebox.showwarning("No PAKs detected","Scan the game folder first or no supported PAKs were found.")
        self._start_extract(tasks)

    def _start_extract(self,tasks):
        out=self._out()
        if not out or self.busy: return
        self.pending_open=archive_output_dir(out,tasks[0][1]) if len(tasks)==1 else out/"PAK_Extracted"
        self._set_busy(True,f"Extracting {len(tasks)} archive(s)...")
        def work():
            ok=failed=0
            for n,(archive,rel) in enumerate(tasks,1):
                try:
                    self.q.put(("log",f"[{n}/{len(tasks)}] Extracting {rel}")); m=extract_archive(Path(archive),rel,out,log=lambda s:self.q.put(("log",s)))
                    self.q.put(("log",f"  OK: {m['entry_count']} entries / {m['header']['magic']}")); ok+=1
                except Exception as exc: failed+=1; self.q.put(("log",f"  ERROR: {rel}: {exc}"))
            self.q.put(("extract_done",ok,failed))
        threading.Thread(target=work,daemon=True).start()

    def _extract_done(self,ok,failed):
        self._set_busy(False,f"Finished: {ok} OK, {failed} failed"); self._log(f"Extraction finished: {ok} OK, {failed} failed.")
        messagebox.showinfo("Extraction complete",f"Extracted {ok} archive(s).\nFailed: {failed}\n\nOutput: {self.out_var.get().strip()}")
        if ok and self.auto_open_var.get() and self.pending_open:
            try: self._open_folder(self.pending_open)
            except Exception as exc: self._log(f"Auto-open failed: {exc}")
        self.pending_open=None

    def export_manifest(self):
        if not self.files: return messagebox.showinfo("No scan data","Scan the game folder first.")
        out=self._out()
        if not out: return
        p=out/"second_sight_scan_manifest.json"
        p.write_text(json.dumps({"game_root":self.game_var.get().strip(),"file_count":len(self.files),"supported_pak_count":sum(is_supported_pak_signature(f.signature) for f in self.files),"files":[f.to_dict() for f in self.files]},indent=2,ensure_ascii=False),encoding="utf-8")
        self._log(f"Scan manifest saved: {p}"); messagebox.showinfo("Manifest saved",str(p))

    def _pump(self):
        try:
            while True:
                e=self.q.get_nowait(); kind=e[0]
                if kind=="log": self._log(e[1])
                elif kind=="scan_done": self._scan_done(e[1])
                elif kind=="extract_done": self._extract_done(e[1],e[2])
                elif kind=="error": self._set_busy(False,"Error"); self._log(e[1]); messagebox.showerror("Error",e[1])
        except queue.Empty: pass
        self.after(80,self._pump)
