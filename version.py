"""
VERSION REGISTRY -- not imported by any other file in this app.

Each file (gui.py, vtt.py, transcript_corrector.py) now carries its own `__version__`
constant, bumped independently on its own schedule -- a change to transcript_corrector.py
no longer forces gui.py or vtt.py to bump too, and vice versa. This file exists purely as
a single place to see every component's current version at a glance, plus a combined
changelog; it is documentation only, not a source of truth any code depends on. (This
replaces the previous scheme, where all three files imported one shared __version__ from
here -- kept as a single source of truth for the whole app, at the cost of every file
sharing one number regardless of whether that specific file actually changed.)

Each file's own version is visible in the running app: gui.py's title bar and bottom
label show its own version, each tab additionally shows the engine version it's running
(vtt.py for Download/Transcribe, transcript_corrector.py for Correct); `python vtt.py
--version` and `python transcript_corrector.py --version` show their own file's version
on the CLI. correctionglossary.md already carried its own independent version number
before this file was repurposed -- it needed no change.

------------------------------------------------------------------------
CURRENT VERSIONS (as of 2026-10-27)
------------------------------------------------------------------------
gui.py                   1.5.0
vtt.py                   1.6.1  (repetition-loop fix, 2026-10-27)
speaker_filter.py        1.1.0  (diarization on MPS, 2026-10-27)
transcript_corrector.py  1.4.5
correctionglossary.md    1.5    (versioned separately -- see that file's own header)

------------------------------------------------------------------------
CHANGELOG
------------------------------------------------------------------------
2026-10-27 -- vtt.py 1.6.0 -> 1.6.1: stopped Whisper repetition loops.
  - Symptom: the first MLX run on the 4h class "20261021 - M4" looped "y el outlook" 308
    times (47:18-57:36, ~10 min of lecture lost) and wrote 113 empty cues. The older
    PyTorch output of the same class didn't loop there, but lost ~40 s at 3:44:20-3:45:00.
  - Cause: prepare_model() passed only verbose/language/task, so condition_on_previous_text
    stayed at Whisper's default (True): each segment is decoded with the previous one's text
    as its prompt, and one bad segment can seed a loop that sustains itself.
  - Fix: options now set condition_on_previous_text=False. Both engines share `options`, so
    this covers MLX and the PyTorch fallback.
  - Confirmed on the Mac (M4 re-run with MLX): 0 loop repeats, 0 empty cues, both problem
    passages transcribed, natural cue timing (33 fixed 2.0 s cues vs 731 before).
  - Side effects: some stretches switch to sentence case / "?...?" punctuation and spell out
    numbers ("treinta y ocho"); 17 "."-only cues in a silent stretch (3:43:53-3:44:16).
    Cosmetic. Possible follow-up: skip cues with no letters/digits when writing the VTT.
  - This also confirms the 1.6.0 MLX engine on the Mac (previously "NOT yet confirmed").

2026-10-27 -- vtt.py 1.5.1 -> 1.6.0: MLX transcription engine.
  - Measured on a 4h class (20261021 - M4, medium model, Main narrator only): transcription
    39m21s, diarization 13m19s -- the speaker-filter fix worked, and Whisper is now ~75% of
    the time. To speed up Whisper itself, vtt.py can now run it through Apple's MLX framework
    (mlx-whisper; same Whisper models converted by mlx-community on Hugging Face, same
    segments/VTT/TXT output, same tqdm progress hook).
  - New engine option: "auto" (default: MLX if mlx-whisper is installed, else PyTorch),
    "mlx", "torch". CLI: -e/--engine. The GUI has no new control -- it uses "auto".
  - Safety nets: if MLX can't start (auto mode), the PyTorch engine is used; if MLX fails
    on a file, that file is retried with PyTorch, which is then used for the rest of the job.
  - Model mapping: tiny/base/small/medium -> mlx-community/whisper-<name>-mlx; "large" ->
    whisper-large-v3-mlx (what openai-whisper's "large" means). First use downloads the
    weights once (~1.5 GB for medium).
  - requirements.txt: + mlx 0.32.4, mlx-metal 0.32.4, mlx-whisper 0.4.3 (cp314 wheels exist).
  - NOT yet confirmed on the Mac (cloud side can't run MLX).

2026-10-27 -- speaker_filter.py 1.0.1 -> 1.1.0, vtt.py 1.5.0 -> 1.5.1:
  - Fix: "Main narrator only" made transcription ~50x slower. Cause: pyannote diarization
    was pinned to CPU, and on 2-3 hour recordings it took far longer than Whisper itself.
    Diarization now runs on the Apple Silicon GPU (MPS), with automatic CPU retry if MPS
    fails. vtt.py sets PYTORCH_ENABLE_MPS_FALLBACK=1 before importing torch, frees Whisper's
    GPU cache before diarizing, and the log now shows how long transcription and
    diarization each took.

2026-10-19 -- gui.py 1.4.3 -> 1.5.0, vtt.py 1.4.2 -> 1.5.0, NEW speaker_filter.py 1.0.0:
  optional "main narrator only" filter, per user request (make the transcriber ignore
  voices other than the main narrator).
  - speaker_filter.py (new): wraps pyannote.audio speaker diarization
    (pyannote/speaker-diarization-community-1, CPU). Audio is decoded with Whisper's own
    ffmpeg loader and passed to pyannote in memory. Each Whisper segment is assigned the
    speaker it overlaps most (nearest turn if none); main narrator = most total speaking
    time per file. Modes: "main" drops other speakers' segments, "label" keeps them with
    an "[Otro hablante]" prefix (TXT gets a paragraph break at each speaker change).
    pyannote is imported lazily -- the app works without it when the option is off.
  - vtt.py: process_file()/run_job() gained speaker_mode ("off" default) + hf_token;
    the diarization pipeline is loaded once per job BEFORE transcription so a missing
    package/token fails fast. With the filter on, per-file progress = 80% transcribe +
    20% diarization. New CLI flags: -s/--speakers {off,main,label}, --hf-token.
  - gui.py (Transcribe tab): "Speakers" dropdown (All speakers / Main narrator only /
    Label other speakers) + masked "HF token" field, both saved in settings.json.
  - Verified in the cloud session with stubbed torch/whisper/pyannote (all three modes,
    TXT+VTT output, progress sequence, bad-token error path). NOT yet confirmed with the
    real pyannote model on the user's Mac.
2026-09-30 -- gui.py 1.4.2 -> 1.4.3, vtt.py 1.4.1 -> 1.4.2: added a real progress %
  indicator to the Transcribe tab, per user request ("this process takes long, need to
  know that is actually happening").
  - vtt.py: Whisper's model.transcribe() has no progress callback of its own, but
    internally drives a tqdm progress bar (disabled by default -- verbose=None). Two
    changes: (1) prepare_model() now passes verbose=False so that internal bar is
    actually active instead of a no-op; (2) new _ProgressTqdm (a tqdm.tqdm subclass)
    and _whisper_progress() context manager temporarily swap tqdm.tqdm for that
    subclass around the transcribe() call, so every internal pbar.update() also
    reports a 0..1 fraction to an external callback -- restored via try/finally even
    if transcribe() raises. process_file() and run_job() both gained an optional
    progress_callback parameter (None by default -- CLI behavior via main() is
    unchanged apart from verbose=False also now printing one harmless "Detected
    language: ..." line when --lang auto). In batch mode, run_job() calls it as
    (fraction, file_index, total_files) per file -- 0.0 at file start, then live
    updates, then a final (1.0, total_files, total_files) -- so a caller can compute
    one combined percentage across the whole batch.
  - gui.py (TranscribeTab only): added a ttk.Progressbar + percentage label under the
    Start button. A new thread-safe Queue (self.progress_queue) plus _on_progress()/
    _poll_progress() mirror the existing LogPanel queue/poll pattern exactly, since
    Tkinter widgets can't be touched from the worker thread. Shows "NN%" for a single
    file or "NN%  (file i/N)" in batch mode.
  - Verified with a stubbed-dependency test in the cloud session (real tqdm, fake
    torch/whisper/model, matching the project's usual cloud-testing approach): fraction
    reporting is exact (0.1/0.25/0.5/1.0 for matching update deltas), tqdm.tqdm is
    restored after both normal completion and an exception, callback=None is a true
    no-op, and a simulated 3-file batch produces the right per-file 0.0->1.0 sequence
    and the right final (3, 3, 1.0). NOT yet confirmed against a real audio file /
    the actual Tkinter window on the user's Mac.
2026-09-21 -- vtt.py 1.4.0 -> 1.4.1: fixed Download failing on a network that inspects HTTPS
  traffic (yt-dlp: "[SSL: CERTIFICATE_VERIFY_FAILED] ... self-signed certificate in
  certificate chain").
  - Cause: a corporate proxy re-signs sites with a company root certificate that is in the
    macOS Keychain but not in Python's bundled certifi list, which yt-dlp uses.
  - New enable_system_trust_store(): if the optional `truststore` package is installed
    (pip install truststore), Python is switched to the OS certificate store. Called once
    from download_video() and prepare_model() (Whisper's first-time model download is HTTPS
    too). No-op if truststore isn't installed.
  - Last-resort escape hatch: env var VTT_INSECURE_SSL=1 or CLI --no-check-certificate sets
    yt-dlp's nocheckcertificate (verification OFF).
  - Certificate failures now raise a RuntimeError with the fix spelled out.
  - Verified via py_compile + a stubbed yt_dlp test (hint raised, nocheckcertificate only
    set with the env var). NOT yet tested on the Mac's network.
2026-09-11 -- gui.py 1.4.1 -> 1.4.2: per user request, every "Browse..." folder dialog
  (folder A on the Download tab; source folder + folder B on the Transcribe tab; source
  folder + output folder on the Correct tab -- 5 buttons total) now opens showing the
  folder that's *currently* set in that field, instead of always landing on whatever
  default location Tk otherwise falls back to (effectively always the same starting
  point, root-ish, regardless of what was already configured).
  - Added a small helper, `_existing_dir_or_none(path)`: returns the (stripped) path if
    it's a real, existing directory, else `None`. Each `_browse_*` method now passes
    `initialdir=_existing_dir_or_none(<that field's current value>)` to
    `filedialog.askdirectory(...)`. Falls back to Tk's own default (via `None`) rather
    than erroring when the field is empty or points at a folder that no longer exists
    (e.g. a deleted/renamed folder from a previous session) -- passing a bad `initialdir`
    string directly to Tk can raise, so the None fallback matters, not just cosmetics.
  - Verified via `py_compile` plus importing the module with `tkinter`/`torch`/`whisper`
    all stubbed (none available in the cloud session): confirmed all 5 `askdirectory`
    call sites were updated, and unit-tested the helper directly (existing dir returns
    itself, missing dir / empty string / `None` all return `None`). Not yet re-tested by
    actually clicking a Browse button in the running GUI on the user's Mac (no way to
    exercise a real Tk dialog from the cloud session).

2026-09-11 -- transcript_corrector.py 1.4.4 -> 1.4.5: closed a latent gap in
  _correct_dado()'s exclusion logic, found (not triggered) while comparing this script's
  output against a user-provided already-corrected file for a brand-new class recording
  ("20260728 M4 CLASE ABIERTA") that had not been seen before in this project. That
  comparison first served as an independent real-world validation of the 1.4.4 fix: this
  script's output on the raw file matched the user's own corrected file paragraph-for-
  paragraph with zero differences, confirming the 1.4.4 dado/dao fix already works
  correctly end-to-end on the user's Mac (resolving the "not yet re-tested end-to-end on
  the user's Mac" caveat repeated in the 1.4.2, 1.4.3, and 1.4.4 entries above).
  - While reviewing that same file's untouched "dado" instances for correctness, found that
    DADO_VERB_EXCLUDE_BEFORE only listed haber's indicative forms (he/has/ha/hemos/habéis/
    han, había.../hube...) immediately before "dado", missing the subjunctive forms
    (hubiera, hubieras, hubiéramos, hubierais, hubieran, hubiese, hubieses, hubiésemos,
    hubieseis, hubiesen) and the bare infinitive ("haber", "haberse"). Phrases like "tiene
    que haberse dado" or "si hubiese dado" would have skipped the immediate-exclusion check
    and fallen through to the 300-char context window, risking a false-positive "Dow"
    conversion if trading vocabulary happened to be nearby -- the same class of bug as the
    original "se han dado cuenta" -> "se han Dow cuenta" incident, just for different haber
    conjugations. No such case actually appeared in the tested file (no ticker was near any
    of the affected instances there), so this was a latent risk rather than an observed
    wrong output -- flagged to the user and fixed at their request rather than left alone.
  - Added all of haber/haberse plus the ten hubiera/hubiese subjunctive forms to
    DADO_VERB_EXCLUDE_BEFORE.
  - Verified via py_compile + a dedicated 8-case test suite (test_haber_forms.py) covering
    each newly-excluded form paired with nearby finance context that would previously have
    tripped the contextual conversion, plus a regression check that real "dado"->Dow and
    "dao"->Dow conversions still fire correctly -- 0 failures. Re-ran the full 18-case
    1.4.4 test suite (0 failures, no regressions) and re-confirmed 0 differences against the
    same uploaded real file used for the validation above (this fix touches only a small
    number of immediate-exclusion phrases that didn't occur in that file, so its output is
    unchanged there, as expected).

2026-09-11 -- transcript_corrector.py 1.4.3 -> 1.4.4: fixed the standalone "dado"/"dao" ->
  Dow gap that correctionglossary.md's Dow row and 2026-09-11 validation entry flag as the
  single most severe, most inconsistent failure mode in the whole glossary (20+ missed real
  Dow references in one file, scattered and inconsistent). The previous entry (1.4.2 ->
  1.4.3) deliberately left this alone rather than guess; this one implements an actual fix:
  - Bare "dao" (as opposed to "dado") is NOT an ordinary Spanish word -- it only ever means
    Dow in this material -- so it's now a safe unconditional blind substitution in
    TICKER_CORRECTIONS (r'\bdao\b': 'Dow'), placed after every multi-word "dao ___" pattern
    so those consume first. Also added a missing r'\bdao jones\b' entry (full "jones") --
    found while testing this, it had fallen through every existing multi-word pattern.
  - "dado" (the real word, ambiguous with the verb "dar") gets a new wider-window function,
    _correct_dado(), mirroring _correct_amad/_correct_ggl's technique: default to leaving it
    alone (a false positive here reproduces the exact "se han dado cuenta" -> "se han Dow
    cuenta" bug already fixed once), converting only when BOTH (a) it doesn't match a small
    set of immediate exclusion signals for ordinary usage -- a haber-auxiliary immediately
    before ("ha dado", "habían dado", ...), a ser-copula immediately before ("es dado", "fue
    dado", ...), a "given X" idiom noun immediately before (momento/caso/punto/instante/
    ejemplo/valor/número/escenario/contexto/nivel -- catches "en un momento dado" etc.), or
    "que"/"cuenta"/"por"/"un(a/os/as)" immediately after ("dado que", "dado cuenta", "dado
    por", "dado un aumento...") -- AND (b) trading/index-comparison vocabulary (Nasdaq,
    Russell, SPY, VIX, alcista, bajista, resistencia, soporte, retroceso/corrección/
    compresión/ruptura language, sube/baja/cae and their conjugations, etc.) appears within
    300 characters on either side.
  - Verified against the same real file used for the v1.5 validation pass
    (`vtt or DOC/20260624 M4.docx`, compared paragraph-by-paragraph against both the raw
    file and the previously-corrected output): went from 372 edits (original pass) to 423
    (after the 1.4.3 glossary sync) to 431 (after this fix) -- 59 additional real
    corrections total from today's work, ~21 of them from this specific fix (daoyons/Dao-
    style "dao" instances, and "dado" cases like "Dado todavía se mantiene bien", "dado
    segundo", "y dado está subiendo"). Manually inspected every one of the ~20 remaining
    un-converted "dado" instances afterward: all but two are correctly-excluded ordinary
    verb uses ("ha dado la entrada", "me he dado cuenta", "hemos dado todo su tiempo", "no
    ha dado señal", etc.) -- confirming the exclusion logic has zero observed false
    positives on this file. The two remaining misses ("problema de dado hoy", "canal dado")
    are genuinely ambiguous without more context and were left alone deliberately rather
    than widening the trigger vocabulary further (e.g. adding "puts"/"calls" as trigger
    words would have caught them, but those terms appear in nearly every paragraph of this
    material and would have made the contextual gate nearly meaningless).
  - Known remaining limitation, NOT fixed here: DOCX correction is paragraph-by-paragraph
    (see the module docstring's long-standing note on this), so _correct_dado()'s 300-char
    window can only ever see within one paragraph/cue for a .docx file -- a short paragraph
    with no ticker name in it (e.g. "que dado siguió subiendo" before the movement-verb
    trigger words were added) has no context to find. TXT/VTT/SRT/MD files don't have this
    limitation since the whole file is corrected as one string. A real fix would mean
    windowing across paragraph boundaries for DOCX, which is a bigger structural change than
    this pass attempted.
  - Verified via py_compile + the stubbed-dependency test suite (18 synthetic cases
    covering every exclusion pattern plus every confirmed-target conversion, 0 failures)
    plus the real-file comparison above. Not yet re-tested by re-running the actual Correct
    tab end-to-end in the GUI on the user's Mac.

2026-09-11 -- transcript_corrector.py 1.4.2 -> 1.4.3: synced new confirmed entries from
  correctionglossary.md's v1.5 (2026-09-11) validation pass:
  - SPY: added "piwa", "espiway", "Espiwai", "espiguaio", "espiguaya" (same mishearing
    family as the existing espy/espigua entries, just more spelling drift).
  - Nasdaq: added "Nasda" (missing the final "q" -- a major gap, 23 uncaught instances in
    one pass) and "nazar" (1x). Deliberately did NOT add "nasta" (9x) -- the glossary flags
    it as "somewhat ambiguous... could plausibly be a fragment of something else"; left out
    pending verification, same treatment as "bebap".
  - Dow Jones: added the fully-fused "daoyons"/"daoyón" spelling (24 uncaught instances in
    one pass) and the multi-word phrase "dado y once" (1x) -- both safe as blind
    substitutions the same way "dado jones" already was (unambiguous multi-word/fused
    forms, unlike bare "dado"/"dao").
  - VBAP: added "IVAP" (3 uncaught instances in one pass, distinct enough from "bbap"/
    "Vibap" that it wasn't already matched).
  - alcista: added "el sista" (was missing even though "al sista" was already covered) and
    a bare catch-all r'\bsista\b' for the residual word-boundary-shift cases the glossary
    called out (e.g. "estructural sista", "la tendencia el sista") -- placed after the more
    specific al/el-sista entries in the dict so those are consumed first.
  - Deliberately NOT synced (per the glossary's own "unconfirmed"/"unresolved" framing,
    same caution the project already applies to "bebap"): "nasta", "rosa" (possible
    Russell), "S5"/"SIFIC" (possible CFI), "actual cista" (possible alcista), "el cd5".
  - Did NOT attempt a broader fix for the standalone "dado"/"dao" -> Dow contextual gap
    that the v1.5 pass documents extensively (20+ missed real Dow references in one file,
    scattered and inconsistent). The glossary itself doesn't propose a concrete rule, only
    flags it as the top-priority open problem -- widening the existing contextual pattern
    without a clearer signal risked reintroducing the "se han dado cuenta" -> "se han Dow
    cuenta" false-positive class of bug this file was already burned by once. Left as a
    known open issue; needs a real fix design, not a guess.

2026-09-11 -- transcript_corrector.py 1.4.1 -> 1.4.2: re-applied the .vtt->.txt output
  rename feature (previously documented in the project's app-state.md as having shipped,
  but found missing from the actual running code during a Mac-vs-notes discrepancy check).
  - Added RENAME_TO_TXT_EXTENSIONS = (".vtt",); run_correction_job now computes a separate
    output_ext ('.txt' for extensions in that tuple, otherwise the source extension)
    instead of always reusing the input file's extension. Content is untouched -- only the
    output file's extension changes. .srt is deliberately excluded, staying as .srt.
  - Added collision handling: a folder with both e.g. clase1.vtt and clase1.txt (typical
    after a Transcribe run with format="both") would otherwise silently collide on the same
    output name once .vtt's output also becomes clase1_corrected.txt. run_correction_job
    now tracks output paths already used in the batch and disambiguates by folding the
    source extension into the name (e.g. clase1_corrected_vtt.txt) on collision, logging a
    note explaining why.
  - Verified via py_compile + a stubbed-dependency script (no torch/whisper/tkinter
    available in the cloud session) covering: .vtt input -> _corrected.txt with WEBVTT
    structure byte-for-byte preserved aside from corrections; .srt input -> _corrected.srt
    unaffected; a same-named .vtt+.txt pair in one folder producing two distinct,
    non-colliding output files. Not yet re-tested end-to-end on the user's Mac.

2026-09-11 -- gui.py 1.4.0 -> 1.4.1: re-applied the Download-tab blank-link-field fix
  (previously documented as shipped, but found missing from the actual running code during
  the same discrepancy check above).
  - The link field now always starts blank on launch (`tk.StringVar(value="")`) instead of
    pre-filling from the last-saved link -- a plain Tk Entry doesn't select existing text on
    focus the way a browser address bar does, so pasting a fresh link over a leftover one
    landed at the cursor and got appended instead of replacing it. Folder and naming-mode
    choices are still remembered as before; only the link resets each run.
  - Added a same-session safety net: a <FocusIn> handler on the link entry now selects any
    text already in the box, so a second paste in the same session replaces rather than
    appends.
  - Verified via py_compile + direct inspection (no tkinter available in the cloud session
    to run the GUI itself). Not yet re-tested end-to-end on the user's Mac.

NOTE ON HOW THIS DISCREPANCY HAPPENED: as of this entry, the actual cause of the gap
between the documented app-state.md history (which described v1.4.2/v1.4.3 as already
shipped) and the code actually running on the user's Mac (which was still at the pre-fix
state) has not been determined -- the fixes may have been reverted, or applied in a session
whose changes never made it to disk, or something else. Flagged to the user; not guessed at
further here.

2026-08-28 -- transcript_corrector.py 1.4.0 -> 1.4.1
  - "dado"/"dao" (Dow) was a blind substitution, producing confirmed wrong output on
    ordinary Spanish ("dado cuenta", "dado que", "ha dado el discurso"). Rewritten as a
    contextual rule (only fires after an article/preposition), matching how "nada"/Nasdaq
    was already handled.
  - "amad" (AMAT vs AMD) disambiguation widened from an adjacent-word-only check to a
    ~300-character window search, since the real confirmed-wrong case had the AMAT
    context several sentences away.
  - New: "ggl"/"gg ll" (GOOGL vs the leveraged GGLL) contextual check, same style as amad.
  - Fixed a silent duplicate-dict-key bug: "bebap" was mapped to both VBAP and VWAP under
    the same regex key, so only the second (VWAP) ever actually applied. Pulled out
    entirely pending audio verification rather than guessed.
  - Synced in entries that were already confirmed in correctionglossary.md but missing
    from the script: "Spi Guay"->SPY, "cols"/"coals"->calls, "Dow Jon"->Dow Jones,
    "COD"->SOXL, the "déjenlo quieto" phrase, "estopco"->stop.

2026-08-28 -- gui.py, vtt.py, transcript_corrector.py: switched from one shared
  __version__ (imported from this file) to each file owning its own __version__.
  gui.py and vtt.py's version numbers carried forward unchanged (1.4.0, their value
  under the old shared scheme) since neither file's code changed in this pass.

(Earlier history, back to when this was still a single shared version number, wasn't
separately preserved per file -- 1.4.0 was the last shared version before the split.)
"""
