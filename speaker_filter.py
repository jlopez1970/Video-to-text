"""
Optional speaker-diarization step for vtt.py: figures out "who speaks when" in a recording
(via pyannote.audio) and lets the Transcribe step keep only the MAIN NARRATOR's speech, or
label everyone else instead of dropping them.

How it works:
  1. Whisper transcribes as usual and returns timestamped segments.
  2. pyannote.audio splits the audio into speaker turns (SPEAKER_00, SPEAKER_01, ...).
  3. Each Whisper segment is assigned the speaker it overlaps with the most.
  4. The main narrator = the speaker with the most total speaking time in that file.
  5. mode "main": other speakers' segments are dropped.
     mode "label": everything is kept, but other speakers' text is prefixed with
     OTHER_SPEAKER_LABEL.

Requirements (one-time setup, see readme.txt):
  - pip install pyannote.audio      (inside the app's venv)
  - A free Hugging Face account + access token, and accepting the user conditions on
    https://huggingface.co/pyannote/speaker-diarization-community-1
  The token can be passed explicitly, or left empty if HF_TOKEN is set in the environment
  or you've run `hf auth login` once.

pyannote is imported lazily, so the rest of the app keeps working without it installed --
you only need it if you turn this option on.

VERSIONING: own __version__, independent of the other files (see version.py).
"""

__version__ = "1.1.0"

# Hugging Face token used when none is given in the GUI/CLI (personal copy -- don't share this file).
DEFAULT_HF_TOKEN = "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"

DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
OTHER_SPEAKER_LABEL = "[Otro hablante]"
SAMPLE_RATE = 16000

MODES = ("off", "main", "label")


def load_pipeline(hf_token=None, log=print):
    """Loads the pyannote diarization pipeline once (reuse it across a batch). Raises a
    RuntimeError with setup instructions if pyannote isn't installed or the model can't be
    downloaded (usually: missing token, or the model's conditions not accepted yet)."""
    try:
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise RuntimeError(
            "Speaker filtering needs pyannote.audio, which isn't installed. In the app's venv run:\n"
            "    pip install pyannote.audio\n"
            f"(import error: {e})"
        )

    import os
    token = (hf_token or "").strip() or os.environ.get("HF_TOKEN") or DEFAULT_HF_TOKEN or None

    log(f"Loading speaker-diarization model '{DIARIZATION_MODEL}'...")
    try:
        try:
            pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, token=token)
        except TypeError:
            # pyannote.audio 3.x used `use_auth_token` instead of `token`
            pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, use_auth_token=token)
    except Exception as e:
        raise RuntimeError(
            "Could not load the speaker-diarization model. Check that (1) your Hugging Face token is "
            "correct and (2) you've accepted the model's conditions at "
            f"https://huggingface.co/{DIARIZATION_MODEL}\n(details: {e})"
        )
    if pipeline is None:
        # pyannote returns None (rather than raising) on some auth failures
        raise RuntimeError(
            "The speaker-diarization model didn't load -- usually a missing/invalid Hugging Face token "
            f"or the conditions at https://huggingface.co/{DIARIZATION_MODEL} not accepted yet."
        )
    # 1.1.0: run on the Apple Silicon GPU (MPS) when available. On CPU, diarizing a 2-3 hour
    # class recording took far longer than Whisper itself (the speaker-embedding step runs over
    # the whole file), which made the speaker filter ~50x slower overall. If MPS hits an
    # unsupported op, diarize() retries that file on CPU automatically.
    _move_pipeline(pipeline, _best_device(), log)
    return pipeline


def _best_device():
    import torch
    return "mps" if torch.backends.mps.is_available() else "cpu"


def _move_pipeline(pipeline, device, log=print):
    import torch
    try:
        pipeline.to(torch.device(device))
        pipeline._vtt_device = device
        log(f"--> Speaker diarization will run on {device.upper()}")
    except Exception as e:
        pipeline._vtt_device = "cpu"
        log(f"--> Could not move diarization to {device.upper()} ({e}); using CPU")


class _Hook:
    """pyannote progress hook -> 0..1 fraction callback. pyannote calls it as
    hook(step_name, step_artifact, file=None, total=None, completed=None) for each step."""

    def __init__(self, callback):
        self.callback = callback

    def __call__(self, step_name=None, step_artifact=None, file=None, total=None, completed=None, **kwargs):
        if self.callback and total and completed is not None:
            try:
                self.callback(min(1.0, completed / total))
            except Exception:
                pass


def diarize(pipeline, media_path, log=print, progress_callback=None):
    """Runs diarization on one file and returns a list of (start, end, speaker) tuples.
    Audio is decoded with Whisper's own ffmpeg loader (16 kHz mono) and handed to pyannote
    in memory, so it works on MP4 and MP3 alike without pyannote needing its own decoder."""
    import torch
    import whisper

    log("--> Identifying speakers (diarization)... (this adds some extra time)")
    audio = whisper.load_audio(media_path)  # float32 numpy array, 16 kHz mono
    waveform = torch.from_numpy(audio).unsqueeze(0)  # shape (1, samples)

    import time
    t0 = time.time()
    try:
        output = pipeline({"waveform": waveform, "sample_rate": SAMPLE_RATE}, hook=_Hook(progress_callback))
    except Exception as e:
        if getattr(pipeline, "_vtt_device", "cpu") == "cpu":
            raise
        log(f"--> Diarization failed on {pipeline._vtt_device.upper()} ({e}); retrying on CPU (slower)...")
        _move_pipeline(pipeline, "cpu", log)
        t0 = time.time()
        output = pipeline({"waveform": waveform, "sample_rate": SAMPLE_RATE}, hook=_Hook(progress_callback))
    log(f"--> Diarization took {_fmt_secs(time.time() - t0)} "
        f"(audio length {_fmt_secs(len(audio) / SAMPLE_RATE)})")

    # pyannote 4.x returns an object with .exclusive_speaker_diarization (no overlapping turns --
    # best for assigning one speaker per segment) and .speaker_diarization; 3.x returns the
    # Annotation directly.
    annotation = (getattr(output, "exclusive_speaker_diarization", None)
                  or getattr(output, "speaker_diarization", None)
                  or output)

    if hasattr(annotation, "itertracks"):
        items = ((turn, speaker) for turn, _track, speaker in annotation.itertracks(yield_label=True))
    else:
        items = iter(annotation)

    turns = [(float(turn.start), float(turn.end), str(speaker)) for turn, speaker in items]
    turns.sort()
    return turns


def _fmt_secs(sec):
    sec = int(round(sec))
    return f"{sec // 3600}h{sec % 3600 // 60:02d}m{sec % 60:02d}s"


def speaker_durations(turns):
    """Total speaking time per speaker, in seconds."""
    totals = {}
    for start, end, speaker in turns:
        totals[speaker] = totals.get(speaker, 0.0) + max(0.0, end - start)
    return totals


def main_speaker(turns):
    """The speaker with the most total speaking time, or None if no turns were found."""
    totals = speaker_durations(turns)
    return max(totals, key=totals.get) if totals else None


def assign_speakers(segments, turns):
    """Returns one speaker label per Whisper segment: whichever speaker overlaps it the most
    in time. Segments with no overlap at all (e.g. in a pause) get the nearest turn's speaker,
    or None if there are no turns."""
    labels = []
    for seg in segments:
        s, e = float(seg["start"]), float(seg["end"])
        overlap = {}
        for ts, te, spk in turns:
            if te <= s:
                continue
            if ts >= e:
                break  # turns are sorted by start time
            overlap[spk] = overlap.get(spk, 0.0) + (min(e, te) - max(s, ts))
        if overlap:
            labels.append(max(overlap, key=overlap.get))
        elif turns:
            mid = (s + e) / 2
            nearest = min(turns, key=lambda t: min(abs(mid - t[0]), abs(mid - t[1])))
            labels.append(nearest[2])
        else:
            labels.append(None)
    return labels


def filter_segments(segments, turns, mode, log=print):
    """Applies the speaker filter to Whisper's segments.

    mode "main":  returns only the main narrator's segments.
    mode "label": returns all segments; other speakers' text is prefixed with OTHER_SPEAKER_LABEL.
    Segments are returned as shallow copies, so Whisper's original result is untouched.
    If no speakers were detected at all, returns the segments unchanged (with a warning)."""
    if mode not in ("main", "label"):
        return list(segments)

    narrator = main_speaker(turns)
    if narrator is None:
        log("--> Warning: no speakers detected -- keeping the full transcript unfiltered.")
        return list(segments)

    totals = speaker_durations(turns)
    grand_total = sum(totals.values()) or 1.0
    log(f"--> Detected {len(totals)} speaker(s). Main narrator: {narrator} "
        f"({100 * totals[narrator] / grand_total:.0f}% of speaking time)")

    labels = assign_speakers(segments, turns)
    out = []
    dropped = 0
    for seg, spk in zip(segments, labels):
        is_narrator = spk == narrator or spk is None
        if mode == "main":
            if is_narrator:
                out.append(dict(seg))
            else:
                dropped += 1
        else:  # label
            new_seg = dict(seg)
            if not is_narrator:
                new_seg["text"] = f" {OTHER_SPEAKER_LABEL} {seg['text'].strip()}"
                dropped += 1
            out.append(new_seg)

    verb = "Removed" if mode == "main" else "Labeled"
    log(f"--> {verb} {dropped} of {len(segments)} segment(s) from other speakers.")
    return out


def build_text(segments, mode):
    """Rebuilds the plain-text transcript from (filtered) segments. In "label" mode, a new
    paragraph starts whenever the speaker changes, so the other speakers' parts stand out."""
    if mode != "label":
        return " ".join(seg["text"].strip() for seg in segments if seg["text"].strip())

    paragraphs, current, current_is_other = [], [], None
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        is_other = text.startswith(OTHER_SPEAKER_LABEL)
        if current and is_other != current_is_other:
            paragraphs.append(" ".join(current))
            current = []
        if is_other and current:  # consecutive other-speaker segments: label only once
            text = text[len(OTHER_SPEAKER_LABEL):].strip()
        current.append(text)
        current_is_other = is_other
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)
