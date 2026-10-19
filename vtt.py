"""
Downloads a video (via yt-dlp) and/or transcribes local MP4/MP3 files with Whisper,
writing TXT and/or WebVTT output. See readme.txt for usage; used as steps 1-2 of the
app (Download, Transcribe), independent of transcript_corrector.py's Correct step.

VERSIONING: this file carries its own __version__, independent of gui.py's and
transcript_corrector.py's -- each file is versioned on its own schedule now, bumped
only when that specific file changes. See version.py for the current version of every
component in one place and the combined changelog. Shown via `python vtt.py --version`
on the CLI.
"""
import argparse
import contextlib
import gc
import glob
import os
import re
import sys
import tqdm
import torch
import whisper

import speaker_filter

__version__ = "1.5.0"

_TRUST_STORE_TRIED = False


def enable_system_trust_store(log=print):
    """On networks that inspect HTTPS traffic (corporate proxies), the proxy re-signs sites with
    a company root certificate that lives in the macOS Keychain but NOT in Python's bundled
    certifi list -- so yt-dlp (and Whisper's model download) fail with 'CERTIFICATE_VERIFY_FAILED:
    self-signed certificate in certificate chain'. The `truststore` package makes Python use the
    operating system's certificate store instead, which already trusts that company root.
    Runs once per process; silently does nothing if truststore isn't installed
    (fix: pip install truststore)."""
    global _TRUST_STORE_TRIED
    if _TRUST_STORE_TRIED:
        return
    _TRUST_STORE_TRIED = True
    try:
        import truststore
        truststore.inject_into_ssl()
        log("--> Using the system certificate store for HTTPS (truststore).")
    except ImportError:
        pass
    except Exception as e:
        log(f"--> Note: could not enable the system certificate store ({e}).")


def is_url(text):
    """Returns True if the given string looks like an http(s) URL."""
    return re.match(r'^https?://', text, re.IGNORECASE) is not None


def sanitize_filename(name):
    """Strips characters that aren't safe in a file name on common filesystems."""
    name = (name or "").strip()
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip(" .")
    return name or "output"


class _ProgressTqdm(tqdm.tqdm):
    """Whisper's own progress bar, subclassed so every update also reports a 0..1
    fraction to an external callback. Used (via _whisper_progress below) to give the
    GUI real transcription progress instead of just a 'please wait' message -- Whisper
    has no callback hook of its own, so this hooks the tqdm bar it already drives
    internally. Falls back to plain tqdm behavior (no callback) when nothing is
    listening."""

    _progress_callback = None  # class-level: set/cleared around each transcribe() call

    def update(self, n=1):
        super().update(n)
        if _ProgressTqdm._progress_callback is not None and self.total:
            try:
                _ProgressTqdm._progress_callback(min(1.0, self.n / self.total))
            except Exception:
                pass  # progress reporting is a nice-to-have, never worth failing the job over


@contextlib.contextmanager
def _whisper_progress(callback):
    """Temporarily swaps in _ProgressTqdm so \`callback(fraction)\` fires as
    model.transcribe() advances through the audio. No-op if callback is None (CLI usage
    with no one listening), and restores tqdm.tqdm afterwards (including on error) so
    nothing else in the process is affected."""
    if callback is None:
        yield
        return
    original_tqdm_class = tqdm.tqdm
    _ProgressTqdm._progress_callback = callback
    tqdm.tqdm = _ProgressTqdm
    try:
        yield
    finally:
        tqdm.tqdm = original_tqdm_class
        _ProgressTqdm._progress_callback = None


def download_video(url, repo_dir, log=print, custom_name=None):
    """Downloads a video from a URL into repository A using yt-dlp. Returns the local file path.
    If custom_name is given, the file is saved under that name instead of the video's original
    title. Raises RuntimeError on failure (missing dependency, or the expected output file not found)."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp is not installed. Install it with: pip install yt-dlp")

    os.makedirs(repo_dir, exist_ok=True)

    if custom_name:
        name_template = f"{sanitize_filename(custom_name)}.%(ext)s"
    else:
        name_template = "%(title)s.%(ext)s"

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(repo_dir, name_template),
        "merge_output_format": "mp4",
        "restrictfilenames": True,
        "quiet": False,
        "no_warnings": True,
    }

    enable_system_trust_store(log)

    # Last-resort escape hatch for networks whose HTTPS-inspection certificate can't be trusted
    # any other way: set VTT_INSECURE_SSL=1 (or use the CLI's --no-check-certificate). This turns
    # certificate verification OFF for the download, so only use it on a network you trust.
    if os.environ.get("VTT_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
        ydl_opts["nocheckcertificate"] = True
        log("--> WARNING: SSL certificate verification is DISABLED for this download.")

    log(f"\n--> Downloading video from: {url}")
    log(f"--> Saving into repository A: {repo_dir}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # After merging, the container is forced to mp4 -- point at the final file if it exists.
            base, _ = os.path.splitext(filepath)
            merged_path = base + ".mp4"
            if os.path.exists(merged_path):
                filepath = merged_path
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            raise RuntimeError(
                "SSL certificate verification failed (your network likely inspects HTTPS traffic "
                "with a company certificate). Fix: run `pip install truststore` in this app's "
                "environment and try again. Last resort: set VTT_INSECURE_SSL=1 (turns certificate "
                "checking off).\n\nOriginal error: " + str(e)
            ) from e
        raise

    if not os.path.exists(filepath):
        raise RuntimeError(f"Expected downloaded file not found: {filepath}")

    log(f"--> Downloaded: {os.path.basename(filepath)}")
    return filepath


def format_vtt_timestamp(seconds):
    """Converts seconds into WebVTT timestamp format (HH:MM:SS.mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def list_media_files(folder):
    """Returns a sorted list of .mp4/.mp3 files directly inside folder (no recursion, no error if empty)."""
    extensions = ["*.mp4", "*.MP4", "*.mp3", "*.MP3"]
    files = []
    for ext in extensions:
        files.extend(glob.glob(os.path.join(folder, ext)))
    return sorted(files)


def process_file(media_path, model, options, outdir, formats, log=print, output_name=None,
                  progress_callback=None, speaker_mode="off", diarization_pipeline=None):
    """Transcribes a single MP4/MP3 file and writes the requested output formats into folder B.
    output_name, if given, overrides the output base file name (default: same as the source file).
    progress_callback, if given, is called with a 0..1 fraction as transcription of THIS file
    advances (see _whisper_progress).
    speaker_mode: "off" (keep everyone, default), "main" (keep only the main narrator) or "label"
    (keep everyone, mark other speakers) -- see speaker_filter.py. Needs diarization_pipeline
    (from speaker_filter.load_pipeline) when not "off"."""
    base_name = sanitize_filename(output_name) if output_name else os.path.splitext(os.path.basename(media_path))[0]

    log(f"\nProcessing: {os.path.basename(media_path)}")
    log("--> Running AI Transcription Engine... (Please wait)")

    use_speakers = speaker_mode != "off" and diarization_pipeline is not None

    # With speaker filtering on, transcription counts as the first 80% of this file's progress
    # and diarization as the remaining 20%.
    transcribe_progress = progress_callback
    if progress_callback and use_speakers:
        transcribe_progress = lambda f: progress_callback(0.8 * f)

    with _whisper_progress(transcribe_progress):
        result = model.transcribe(media_path, **options)

    segments = result["segments"]
    full_text = result["text"].strip()
    if use_speakers:
        diar_progress = (lambda f: progress_callback(0.8 + 0.2 * f)) if progress_callback else None
        turns = speaker_filter.diarize(diarization_pipeline, media_path, log=log, progress_callback=diar_progress)
        segments = speaker_filter.filter_segments(segments, turns, speaker_mode, log=log)
        full_text = speaker_filter.build_text(segments, speaker_mode)

    if "vtt" in formats:
        vtt_path = os.path.join(outdir, f"{base_name}.vtt")
        with open(vtt_path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")
            for i, segment in enumerate(segments, start=1):
                start = format_vtt_timestamp(segment["start"])
                end = format_vtt_timestamp(segment["end"])
                text = segment["text"].strip()
                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")
        log(f"--> Success! Saved VTT: {os.path.basename(vtt_path)}")

    if "txt" in formats:
        txt_path = os.path.join(outdir, f"{base_name}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(full_text + "\n")
        log(f"--> Success! Saved TXT: {os.path.basename(txt_path)}")


def resolve_media_files(input_path, repo_dir, log=print, download_name=None):
    """Figures out which media file(s) to transcribe: downloads a URL into repo_dir,
    or lists a local file/directory. Raises FileNotFoundError on an invalid input."""
    if is_url(input_path):
        return [download_video(input_path, repo_dir, log=log, custom_name=download_name)]

    if os.path.isdir(input_path):
        media_files = list_media_files(input_path)
        if not media_files:
            raise FileNotFoundError(f"No .mp4 or .mp3 files found in the directory '{input_path}'")
        log(f"Batch mode activated. Found {len(media_files)} files to process.")
        return media_files

    if os.path.isfile(input_path):
        return [input_path]

    raise FileNotFoundError(f"'{input_path}' is not a valid URL, file, or directory.")


def release_gpu_memory(log=None):
    """Best-effort cleanup between/after transcriptions. On Apple Silicon (MPS), PyTorch's
    caching allocator keeps freed GPU buffers around instead of returning them to the OS --
    since MPS shares system RAM (unified memory), that shows up as high process memory in
    Activity Monitor. This releases what it can back to the system."""
    gc.collect()
    if torch.backends.mps.is_available():
        try:
            torch.mps.empty_cache()
        except Exception:
            pass
    if log:
        log("--> Released GPU memory cache.")


def prepare_model(model_name, lang, translate, log=print):
    """Loads the Whisper model (using Apple Silicon GPU when available) and builds the transcribe options."""
    enable_system_trust_store(log)  # first-time model download also goes over HTTPS

    if torch.backends.mps.is_available():
        device = "mps"
        log("--> Hardware Acceleration Enabled: Using Apple Silicon M3 GPU (MPS)")
    else:
        device = "cpu"
        log("--> Warning: MPS not active. Falling back to CPU (Processing will be slower)")

    if model_name.startswith("large"):
        log("--> Note: the 'large' model is the heaviest -- on Apple Silicon (MPS) it can use "
            "well over its ~3GB of weights in RAM (often 15-25GB+) because MPS falls back to "
            "32-bit math for some operations and caches GPU memory rather than releasing it. "
            "If memory usage is a problem, 'medium' uses substantially less RAM for a modest "
            "accuracy tradeoff.")

    log(f"Loading Whisper model '{model_name}' on {device.upper()}...")
    model = whisper.load_model(model_name, device=device)

    # verbose=False (rather than Whisper's default of None) turns ON its internal progress
    # bar instead of leaving it disabled -- _whisper_progress/_ProgressTqdm above hook into
    # exactly that bar to report real progress. It does NOT print per-segment text (that's
    # verbose=True); it only adds one harmless "Detected language: ..." print when lang=auto.
    options = {"verbose": False}
    if lang.lower() != "auto":
        options["language"] = lang
    if translate:
        options["task"] = "translate"

    return model, options


def run_job(input_path, repo_dir, outdir, model_name, lang, translate, formats, log=print,
            download_name=None, output_name=None, progress_callback=None, speaker_mode="off", hf_token=None):
    """High-level orchestration shared by the CLI and the GUI: resolve input, load the model,
    transcribe every file, and write output into folder B.

    download_name: custom file name for a downloaded video (only applies when input_path is a URL).
    output_name: custom base file name for the transcript output. Only applies when exactly one
    file is being processed -- if more than one file matched, it's ignored (each keeps its own
    name) so multiple files don't overwrite each other.
    progress_callback: called as (fraction, file_index, total_files) -- fraction is 0..1 progress
    through the CURRENT file (file_index is 1-based); the caller combines the three into an
    overall percentage. Called from whatever thread run_job() itself runs on (the GUI runs it on
    a background thread and hops back to the main thread via a Queue, same as \`log\`).
    speaker_mode / hf_token: optional main-narrator filtering, see process_file() and
    speaker_filter.py. The diarization model is loaded once per job, before any transcription,
    so a setup problem (missing package/token) fails fast instead of after a long transcribe."""
    media_files = resolve_media_files(input_path, repo_dir, log=log, download_name=download_name)
    os.makedirs(outdir, exist_ok=True)

    diarization_pipeline = None
    if speaker_mode not in ("off", None, ""):
        if speaker_mode not in speaker_filter.MODES:
            raise ValueError(f"Unknown speaker mode '{speaker_mode}' (use one of: {', '.join(speaker_filter.MODES)})")
        diarization_pipeline = speaker_filter.load_pipeline(hf_token, log=log)
    else:
        speaker_mode = "off"

    model, options = prepare_model(model_name, lang, translate, log=log)

    effective_output_name = None
    if output_name:
        if len(media_files) == 1:
            effective_output_name = output_name
        else:
            log(f"Note: a custom output name was given but {len(media_files)} files matched -- "
                f"ignoring it and keeping each file's own name.")

    total_files = len(media_files)
    for index, media_path in enumerate(media_files, start=1):
        log(f"\n[File {index}/{len(media_files)}]")

        file_progress_callback = None
        if progress_callback:
            progress_callback(0.0, index, total_files)
            file_progress_callback = lambda fraction, _i=index, _t=total_files: progress_callback(fraction, _i, _t)

        process_file(media_path, model, options, outdir, formats, log=log, output_name=effective_output_name,
                     progress_callback=file_progress_callback, speaker_mode=speaker_mode,
                     diarization_pipeline=diarization_pipeline)
        if len(media_files) > 1:
            # In batch mode, release cached GPU memory between files so usage doesn't climb
            # file-over-file -- otherwise peak memory would roughly track total files processed.
            release_gpu_memory(log=log)

    if progress_callback:
        progress_callback(1.0, total_files, total_files)

    # Drop the model and release its memory now that the job is done, so the app's memory
    # footprint doesn't stay high (or keep climbing across separate Transcribe runs) once idle.
    del model
    diarization_pipeline = None
    release_gpu_memory(log=log)

    log(f"\nBatch processing completed successfully! Transcripts saved in: {outdir}")


def main():
    parser = argparse.ArgumentParser(
        description="Download a video from a link (or use local MP4/MP3 files) and transcribe it with "
                     "Whisper on Apple Silicon, saving TXT and/or WebVTT output."
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "input",
        help="A video URL to download, OR a path to a local MP4/MP3 file, OR a directory containing them."
    )
    parser.add_argument(
        "-A", "--repo", default="repository_A",
        help="Repository A: folder where downloaded videos are saved (only used for URL input). Default: ./repository_A"
    )
    parser.add_argument(
        "-B", "--outdir", default="folder_B",
        help="Folder B: folder where transcript output (TXT/VTT) is saved. Default: ./folder_B"
    )
    parser.add_argument("-l", "--lang", default="es", help="Force audio language code (default: es for Spanish). Use 'auto' to let Whisper guess.")
    parser.add_argument("-t", "--translate", action="store_true", help="Translate audio directly into English text")
    parser.add_argument("-m", "--model", default="base", help="Whisper model size: tiny, base, small, medium, large (default: base)")
    parser.add_argument(
        "-f", "--format", default="both", choices=["txt", "vtt", "both"],
        help="Output format(s) to write into folder B (default: both)"
    )
    parser.add_argument(
        "-n", "--download-name", default=None,
        help="Custom file name for the downloaded video (only used when input is a URL). Default: the video's original title."
    )
    parser.add_argument(
        "-o", "--output-name", default=None,
        help="Custom base file name for the transcript output (only used when exactly one file is processed). Default: same name as the source file."
    )

    parser.add_argument(
        "-s", "--speakers", default="off", choices=list(speaker_filter.MODES),
        help="Speaker filter: 'off' = keep everyone (default); 'main' = keep only the main narrator "
             "(the person who talks the most); 'label' = keep everyone but mark other speakers as "
             f"'{speaker_filter.OTHER_SPEAKER_LABEL}'. Needs pyannote.audio + a Hugging Face token (see readme.txt)."
    )
    parser.add_argument(
        "--hf-token", default=None,
        help="Hugging Face access token for --speakers main/label (default: HF_TOKEN env var or a saved `hf auth login`)."
    )

    parser.add_argument(
        "--no-check-certificate", action="store_true",
        help="Turn OFF SSL certificate checking for the download (last resort behind a HTTPS-inspecting proxy; "
             "prefer `pip install truststore`)."
    )

    args = parser.parse_args()
    if args.no_check_certificate:
        os.environ["VTT_INSECURE_SSL"] = "1"
    formats = {"txt", "vtt"} if args.format == "both" else {args.format}

    print(f"Video-to-Text v{__version__}")

    try:
        run_job(args.input, args.repo, args.outdir, args.model, args.lang, args.translate, formats,
                download_name=args.download_name, output_name=args.output_name,
                speaker_mode=args.speakers, hf_token=args.hf_token)
    except Exception as e:
        # Catches our own FileNotFoundError/RuntimeError as well as anything yt-dlp or
        # Whisper might raise (e.g. yt_dlp.utils.DownloadError), so failures print cleanly
        # instead of a raw traceback.
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
