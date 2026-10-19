========================================================================
VIDEO-TO-TEXT: DOWNLOAD + WHISPER TRANSCRIPTION (APPLE M3 OPTIMIZED)
========================================================================

This app takes a video link L, downloads it into a local repository A
(via yt-dlp), transcribes it with OpenAI's Whisper model, and saves the
result as TXT and/or WebVTT subtitle files into a folder B. It also still
supports transcribing local MP4/MP3 files or a whole folder of them
directly, without downloading anything. It uses your Apple Silicon M3 GPU
(MPS) for high-speed processing when available.

A third step, Correct, then fixes recurring Whisper mis-hearings (tickers,
index names, trading jargon specific to this source material) in TXT, VTT,
SRT, MD, and DOCX transcript files, using the glossary documented in
correctionglossary.md. (PDF is not supported: a corrected PDF that looks
identical to the original isn't realistically achievable via text
substitution, so PDF was intentionally left out rather than done badly.)

Each file (gui.py, vtt.py, transcript_corrector.py) carries its own
independent version number, bumped on its own schedule -- a change to
one doesn't force the others to bump too. gui.py's title bar and the
label at the bottom of the window show the GUI's own version; each tab
also shows the version of the engine it runs underneath (vtt.py for the
Download/Transcribe tabs, transcript_corrector.py for the Correct tab).
On the command line, `python vtt.py --version` and `python
transcript_corrector.py --version` each report their own file's version.
correctionglossary.md is versioned separately too (see its own header).
version.py is no longer imported by any of these -- it's kept only as a
single place to see every component's current version at a glance, plus
a combined changelog.

------------------------------------------------------------------------
1. PREREQUISITES & INSTALLATION (macOS)
------------------------------------------------------------------------

Open your Terminal application and run the following setup commands:

Step A: Install FFmpeg (System audio/video framework)
Using Homebrew:
brew install ffmpeg

Step B: Install Python Packages
The standard PyTorch build includes built-in support for Apple Silicon GPUs.
yt-dlp is used to download videos from a link. python-docx is only needed for
correcting .docx files in the Correct tab -- everything else works without it.
pip install openai-whisper setuptools-rust torch yt-dlp python-docx

Optional -- only if you're on a work/corporate network: if downloads fail with
"CERTIFICATE_VERIFY_FAILED ... self-signed certificate in certificate chain", your
network inspects HTTPS traffic. Fix it with:
pip install truststore
The app then trusts the same certificates as macOS itself. (Last resort, turns
certificate checking off: set VTT_INSECURE_SSL=1, or pass --no-check-certificate
to vtt.py.)

Step C: Tkinter (only needed for the graphical interface, gui.py)
Tkinter ships with the python.org installer for macOS. If you installed
Python via Homebrew and get an error like "No module named tkinter" when
running gui.py, install it with:
brew install python-tk

Step D (optional): "Main narrator only" speaker filter
Only needed if you want the Transcribe tab's "Speakers" option (see Tab 2 below).
  1. In the app's venv:  pip install pyannote.audio
  2. Create a free Hugging Face account and an access token (read access is
     enough) at https://huggingface.co/settings/tokens
  3. While logged in, open https://huggingface.co/pyannote/speaker-diarization-community-1
     and accept the model's user conditions (one time only).
  4. Paste the token into the Transcribe tab's "HF token" field (it's saved in
     settings.json on this Mac), or set the HF_TOKEN environment variable, or run
     `hf auth login` once and leave the field empty.
The first run downloads the diarization model (a few hundred MB at most).

------------------------------------------------------------------------
2A. RUNNING THE GRAPHICAL INTERFACE (gui.py)
------------------------------------------------------------------------

For a point-and-click experience, run:
python gui.py

This opens a window with three independent tabs -- each has its own Start button and
its own progress log, and they don't depend on each other:

Tab 1: Download (Link -> Folder A)
  - Paste a video link
  - Choose folder A (where the downloaded video is saved)
  - Choose the file name: "Use original video title" (default) or "Custom name"
    with your own name typed in
  - Click "Start Download"

Tab 2: Transcribe (Folder -> Folder B)
  - Choose a source folder (folder A by default, but any folder works)
  - Pick a specific file from that folder (click "Refresh list" to update the
    dropdown), or leave it on "All files in this folder" to batch-process everything
  - When a specific file is selected, an "Output file name" field appears, prefilled
    with the source file's name -- edit it to save the transcript under a different
    name. (Ignored in "All files" mode, since one name can't apply to a whole batch.)
  - Choose folder B, the language, Whisper model size, output format, and whether
    to translate
  - Optional "Speakers" setting (needs Step D above):
      All speakers          -- normal transcript, everyone included (default)
      Main narrator only    -- keeps only the person who talks the most in each
                               file (e.g. the instructor); other voices are removed
      Label other speakers  -- keeps everything, but marks the other voices as
                               "[Otro hablante]" so they're easy to spot or delete
    How it works: after Whisper transcribes, pyannote.audio works out who speaks
    when, and each transcript segment is matched to the speaker talking during it.
    It adds extra processing time, and it isn't perfect: overlapping speech and
    very short interjections ("ok", "sí") can be misassigned, and if a student
    talks more than the instructor in a recording, that student would be treated
    as the "main" speaker. CLI equivalent: vtt.py --speakers main|label [--hf-token ...]
  - Click "Start Transcribe"

Tab 3: Correct (Fix Transcription Errors)
  - Fixes recurring Whisper mis-hearings (tickers, index names, trading jargon) in
    transcript files, using the glossary in correctionglossary.md -- see that file
    for the full documented reasoning behind each correction.
  - Choose a source folder (folder B by default, but any folder works) containing
    .txt / .vtt / .srt / .md / .docx files. DOCX files are corrected in place --
    paragraph text is fixed while formatting (bold, italic, styles) is kept; the
    one exception is a paragraph where a correction spans across two of Word's
    internal formatting "runs" -- fixing that requires merging those runs, which
    can flatten mixed formatting within that one paragraph onto its first run's
    style. Paragraphs that need no correction are always left completely untouched.
    Requires python-docx (see Step B above); other formats don't need it.
  - Pick a specific file (click "Refresh list" to update the dropdown), or leave it
    on "All files in this folder" to batch-correct everything. Files already named
    with "_corrected" are skipped when listing, so re-running never corrects an
    already-corrected file.
  - When a specific file is selected, an "Output file name" field appears, prefilled
    with "<source name>_corrected" -- edit it to save under a different name.
    (Ignored in "All files" mode; each file automatically gets "_corrected" appended
    to its own name instead, so a batch never collides into one output file.)
  - Choose the folder to save corrected file(s) into (folder_B_corrected by default)
  - Click "Start Correct"

All three tabs remember their own last-used settings between runs.

A natural pipeline through all three tabs: Download a video link into folder A,
Transcribe folder A into folder B, then Correct folder B into a cleaned-up final
folder -- each step is a separate, deliberate click, so you can always check the
output of one step before running the next.

------------------------------------------------------------------------
2B. RUNNING FROM THE COMMAND LINE (vtt.py)
------------------------------------------------------------------------

The script accepts one of three things as its main argument:
  - A video URL (e.g. a YouTube link) -> downloaded into repository A, then transcribed
  - A path to a single local MP4/MP3 file -> transcribed directly
  - A path to a folder of local MP4/MP3 files -> all of them transcribed (batch mode)

Transcripts are always written into folder B.

Basic Command Structure:
python vtt.py <link_or_path> [options]

------------------------------------------------------------------------
3. AVAILABLE OPTIONS & FLAGS
------------------------------------------------------------------------

-A, --repo       Repository A: folder where downloaded videos are saved.
                 Only used when the input is a URL. Default: ./repository_A

-B, --outdir     Folder B: folder where the transcript output (TXT/VTT)
                 is saved. Default: ./folder_B

-f, --format     Output format(s) to write into folder B: txt, vtt, or both.
                 Default: both

-l, --lang       Force the language of the source audio.
                 Default is "es" (Spanish).
                 Use "-l auto" to let the AI guess the language.

-t, --translate  Translate foreign language audio directly into
                 English text inside the output file(s).

-m, --model      Choose the Whisper model size. Options include:
                 tiny, base, small, medium, large
                 Default is "base" (good balance of speed and accuracy).

-n, --download-name  Custom file name for the downloaded video (only used when the
                 input is a URL). Default: the video's original title.

-o, --output-name    Custom base file name for the transcript output (only used
                 when exactly one file is processed -- ignored, with a note printed,
                 if a folder with multiple files is processed instead).
                 Default: same name as the source file.

------------------------------------------------------------------------
3B. MEMORY USAGE
------------------------------------------------------------------------

The "large" Whisper model can use much more RAM than its ~3GB of weights
suggests -- often 15-25GB+ -- when running on Apple Silicon's MPS (GPU)
backend. This isn't a bug specific to this app: MPS often has to fall back
to 32-bit math for operations Whisper uses (roughly doubling memory), and
its caching allocator holds onto freed GPU buffers instead of returning
them to the OS. Since Apple Silicon shares one memory pool between CPU and
GPU, that shows up as high process memory in Activity Monitor.

What this app already does about it:
  - Releases cached GPU memory between files during a batch run, so
    memory doesn't keep climbing file-over-file.
  - Releases the model and its memory once a Transcribe job finishes.

What you can do:
  - Use "medium" instead of "large" -- substantially less memory for a
    modest accuracy tradeoff. "small" is lighter still.
  - PyTorch's MPS backend allows memory use up to 1.7x the GPU's
    recommended working set by default (PYTORCH_MPS_HIGH_WATERMARK_RATIO),
    which is a big part of why "large" can balloon so high. You can make
    it clean up more proactively during a run instead of letting usage
    climb, by lowering the "low watermark" (the level at which PyTorch
    starts garbage-collecting/committing more often) before launching:
    PYTORCH_MPS_LOW_WATERMARK_RATIO=1.0 python gui.py
  - Or set a hard ceiling with PYTORCH_MPS_HIGH_WATERMARK_RATIO=1.0 (caps
    memory at the GPU's recommended working set) -- this trades runaway
    memory growth for the job failing with an out-of-memory error if it
    would have needed more, so it's best combined with a smaller model
    rather than used with "large" on its own.

------------------------------------------------------------------------
3C. CORRECTING TRANSCRIPTS FROM THE COMMAND LINE (transcript_corrector.py)
------------------------------------------------------------------------

python transcript_corrector.py <path> [options]

<path> is a single .txt/.vtt/.srt/.md/.docx file, or a directory containing them.

-B, --outdir     Folder to save corrected file(s) into.
                 Default: ./folder_B_corrected

-o, --output-name    Custom base file name for the output (only used when
                 exactly one file is processed; each file's original
                 extension is always kept). Default: <source name>_corrected

Example: python transcript_corrector.py folder_B -B folder_B_corrected

------------------------------------------------------------------------
4. EXAMPLES
------------------------------------------------------------------------

Example 1: Download a video from a link and transcribe it (Spanish, default folders)
python vtt.py "https://www.youtube.com/watch?v=XXXXXXXXXXX"
-> Video saved in ./repository_A, transcript (TXT + VTT) saved in ./folder_B

Example 2: Same, but choose custom repository A and folder B locations
python vtt.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" -A ~/Videos/Downloaded -B ~/Documents/Transcripts

Example 3: Download and translate directly into English, TXT only
python vtt.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" -t -f txt

Example 4: Transcribe a single local Spanish MP3 audio track (no download)
python vtt.py cancion.mp3

Example 5: Batch process an entire local folder containing a mix of MP4s and MP3s
python vtt.py /Users/yourusername/Movies/Lectures/ -B /Users/yourusername/Movies/Transcripts

Example 6: Use a larger model for maximum accuracy on tough audio recordings
python vtt.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" -m small
