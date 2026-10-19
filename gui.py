"""
Graphical interface for vtt.py (Video-to-Text) -- three independent steps:

  1. Download tab:   a video link L -> downloaded into folder A
  2. Transcribe tab: video(s) in any folder (typically folder A) -> TXT/VTT in folder B
  3. Correct tab:    transcript(s) in any folder (typically folder B) -> corrected
                      copies, fixing recurring Whisper mis-hearings via a glossary
                      (see transcript_corrector.py / correctionglossary.md)

Each tab runs on its own background thread with its own progress log, so the steps
are fully independent -- you can download several videos without transcribing right
away, transcribe files that were never downloaded through this app, or correct
transcripts that came from somewhere else entirely.

Run with:  python gui.py

VERSIONING: this file carries its own __version__, independent of vtt.py's and
transcript_corrector.py's -- each file is versioned on its own schedule now, bumped
only when that specific file changes. See version.py for the current version of every
component in one place and the combined changelog. The window title bar shows this
file's own version; the Transcribe and Correct tabs' status bars additionally show
vtt.VERSION and transcript_corrector.VERSION respectively, since those are the engines
actually doing the work in each tab.
"""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import transcript_corrector
import vtt

__version__ = "1.5.0"

MODEL_SIZES = ["tiny", "base", "small", "medium", "large"]
FORMATS = ["both", "txt", "vtt"]
ALL_FILES_LABEL = "-- All files in this folder --"
# Transcribe tab's "Speakers" option: vtt.py mode key -> text shown in the dropdown.
SPEAKER_LABELS = {
    "off": "All speakers",
    "main": "Main narrator only",
    "label": "Label other speakers",
}


def _existing_dir_or_none(path):
    """Return `path` stripped if it's a real, existing directory, else None.

    Used as `initialdir` for every "Browse..." folder dialog so it opens showing the
    folder that's currently set in that field, instead of whatever default location Tk
    otherwise falls back to. Returning None (rather than "") when the field is empty or
    points at a folder that no longer exists lets Tk fall back to its own default for
    that case, rather than erroring on a bad initialdir.
    """
    path = (path or "").strip()
    return path if path and os.path.isdir(path) else None

# Settings are remembered next to this script, so the app is portable if you move the folder.
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "download": {
        "link": "",
        "repo": "repository_A",
        "naming_mode": "original",
        "custom_name": "",
    },
    "transcribe": {
        "folder": "repository_A",
        "selected_file": ALL_FILES_LABEL,
        "outdir": "folder_B",
        "lang": "es",
        "translate": False,
        "model": "base",
        "format": "both",
        "speakers": "off",
        "hf_token": "",
    },
    "correct": {
        "folder": "folder_B",
        "selected_file": ALL_FILES_LABEL,
        "outdir": "folder_B_corrected",
    },
}


def _fresh_defaults():
    return {section: dict(values) for section, values in DEFAULT_SETTINGS.items()}


def _migrate_flat_settings(saved):
    """Upgrades the old single-task settings.json (flat keys) into the new multi-task shape."""
    migrated = _fresh_defaults()
    old_input = saved.get("input", "")
    repo = saved.get("repo", DEFAULT_SETTINGS["download"]["repo"])
    migrated["download"]["repo"] = repo
    if vtt.is_url(old_input):
        migrated["download"]["link"] = old_input
    migrated["transcribe"]["folder"] = repo
    migrated["transcribe"]["outdir"] = saved.get("outdir", DEFAULT_SETTINGS["transcribe"]["outdir"])
    migrated["transcribe"]["lang"] = saved.get("lang", DEFAULT_SETTINGS["transcribe"]["lang"])
    migrated["transcribe"]["translate"] = saved.get("translate", DEFAULT_SETTINGS["transcribe"]["translate"])
    migrated["transcribe"]["model"] = saved.get("model", DEFAULT_SETTINGS["transcribe"]["model"])
    migrated["transcribe"]["format"] = saved.get("format", DEFAULT_SETTINGS["transcribe"]["format"])
    return migrated


def load_settings():
    """Reads previously-used parameter values, if any. Missing/corrupt/old-format file falls back safely.
    Handles both the very old flat schema and the two-task schema (before the Correct tab existed) --
    either way, any section absent from the saved file just falls back to its defaults."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _fresh_defaults()

    if "download" not in saved or "transcribe" not in saved:
        return _migrate_flat_settings(saved)

    settings = _fresh_defaults()
    for section in settings:
        settings[section].update(saved.get(section, {}))
    return settings


def save_section(section, values):
    """Best-effort save of just one task's section ('download' or 'transcribe'), preserving the other."""
    settings = load_settings()
    settings[section] = values
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


class LogPanel:
    """A ScrolledText log fed from a background thread via a thread-safe Queue."""

    def __init__(self, parent, root, height=14):
        self.root = root
        self.queue = queue.Queue()
        self.widget = scrolledtext.ScrolledText(parent, height=height, state="disabled", wrap="word")

    def log(self, message):
        """Safe to call from a background thread."""
        self.queue.put(str(message) + "\n")

    def clear(self):
        self.widget.configure(state="normal")
        self.widget.delete("1.0", "end")
        self.widget.configure(state="disabled")

    def poll(self, on_idle=None, still_running=None):
        try:
            while True:
                text = self.queue.get_nowait()
                self.widget.configure(state="normal")
                self.widget.insert("end", text)
                self.widget.see("end")
                self.widget.configure(state="disabled")
        except queue.Empty:
            pass

        if still_running and still_running():
            self.root.after(100, lambda: self.poll(on_idle, still_running))
        elif on_idle:
            on_idle()


class DownloadTab:
    """Step 1: a video link L -> downloaded into folder A. Independent of the Transcribe tab."""

    def __init__(self, parent, root, settings):
        self.root = root
        self.worker_thread = None

        # The link field always starts blank on launch, regardless of the saved settings --
        # a saved link is meant to be pasted fresh each run, not left sitting in the box (see
        # v1.4.1 changelog entry in version.py). Folder/naming choices are still remembered.
        self.link_var = tk.StringVar(value="")
        self.repo_var = tk.StringVar(value=settings["repo"])
        self.naming_mode_var = tk.StringVar(value=settings.get("naming_mode", "original"))
        self.custom_name_var = tk.StringVar(value=settings.get("custom_name", ""))
        self.status_var = tk.StringVar(value="Ready.")

        pad = {"padx": 8, "pady": 6}
        frame = ttk.Frame(parent, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frame, text=f"Engine: vtt.py v{vtt.__version__}", anchor="e", foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8
        )
        row += 1
        ttk.Label(frame, text="Video link (URL):").grid(row=row, column=0, sticky="w", **pad)
        self.link_entry = ttk.Entry(frame, textvariable=self.link_var)
        self.link_entry.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        # Same-session safety net: a plain Tk Entry doesn't select existing text on focus the
        # way a browser address bar does, so a fresh paste over a leftover link would land at
        # the cursor and get appended instead of replacing it. Select-all on focus fixes that.
        self.link_entry.bind("<FocusIn>", self._select_link_text)
        row += 1

        ttk.Label(frame, text="Folder A (save downloaded video into):").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.repo_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_repo).grid(row=row, column=2, **pad)
        row += 1

        naming = ttk.Frame(frame)
        naming.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        row += 1

        ttk.Radiobutton(
            naming, text="Use original video title", value="original",
            variable=self.naming_mode_var, command=self._update_naming_state
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(
            naming, text="Custom name:", value="custom",
            variable=self.naming_mode_var, command=self._update_naming_state
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.custom_name_entry = ttk.Entry(naming, textvariable=self.custom_name_var)
        self.custom_name_entry.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        naming.columnconfigure(1, weight=1)

        self.start_button = ttk.Button(frame, text="Start Download", command=self.on_start)
        self.start_button.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        row += 1

        ttk.Label(frame, text="Progress log:").grid(row=row, column=0, sticky="w", **pad)
        row += 1

        self.log_panel = LogPanel(frame, root)
        self.log_panel.widget.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        frame.rowconfigure(row, weight=1)
        row += 1

        ttk.Label(frame, textvariable=self.status_var, anchor="w").grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        self._update_naming_state()

    def _select_link_text(self, event):
        event.widget.select_range(0, "end")
        event.widget.icursor("end")

    def _browse_repo(self):
        path = filedialog.askdirectory(
            title="Choose folder A (downloaded videos)",
            initialdir=_existing_dir_or_none(self.repo_var.get()),
        )
        if path:
            self.repo_var.set(path)

    def _update_naming_state(self):
        self.custom_name_entry.configure(state="normal" if self.naming_mode_var.get() == "custom" else "disabled")

    def on_start(self):
        link = self.link_var.get().strip()
        repo_dir = self.repo_var.get().strip() or "repository_A"
        naming_mode = self.naming_mode_var.get()
        custom_name = self.custom_name_var.get().strip()

        if not link:
            messagebox.showwarning("Missing link", "Paste a video URL first.")
            return
        if not vtt.is_url(link):
            messagebox.showwarning("Not a link", "That doesn't look like a URL (must start with http:// or https://).")
            return
        if naming_mode == "custom" and not custom_name:
            messagebox.showwarning("Missing name", "Enter a custom file name, or switch to 'Use original video title'.")
            return

        download_name = custom_name if naming_mode == "custom" else None

        save_section("download", {
            "link": link, "repo": repo_dir,
            "naming_mode": naming_mode, "custom_name": custom_name,
        })

        self.start_button.configure(state="disabled")
        self.status_var.set("Downloading...")
        self.log_panel.clear()

        self.worker_thread = threading.Thread(target=self._worker, args=(link, repo_dir, download_name), daemon=True)
        self.worker_thread.start()
        self.root.after(100, lambda: self.log_panel.poll(self._on_idle, lambda: self.worker_thread and self.worker_thread.is_alive()))

    def _worker(self, link, repo_dir, download_name):
        try:
            vtt.download_video(link, repo_dir, log=self.log_panel.log, custom_name=download_name)
            self.log_panel.log("\nDone. Video saved in folder A.")
        except Exception as e:
            self.log_panel.log(f"\nError: {e}")

    def _on_idle(self):
        self.start_button.configure(state="normal")
        self.status_var.set("Ready.")
        self.worker_thread = None


class TranscribeTab:
    """Step 2: video(s) in any folder (typically folder A) -> TXT/VTT in folder B. Independent of Download."""

    def __init__(self, parent, root, settings):
        self.root = root
        self.worker_thread = None

        self.folder_var = tk.StringVar(value=settings["folder"])
        self.file_var = tk.StringVar(value=settings["selected_file"])
        self.output_name_var = tk.StringVar(value="")
        self.outdir_var = tk.StringVar(value=settings["outdir"])
        self.lang_var = tk.StringVar(value=settings["lang"])
        self.translate_var = tk.BooleanVar(value=settings["translate"])
        self.model_var = tk.StringVar(value=settings["model"] if settings["model"] in MODEL_SIZES else "base")
        self.format_var = tk.StringVar(value=settings["format"] if settings["format"] in FORMATS else "both")
        saved_speakers = settings.get("speakers", "off")
        self.speakers_var = tk.StringVar(value=SPEAKER_LABELS.get(saved_speakers, SPEAKER_LABELS["off"]))
        self.hf_token_var = tk.StringVar(value=settings.get("hf_token", ""))
        self.status_var = tk.StringVar(value="Ready.")

        pad = {"padx": 8, "pady": 6}
        frame = ttk.Frame(parent, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frame, text=f"Engine: vtt.py v{vtt.__version__}", anchor="e", foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8
        )
        row += 1
        ttk.Label(frame, text="Source folder (e.g. folder A):").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.folder_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_folder).grid(row=row, column=2, **pad)
        row += 1

        ttk.Label(frame, text="File to transcribe:").grid(row=row, column=0, sticky="w", **pad)
        self.file_combo = ttk.Combobox(frame, textvariable=self.file_var, state="readonly")
        self.file_combo.grid(row=row, column=1, sticky="ew", **pad)
        self.file_combo.bind("<<ComboboxSelected>>", lambda e: self._update_output_name_default())
        ttk.Button(frame, text="Refresh list", command=self.refresh_file_list).grid(row=row, column=2, **pad)
        row += 1

        ttk.Label(frame, text="Output file name:").grid(row=row, column=0, sticky="w", **pad)
        self.output_name_entry = ttk.Entry(frame, textvariable=self.output_name_var)
        self.output_name_entry.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frame, text="(no extension; default = source name)").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Folder B (save transcript into):").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.outdir_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_outdir).grid(row=row, column=2, **pad)
        row += 1

        options = ttk.Frame(frame)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        row += 1

        ttk.Label(options, text="Language:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttk.Entry(options, textvariable=self.lang_var, width=8).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(options, text="(e.g. es, en, fr, or 'auto')").grid(row=0, column=2, sticky="w")

        ttk.Label(options, text="Model:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        ttk.Combobox(options, textvariable=self.model_var, values=MODEL_SIZES, state="readonly", width=10).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        ttk.Label(options, text="Output format:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Combobox(options, textvariable=self.format_var, values=FORMATS, state="readonly", width=8).grid(
            row=1, column=3, sticky="w", pady=(6, 0)
        )

        ttk.Checkbutton(options, text="Translate to English", variable=self.translate_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

        # Optional main-narrator filter (speaker_filter.py / pyannote.audio).
        ttk.Label(options, text="Speakers:").grid(row=3, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        speakers_combo = ttk.Combobox(options, textvariable=self.speakers_var,
                                      values=list(SPEAKER_LABELS.values()), state="readonly", width=24)
        speakers_combo.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        speakers_combo.bind("<<ComboboxSelected>>", lambda e: self._update_token_state())

        ttk.Label(options, text="HF token:").grid(row=4, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        self.hf_token_entry = ttk.Entry(options, textvariable=self.hf_token_var, show="*", width=28)
        self.hf_token_entry.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(options, text="(Hugging Face; empty = use HF_TOKEN / saved login)", foreground="gray").grid(
            row=4, column=3, sticky="w", pady=(6, 0)
        )
        self._update_token_state()

        self.start_button = ttk.Button(frame, text="Start Transcribe", command=self.on_start)
        self.start_button.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        row += 1

        # Transcription can run for a long time with no other feedback, so this bar/label give a
        # running "is it actually doing something" signal -- driven by vtt.py hooking Whisper's
        # own internal progress bar (see vtt._whisper_progress) and relayed here via a
        # thread-safe Queue, same pattern as self.log_panel below.
        self.progress_queue = queue.Queue()
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_label_var = tk.StringVar(value="")
        ttk.Progressbar(
            frame, orient="horizontal", mode="determinate", maximum=100, variable=self.progress_var
        ).grid(row=row, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(frame, textvariable=self.progress_label_var, anchor="w").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Progress log:").grid(row=row, column=0, sticky="w", **pad)
        row += 1

        self.log_panel = LogPanel(frame, root)
        self.log_panel.widget.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        frame.rowconfigure(row, weight=1)
        row += 1

        ttk.Label(frame, textvariable=self.status_var, anchor="w").grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        self.refresh_file_list(keep_selection=True)

    def _speaker_mode(self):
        """Maps the Speakers dropdown's display text back to vtt.py's mode key."""
        label = self.speakers_var.get()
        for mode, text in SPEAKER_LABELS.items():
            if text == label:
                return mode
        return "off"

    def _update_token_state(self):
        """The token field only matters when a speaker filter is on."""
        self.hf_token_entry.configure(state="disabled" if self._speaker_mode() == "off" else "normal")

    def _browse_folder(self):
        path = filedialog.askdirectory(
            title="Choose the folder containing video/audio files",
            initialdir=_existing_dir_or_none(self.folder_var.get()),
        )
        if path:
            self.folder_var.set(path)
            self.refresh_file_list()

    def _browse_outdir(self):
        path = filedialog.askdirectory(
            title="Choose folder B (transcript output)",
            initialdir=_existing_dir_or_none(self.outdir_var.get()),
        )
        if path:
            self.outdir_var.set(path)

    def refresh_file_list(self, keep_selection=False):
        folder = self.folder_var.get().strip()
        previous = self.file_var.get()
        files = []
        if folder and os.path.isdir(folder):
            files = vtt.list_media_files(folder)
        names = [os.path.basename(f) for f in files]
        values = [ALL_FILES_LABEL] + names
        self.file_combo.configure(values=values)
        if keep_selection and previous in values:
            self.file_var.set(previous)
        else:
            self.file_var.set(ALL_FILES_LABEL)
        self._update_output_name_default()

    def _update_output_name_default(self):
        """Resets the output name field to match the currently selected source file.
        Disabled entirely in 'all files' mode, since one name can't apply to a whole batch."""
        selection = self.file_var.get()
        if selection == ALL_FILES_LABEL:
            self.output_name_var.set("")
            self.output_name_entry.configure(state="disabled")
        else:
            self.output_name_var.set(os.path.splitext(selection)[0])
            self.output_name_entry.configure(state="normal")

    def on_start(self):
        folder = self.folder_var.get().strip()
        outdir = self.outdir_var.get().strip() or "folder_B"
        lang = self.lang_var.get().strip() or "auto"
        translate = self.translate_var.get()
        model_name = self.model_var.get()
        speaker_mode = self._speaker_mode()
        hf_token = self.hf_token_var.get().strip()
        fmt = self.format_var.get()
        formats = {"txt", "vtt"} if fmt == "both" else {fmt}
        selection = self.file_var.get()

        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing folder", "Choose a valid source folder first.")
            return

        if selection == ALL_FILES_LABEL:
            input_path = folder
            output_name = None
        else:
            input_path = os.path.join(folder, selection)
            if not os.path.isfile(input_path):
                messagebox.showwarning("File not found", "That file is no longer there -- click 'Refresh list'.")
                return
            output_name = self.output_name_var.get().strip() or os.path.splitext(selection)[0]

        save_section("transcribe", {
            "folder": folder,
            "selected_file": selection,
            "outdir": outdir,
            "lang": lang,
            "translate": translate,
            "model": model_name,
            "format": fmt,
            "speakers": speaker_mode,
            "hf_token": hf_token,
        })

        self.start_button.configure(state="disabled")
        self.status_var.set("Transcribing...")
        self.log_panel.clear()
        self.progress_var.set(0.0)
        self.progress_label_var.set("0%")

        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(input_path, folder, outdir, model_name, lang, translate, formats, output_name,
                  speaker_mode, hf_token),
            daemon=True,
        )
        self.worker_thread.start()
        self.root.after(100, lambda: self.log_panel.poll(self._on_idle, lambda: self.worker_thread and self.worker_thread.is_alive()))
        self.root.after(100, self._poll_progress)

    def _worker(self, input_path, folder, outdir, model_name, lang, translate, formats, output_name,
                speaker_mode="off", hf_token=""):
        try:
            vtt.run_job(input_path, folder, outdir, model_name, lang, translate, formats,
                        log=self.log_panel.log, output_name=output_name,
                        progress_callback=self._on_progress,
                        speaker_mode=speaker_mode, hf_token=hf_token or None)
        except Exception as e:
            self.log_panel.log(f"\nError: {e}")

    def _on_progress(self, fraction, file_index, total_files):
        """Called from the background worker thread (see vtt.run_job) -- just hands the update to
        the main thread via a Queue, same pattern as LogPanel.log(), since Tkinter widgets can't
        be touched off the main thread."""
        self.progress_queue.put((fraction, file_index, total_files))

    def _poll_progress(self):
        try:
            while True:
                fraction, file_index, total_files = self.progress_queue.get_nowait()
                overall = ((file_index - 1) + fraction) / total_files if total_files else fraction
                percent = max(0.0, min(100.0, overall * 100))
                self.progress_var.set(percent)
                if total_files > 1:
                    self.progress_label_var.set(f"{percent:.0f}%  (file {file_index}/{total_files})")
                else:
                    self.progress_label_var.set(f"{percent:.0f}%")
        except queue.Empty:
            pass

        if self.worker_thread and self.worker_thread.is_alive():
            self.root.after(150, self._poll_progress)

    def _on_idle(self):
        self.start_button.configure(state="normal")
        self.status_var.set("Ready.")
        self.worker_thread = None


class CorrectTab:
    """Step 3: transcript(s) in any folder (typically folder B) -> corrected copies, fixing
    recurring Whisper mis-hearings via the trading-class glossary. Independent of the other tabs."""

    def __init__(self, parent, root, settings):
        self.root = root
        self.worker_thread = None

        self.folder_var = tk.StringVar(value=settings["folder"])
        self.file_var = tk.StringVar(value=settings["selected_file"])
        self.output_name_var = tk.StringVar(value="")
        self.outdir_var = tk.StringVar(value=settings["outdir"])
        self.status_var = tk.StringVar(value="Ready.")

        pad = {"padx": 8, "pady": 6}
        frame = ttk.Frame(parent, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frame, text=f"Engine: transcript_corrector.py v{transcript_corrector.__version__}", anchor="e", foreground="gray").grid(
            row=row, column=0, columnspan=3, sticky="ew", padx=8
        )
        row += 1
        ttk.Label(frame, text="Source folder (e.g. folder B):").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.folder_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_folder).grid(row=row, column=2, **pad)
        row += 1

        ttk.Label(frame, text="File to correct:").grid(row=row, column=0, sticky="w", **pad)
        self.file_combo = ttk.Combobox(frame, textvariable=self.file_var, state="readonly")
        self.file_combo.grid(row=row, column=1, sticky="ew", **pad)
        self.file_combo.bind("<<ComboboxSelected>>", lambda e: self._update_output_name_default())
        ttk.Button(frame, text="Refresh list", command=self.refresh_file_list).grid(row=row, column=2, **pad)
        row += 1

        ttk.Label(frame, text="Output file name:").grid(row=row, column=0, sticky="w", **pad)
        self.output_name_entry = ttk.Entry(frame, textvariable=self.output_name_var)
        self.output_name_entry.grid(row=row, column=1, sticky="ew", **pad)
        ttk.Label(frame, text="(no extension; default = source name + _corrected)").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frame, text="Save corrected file(s) into:").grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.outdir_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse...", command=self._browse_outdir).grid(row=row, column=2, **pad)
        row += 1

        self.start_button = ttk.Button(frame, text="Start Correct", command=self.on_start)
        self.start_button.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        row += 1

        ttk.Label(frame, text="Progress log:").grid(row=row, column=0, sticky="w", **pad)
        row += 1

        self.log_panel = LogPanel(frame, root)
        self.log_panel.widget.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        frame.rowconfigure(row, weight=1)
        row += 1

        ttk.Label(frame, textvariable=self.status_var, anchor="w").grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        self.refresh_file_list(keep_selection=True)

    def _browse_folder(self):
        path = filedialog.askdirectory(
            title="Choose the folder containing transcript files",
            initialdir=_existing_dir_or_none(self.folder_var.get()),
        )
        if path:
            self.folder_var.set(path)
            self.refresh_file_list()

    def _browse_outdir(self):
        path = filedialog.askdirectory(
            title="Choose the folder for corrected output",
            initialdir=_existing_dir_or_none(self.outdir_var.get()),
        )
        if path:
            self.outdir_var.set(path)

    def refresh_file_list(self, keep_selection=False):
        folder = self.folder_var.get().strip()
        previous = self.file_var.get()
        files = []
        if folder and os.path.isdir(folder):
            files = transcript_corrector.list_correctable_files(folder)
        names = [os.path.basename(f) for f in files]
        values = [ALL_FILES_LABEL] + names
        self.file_combo.configure(values=values)
        if keep_selection and previous in values:
            self.file_var.set(previous)
        else:
            self.file_var.set(ALL_FILES_LABEL)
        self._update_output_name_default()

    def _update_output_name_default(self):
        """Resets the output name field to match the currently selected source file.
        Disabled entirely in 'all files' mode, since one name can't apply to a whole batch."""
        selection = self.file_var.get()
        if selection == ALL_FILES_LABEL:
            self.output_name_var.set("")
            self.output_name_entry.configure(state="disabled")
        else:
            base = os.path.splitext(selection)[0]
            self.output_name_var.set(f"{base}_corrected")
            self.output_name_entry.configure(state="normal")

    def on_start(self):
        folder = self.folder_var.get().strip()
        outdir = self.outdir_var.get().strip() or "folder_B_corrected"
        selection = self.file_var.get()

        if not folder or not os.path.isdir(folder):
            messagebox.showwarning("Missing folder", "Choose a valid source folder first.")
            return

        if selection == ALL_FILES_LABEL:
            input_path = folder
            output_name = None
        else:
            input_path = os.path.join(folder, selection)
            if not os.path.isfile(input_path):
                messagebox.showwarning("File not found", "That file is no longer there -- click 'Refresh list'.")
                return
            output_name = self.output_name_var.get().strip() or f"{os.path.splitext(selection)[0]}_corrected"

        save_section("correct", {
            "folder": folder,
            "selected_file": selection,
            "outdir": outdir,
        })

        self.start_button.configure(state="disabled")
        self.status_var.set("Correcting...")
        self.log_panel.clear()

        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(input_path, outdir, output_name),
            daemon=True,
        )
        self.worker_thread.start()
        self.root.after(100, lambda: self.log_panel.poll(self._on_idle, lambda: self.worker_thread and self.worker_thread.is_alive()))

    def _worker(self, input_path, outdir, output_name):
        try:
            transcript_corrector.run_correction_job(input_path, outdir, log=self.log_panel.log, output_name=output_name)
        except Exception as e:
            self.log_panel.log(f"\nError: {e}")

    def _on_idle(self):
        self.start_button.configure(state="normal")
        self.status_var.set("Ready.")
        self.worker_thread = None


def main():
    root = tk.Tk()
    root.title(f"Video to Text (Whisper) -- GUI v{__version__}")
    root.geometry("760x660")
    root.minsize(680, 580)

    settings = load_settings()

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    download_frame = ttk.Frame(notebook)
    transcribe_frame = ttk.Frame(notebook)
    correct_frame = ttk.Frame(notebook)
    notebook.add(download_frame, text="1. Download (Link -> Folder A)")
    notebook.add(transcribe_frame, text="2. Transcribe (Folder -> Folder B)")
    notebook.add(correct_frame, text="3. Correct (Fix Transcription Errors)")

    DownloadTab(download_frame, root, settings["download"])
    TranscribeTab(transcribe_frame, root, settings["transcribe"])
    CorrectTab(correct_frame, root, settings["correct"])

    # Outside the notebook so it stays visible no matter which tab is active. Per-tab engine
    # versions (vtt.py / transcript_corrector.py) are shown at the top of each tab instead,
    # since this bar can't represent three independently-versioned files with one number.
    ttk.Label(root, text=f"GUI v{__version__}", anchor="e", foreground="gray").pack(
        fill="x", padx=10, pady=(0, 4)
    )

    root.mainloop()


if __name__ == "__main__":
    main()
