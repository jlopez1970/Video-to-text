#!/usr/bin/env python3
"""
Corrects recurring Whisper mis-hearings (tickers, index names, trading jargon) in
transcript files, using a glossary of known garbled forms -> correct terms. Built for
this specific instructor's trading class recordings -- see correctionglossary.md in
this same folder for the full documented reasoning behind each mapping.

Used as the third step in the app: Download (link -> folder A) -> Transcribe (folder A
-> TXT/VTT in folder B) -> Correct (folder B -> cleaned-up TXT/VTT/SRT/MD/DOCX files).

DOCX note: python-docx doesn't let us regex the raw text of a .docx (it's a zipped XML
document with text split across "runs" for formatting), so it's handled separately from
the plain-text formats -- see correct_docx() below for how and its formatting tradeoff.
Known limitation: corrections are applied one paragraph at a time in DOCX files (see
_correct_docx_paragraph), so a garbled phrase that spans two paragraphs can't be fixed
there even though the equivalent TXT/VTT/SRT/MD fix (applied to the whole file as one
string) would catch it. This has been observed for real: a VTT->DOCX conversion split
"dejo mi stop" across two cues/paragraphs in one transcript. None of the entries below
currently span more than one word for this reason -- multi-word corrections stay
confined to patterns that plausibly land within a single cue.

NOTE ON KEEPING THIS FILE IN SYNC WITH correctionglossary.md: TICKER_CORRECTIONS and
CONTEXTUAL_CORRECTIONS below are a manually-maintained transcription of that glossary,
not something that's read from it automatically. Whenever correctionglossary.md gets a
new confirmed entry, it needs to be added here too, by hand -- there's no code path that
loads the .md at runtime. If this file and the glossary drift, the glossary is the
source of truth; this file is what's actually running.

VERSIONING: this file carries its own __version__, independent of gui.py's and vtt.py's --
each file is versioned on its own schedule now, bumped only when that specific file
changes. See version.py for the current version of every component in one place and the
combined changelog. Shown via `python transcript_corrector.py --version` on the CLI.
"""
import argparse
import glob
import os
import re
import sys

from vtt import sanitize_filename

__version__ = "1.4.5"

TEXT_EXTENSIONS = (".txt", ".vtt", ".srt", ".md")
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS + (".docx",)

# Per user request: a corrected .vtt is saved with a .txt extension instead of .vtt --
# content is left exactly as-is (WEBVTT header, cue numbers, timestamps all untouched),
# only the output file's extension changes. .srt is deliberately NOT in this tuple -- the
# user's choice was to leave .srt corrected-in-place, still saved as .srt.
RENAME_TO_TXT_EXTENSIONS = (".vtt",)

# Comprehensive correction dictionary based on correctionglossary.md
# Maps common transcription errors to their correct financial terms.
#
# IMPORTANT: only put a mapping here if it's safe as a blind, context-free substitution --
# i.e. the garbled string essentially never occurs with any other meaning in this material.
# Anything that collides with an ordinary word or another real ticker/term belongs in
# CONTEXTUAL_CORRECTIONS instead (or a dedicated function, like _correct_amad/_correct_dado
# below), where it can be checked against surrounding text before being applied. "dado" and
# "nada" are the two clearest examples of this distinction -- bare "dado" is deliberately
# NOT here (see CONTEXTUAL_CORRECTIONS / _correct_dado) after confirmed false positives on
# ordinary Spanish like "se han dado cuenta" ("you've realized") and "ha dado el discurso"
# ("has given the speech") -- ordinary uses of "dar" (to give), not the Dow. "dao" IS safe
# here (further down, with the other Dow Jones entries) since it isn't a real Spanish word.
TICKER_CORRECTIONS = {
    # Tickers & Indices
    r'\bespy\b': 'SPY',
    r'\bes piguar\b': 'SPY',
    r'\bespigua\b': 'SPY',
    r'\bespibua\b': 'SPY',
    r'\bes pivua\b': 'SPY',
    r'\bspi guay\b': 'SPY',
    r'\bsp y\b': 'SPY',
    r'\bspy\b': 'SPY',
    r'\bsp\s+y\b': 'SPY',
    # New variants confirmed in correctionglossary.md v1.5 (2026-09-11 validation pass) --
    # same underlying mishearing family as the entries above, just with more spelling drift.
    r'\bpiwa\b': 'SPY',
    r'\bespiway\b': 'SPY',
    r'\bespiwai\b': 'SPY',
    r'\bespiguaio\b': 'SPY',
    r'\bespiguaya\b': 'SPY',

    r'\bnazda\b': 'Nasdaq',
    r'\bnasa\b': 'Nasdaq',
    # New in v1.5 (2026-09-11 pass): "Nasda" (missing the final "q") was a major uncaught
    # gap -- 23 instances in one pass, 0 corrected. "nazar" (1x) is also added as safe.
    # "nasta" (9x) was flagged there as "somewhat ambiguous... could plausibly be a fragment
    # of something else" and is deliberately left out here pending verification, same
    # treatment as "bebap" below.
    r'\bnasda\b': 'Nasdaq',
    r'\bnazar\b': 'Nasdaq',

    r'\brostro\b': 'Russell',
    r'\brosel\b': 'Russell',
    r'\bross el\b': 'Russell',
    r'\brosa el\b': 'Russell',
    r'\brossell\b': 'Russell',
    r'\brossel\b': 'Russell',
    r'\brussel\b': 'Russell',

    # Multi-word "dado"/"dao" phrases are unambiguous (nobody says "he dado jones" as the
    # verb "dar"), so these stay as safe blind substitutions. The standalone single-word
    # "dado"/"dao" -> Dow mapping was removed -- see CONTEXTUAL_CORRECTIONS below.
    r'\bdado jones\b': 'Dow Jones',
    # "dao jones" (full "jones", as opposed to "dao yons"/"dao john" below) was a gap found
    # while testing the 2026-09-11 _correct_dado() fix -- it fell through every existing
    # multi-word pattern and only got the bare "dao"->Dow substitution, leaving "jones"
    # lowercase and un-substituted.
    r'\bdao jones\b': 'Dow Jones',
    r'\bdao yons\b': 'Dow Jones',
    r'\bdado yons\b': 'Dow Jones',
    r'\bdao john\b': 'Dow Jones',
    # "Dow Jon" -- Whisper gets "Dow" right but truncates "Jones" to "Jon". Also cover the
    # pre-conversion "dao"/"dado" + "jon" spellings, just in case.
    r'\bdow jon\b': 'Dow Jones',
    r'\bdao jon\b': 'Dow Jones',
    r'\bdado jon\b': 'Dow Jones',
    # New in v1.5: "daoyons"/"daoyón" is a fully fused "Dow Jones" (no space) -- 24 instances
    # in one pass, 0 corrected, distinct enough from the space-separated forms above that it
    # needs its own check. "dado y once" (1x) is a garbled multi-word phrase, unambiguous the
    # same way "dado jones" is (nobody says this as the ordinary verb "dar").
    r'\bdaoyons\b': 'Dow Jones',
    r'\bdaoyón\b': 'Dow Jones',
    r'\bdado y once\b': 'Dow Jones',
    # Bare "dao" (not "dado") is NOT an ordinary Spanish word -- unlike "dado" (past
    # participle of "dar"), it has no other meaning in this material, so it's safe as an
    # unconditional blind substitution. Must come AFTER every multi-word "dao ___" pattern
    # above in this dict so those consume first (e.g. "dao jones" -> "Dow Jones" whole),
    # leaving this to only catch genuinely standalone "dao" (e.g. "Ok, miren, Dao ya",
    # "Russell, y Dao, entonces..." -- both confirmed-missed in the v1.5 validation pass).
    r'\bdao\b': 'Dow',

    r'\bbix\b': 'VIX',
    r'\bbixi\b': 'VIXI',
    r'\bvixiel\b': 'VIXI',
    r'\bvixico\b': 'VIXI',
    r'\bbíxico\b': 'VIXI',

    r'\bsan piquiniento\b': 'S&P 500',
    r'\besamtiquimiento\b': 'S&P 500',
    r'\besan pique\b': 'S&P 500',
    r'\besanpi\b': 'S&P 500',

    r'\bcb oe\b': 'CBOE',
    r'\bcibio\b': 'CBOE',
    r'\bchico por options exchange\b': 'CBOE',

    r'\bwallman\b': 'Walmart',
    r'\bwallmark\b': 'Walmart',
    r'\bwall mar\b': 'Walmart',

    r'\bvava\b': 'BABA',
    r'\bbabex\b': 'BABX',
    r'\bva ex\b': 'BABX',
    r'\bvex\b': 'BABX',

    r'\bbáidu\b': 'Baidu',
    r'\bbail\b': 'Baidu',

    # "ggl"/"gg ll" default to GOOGL (the stock). When the surrounding text signals a
    # leveraged-ETF context ("apalancado"/"leveraged" etc., alongside the instructor's other
    # leveraged single-stock ETFs like METU/MSFU/AMZU), it's more likely GGLL, the leveraged
    # product -- see _correct_ggl below. This distinction is inferred from the pattern of
    # sibling tickers in one comparison, not yet confirmed against audio -- verify against
    # the instructor's actual product list if possible.
    r'\bbg u\b': 'BGU',
    r'\babgu\b': 'ABGU',

    r'\bmuu\b': 'MUU',
    r'\bme uca\b': 'MUU',

    r'\bcbrj\b': 'CBRG',
    r'\bcbrg\b': 'CBRG',
    r'\bsi rubras\b': 'Cerebras',
    r'\bscribras\b': 'Cerebras',
    r'\bcerebras\b': 'Cerebras',

    r'\bfutg\b': 'FUTG',
    r'\bfeuteje\b': 'FUTG',
    r'\bfute\b': 'FUTG',

    r'\bjean\b': 'YINN',
    r'\bjin\b': 'YINN',

    r'\bcod\b': 'SOXL',

    r'\bel cat y boot\b': 'Cathie Wood',
    r'\bkathy wood\b': 'Cathie Wood',

    r'\bcomprobada alfabetes\b': 'Berkshire Hathaway buying Alphabet',
    r'\bdonaldsron\b': 'Donald Trump',
    # NOTE: "pelo amarillo" removed from this table. A prior automated correction pass
    # over-corrected it into a redundant "Donald Trump" right before the instructor said the
    # real name in the very next cue -- it's a deliberate descriptive aside/joke setup, not a
    # mis-transcription. Leave it as spoken.

    # Indicators & Jargon
    r'\bmac d\b': 'MACD',
    r'\bmcd\b': 'MACD',
    r'\bmacdeep\b': 'MACD',
    r'\bmac deep\b': 'MACD',

    r'\br6\b': 'RSI',
    r'\brci\b': 'RSI',

    r'\bcf a y\b': 'CFI',
    r'\bcf i\b': 'CFI',
    r'\bcfi\b': 'CFI',
    r'\bsifi\b': 'CFI',
    r'\bsi es fi\b': 'CFI',
    r'\bcifi\b': 'CFI',
    r'\bc_i_e_f_i\b': 'CFI',

    r'\bpms 80\b': 'PM80',
    r'\bpm 80\b': 'PM80',
    r'\bpm 40\b': 'PM40',
    r'\bpm 100\b': 'PM100',
    r'\bpm 200\b': 'PM200',
    r'\bpmy 80\b': 'PM80',
    r'\bpme 80\b': 'PM80',
    r'\bpme80\b': 'PM80',
    r'\bpm80\b': 'PM80',

    r'\bbbap\b': 'VBAP',
    r'\bbibap\b': 'VBAP',
    r'\bvibap\b': 'VBAP',
    # New in v1.5: "IVAP" is a distinct-enough spelling that it wasn't caught by the existing
    # bbap/Vibap matches -- 3 instances in one pass, 0 corrected.
    r'\bivap\b': 'VBAP',
    r'\bvwap\b': 'VWAP',
    # NOTE: "bebap" used to map to both VBAP and VWAP as two entries with the same regex key
    # in this dict -- since Python dict literals silently keep only the last duplicate key,
    # "bebap" was actually only ever being corrected to VWAP, never VBAP, despite the
    # original comment implying both were intended. Rather than guess which one is right,
    # "bebap" has been pulled out entirely -- see the Unresolved section of
    # correctionglossary.md; add it back here once it's confirmed against audio.

    r'\bchachi piti\b': 'ChatGPT',
    r'\bchagipiti\b': 'ChatGPT',
    r'\bchay bittie\b': 'ChatGPT',
    r'\bchagip y timeo\b': 'ChatGPT',

    r'\bprice to earning\b': 'P/E',
    r'\bprize toer en\b': 'P/E',
    r'\bpeck\b': 'PEG',
    r'\bpec\b': 'PEG',

    # Phonetic segmentation errors
    r'\bal cista\b': 'alcista',
    r'\bel cista\b': 'alcista',
    r'\balsista\b': 'alcista',
    r'\bal sista\b': 'alcista',
    r'\bel sista\b': 'alcista',
    # New in v1.5: ~7 residual misses where the word-boundary break separated differently
    # than the standard "al cista"/"al sista" pattern -- the leading "al"/"el" fuses onto the
    # *previous* word instead (e.g. "una estructural sista", "la tendencia el sista"). This
    # bare-token catch-all (suggested directly in correctionglossary.md) must stay AFTER the
    # more specific "al sista"/"el sista" entries above, so those are consumed first and this
    # only catches the leftover orphaned "sista" instances -- dict order is preserved and
    # correct_text() applies TICKER_CORRECTIONS in insertion order.
    r'\bsista\b': 'alcista',

    r'\bel segos\b': 'el sesgo',
    r'\bel sejo\b': 'el sesgo',

    r'\bputz\b': 'put',
    r'\bcol\b': 'call',
    r'\bcols\b': 'calls',
    r'\bcoals\b': 'calls',

    # A single fused mis-hearing (not a real Spanish word), so safe as a blind substitution --
    # "dejo mi ESTOPCO" -> "dejo mi stop" ("I leave my stop [-loss] as is"). This is a
    # single-word, single-cue fix, so it isn't affected by the DOCX cross-paragraph
    # limitation noted at the top of this file.
    r'\bestopco\b': 'stop',
}

# Advanced contextual rules for ambiguous words. Each of these collides with either an
# ordinary Spanish word or another real ticker, so -- unlike TICKER_CORRECTIONS above --
# they only fire when specific surrounding words make the intended meaning clear.
CONTEXTUAL_CORRECTIONS = [
    # "nada" is Spanish for "nothing", but often transcribed when they say "Nasdaq"
    # Example: "el nada", "del nada", "nada subiendo", "nada bajando"
    (r'\b(el|del|un|al)\s+nada\b', r'\1 Nasdaq'),
    (r'\bnada\s+(subiendo|bajando|corrigiendo|rompiendo|comprimiendo|extendido|lateral|en techo|en piso|zona|con volumen)\b', r'Nasdaq \1'),
    (r'\b(subió|cayó|corrigió|rompió|abrió)\s+nada\b', r'\1 Nasdaq'),

    # "dado" (unlike "dao", handled as a blind substitution above -- it isn't a real Spanish
    # word) is Spanish for "given" (past participle of "dar", used constantly in ordinary
    # phrases like "dado cuenta"/"dado que"), but also this instructor's frequent way of
    # saying "Dow" -- these two templates catch the clearest cases (preceded by an article/
    # preposition, or followed by a specific verb), never as a blanket \bdado\b substitution
    # (that produced confirmed wrong output: "se han dado cuenta" -> "se han Dow cuenta",
    # "ha dado el discurso" -> "ha Dow el discurso"). Cases that slip past both templates
    # (e.g. "Dado todavía se mantiene bien", "dado segundo") are caught afterward by the
    # wider-window _correct_dado() function below, same technique as _correct_amad/_correct_ggl.
    (r'\b(el|del|un|al|con|sobre|hacia|tengo)\s+dado\b', r'\1 Dow'),
    (r'\bdado\s+(subiendo|bajando|corrigiendo|rompiendo|comprimiendo|cayendo|persiste|se mantiene)\b', r'Dow \1'),

    # "biden" as "Baidu"
    (r'\b(el|del|un)\s+biden\b', r'\1 Baidu'),
    (r'\bbiden\s+(subiendo|bajando|corrigiendo|rompiendo|comprimiendo|reportó|earnings|compró)\b', r'Baidu \1'),
    (r'\b(compró|vendió|comprar|vender)\s+biden\b', r'\1 Baidu'),

    # A fused mis-hearing of "déjenlo quieto" ("leave it alone/be still") into fragmented
    # nonsense syllables. Distinct style from the other entries here -- one word split into
    # several fake ones, rather than a real word colliding with another meaning -- but kept
    # in this list rather than TICKER_CORRECTIONS since it's a multi-word phrase match.
    (r'\bahí,?\s*ve,?\s*en lo hay quieto\b', 'déjenlo quieto'),
]

# "amad" is ambiguous between AMAT (Applied Materials) and AMD. A simple adjacent-word check
# is too narrow -- the one confirmed real-world wrong case had the AMAT-context words
# ("tierras raras", "componentes"/manufacturing, "revenues") several sentences before "amad"
# actually appeared, so a same-sentence-only rule never fired and it fell through to the
# generic AMD default, which was wrong. Instead this searches a wider character window
# around each match for any supply-chain/materials trigger word.
AMAT_CONTEXT_WORDS = re.compile(
    r'\b(capex|material|materials|tierras|raras|ormuz|componentes?|manufactura|'
    r'revenue|revenues|cadena de suministro|semiconductor(?:es)?|equipos)\b',
    re.IGNORECASE,
)
AMAT_CONTEXT_WINDOW = 300  # characters searched before and after each "amad" match


def _correct_amad(text):
    """Replaces every standalone 'amad' with AMAT if a supply-chain/materials trigger word
    appears within AMAT_CONTEXT_WINDOW characters on either side, else AMD (the more common
    generic reading)."""
    def replace(match):
        start = max(0, match.start() - AMAT_CONTEXT_WINDOW)
        end = min(len(text), match.end() + AMAT_CONTEXT_WINDOW)
        window = text[start:end]
        return 'AMAT' if AMAT_CONTEXT_WORDS.search(window) else 'AMD'

    return re.compile(r'\bamad\b', re.IGNORECASE).sub(replace, text)


# "ggl"/"gg ll" is ambiguous between GOOGL (the stock) and GGLL (Direxion's leveraged-Google
# ETF). Inferred from the pattern of the instructor's other leveraged single-stock ETF
# tickers appearing alongside an explicit "(ETF apalancado)"/"leveraged" label in the same
# material (METU, MSFU, AMZU) -- NOT yet confirmed against audio, so treat this as a
# best-effort heuristic rather than a settled mapping; verify against the instructor's
# actual product list if possible.
GGLL_CONTEXT_WORDS = re.compile(
    r'\b(apalancado|apalancada|leveraged|etf)\b',
    re.IGNORECASE,
)
GGLL_CONTEXT_WINDOW = 80  # characters -- deliberately tighter than AMAT's, since this is a
# same-clause label ("GGL (ETF apalancado)") rather than context from several sentences away


def _correct_ggl(text):
    """Replaces every standalone 'ggl'/'gg ll' with GGLL if a leveraged-ETF label appears
    within GGLL_CONTEXT_WINDOW characters, else GOOGL (the underlying stock)."""
    def replace(match):
        start = max(0, match.start() - GGLL_CONTEXT_WINDOW)
        end = min(len(text), match.end() + GGLL_CONTEXT_WINDOW)
        window = text[start:end]
        return 'GGLL' if GGLL_CONTEXT_WORDS.search(window) else 'GOOGL'

    return re.compile(r'\bggl\b|\bgg ll\b', re.IGNORECASE).sub(replace, text)


# "dado" is the single most severe, most inconsistent failure mode documented in
# correctionglossary.md (see its Dow row and the 2026-09-11 validation entry): the two
# CONTEXTUAL_CORRECTIONS templates above only catch it right after a specific article/
# preposition or right before a specific verb, but real transcripts show dozens of missed
# Dow references that match neither template -- e.g. "Dado todavía se mantiene bien", "para
# estrategias calls está de primero, dado segundo", a rapid SPY/Nasdaq/Dow comparison drill.
#
# This catches the remainder the same way _correct_amad/_correct_ggl do: default to leaving
# it alone (a false positive here reproduces the exact "se han dado cuenta" -> "se han Dow
# cuenta" bug already fixed once, which is worse than a missed correction), UNLESS (a) it
# doesn't match one of the known ordinary-"dar"/adjectival-"given" patterns immediately
# around it, AND (b) trading/index-comparison vocabulary appears nearby.
DADO_VERB_EXCLUDE_BEFORE = re.compile(
    # haber-auxiliary immediately before ("ha dado", "habían dado", "haberse dado",
    # "hubiese dado", ...) -- the ordinary verb, all tenses/moods/persons including the
    # bare infinitive and reflexive ("haber", "haberse") and the subjunctive ("hubiera"/
    # "hubiese" families), found missing during a 2026-09-11 real-file comparison (no wrong
    # conversion resulted there since no ticker was nearby, but a future file could differ).
    r'\b(he|has|ha|hemos|habéis|han|había|habías|habíamos|habíais|habían|'
    r'hube|hubiste|hubo|hubimos|hubisteis|hubieron|'
    r'hubiera|hubieras|hubiéramos|hubierais|hubieran|'
    r'hubiese|hubieses|hubiésemos|hubieseis|hubiesen|'
    r'haber|haberse)\s*$'
    r'|'
    # ser-copula immediately before ("es dado", "fue dado", ...) -- passive-voice ordinary use.
    r'\b(es|son|era|eran|fue|fueron|será|serán|sería|serían|sean|siendo)\s*$'
    r'|'
    # "given" as an adjective modifying a preceding noun -- a common idiom ("en un momento
    # dado" = "at some point"), not the index.
    r'\b(momento|caso|punto|instante|ejemplo|valor|número|escenario|contexto|nivel)\s*$',
    re.IGNORECASE,
)
DADO_VERB_EXCLUDE_AFTER = re.compile(
    # "dado que" (given that), "dado cuenta" (the original confirmed false-positive case),
    # "dado por" (given by), "dado un/una/unos/unas ___" (given a ___, a conjunctive opener)
    # -- all ordinary/adjectival "given", not the index.
    r'^\s*(que|cuenta|por|un|una|unos|unas)\b',
    re.IGNORECASE,
)
DADO_CONTEXT_WORDS = re.compile(
    r'\b(nasdaq|russell|spy|vix|vixi|s&p|cfi|rsi|macd|pm ?(?:40|80|100|200)|vbap|'
    r'alcista|bajista|resistencia|soporte|retroced\w*|corrig\w*|comprim\w*|romp\w*|'
    r'tendencia|volumen|t[ée]cnico|[ií]ndice|mercado|velas?|cierre|apertura|'
    r'puntos?|tickers?|sub(?:e|en|iendo)|baj(?:a|an|ando)|ca(?:e|en|yendo))\b',
    re.IGNORECASE,
)
DADO_CONTEXT_WINDOW = 300  # characters -- same width as AMAT's, for the same reason: the
# real confirmed cases have the index-comparison words several sentences away, not adjacent.
DADO_EXCLUDE_LOOKAROUND = 20  # characters -- the verb/idiom exclusion signals, unlike the
# context words above, are always immediately adjacent, so a small fixed slice is enough
# and keeps this from re-scanning large amounts of text for every match in a big file.


def _correct_dado(text):
    """Catches standalone 'dado' -> Dow instances that slip past the CONTEXTUAL_CORRECTIONS
    templates above (see the long comment near DADO_CONTEXT_WORDS). Leaves it alone by
    default -- converts only when it doesn't look like the ordinary verb 'dar' or the
    adjectival 'given' (checked via the small exclusion lists immediately before/after)
    AND trading/index vocabulary appears within DADO_CONTEXT_WINDOW characters on either
    side. Does not handle "dao" -- that's a safe blind substitution in TICKER_CORRECTIONS,
    since (unlike "dado") it isn't an ordinary Spanish word at all."""
    def replace(match):
        before = text[max(0, match.start() - DADO_EXCLUDE_LOOKAROUND):match.start()]
        after = text[match.end():match.end() + DADO_EXCLUDE_LOOKAROUND]
        if DADO_VERB_EXCLUDE_BEFORE.search(before) or DADO_VERB_EXCLUDE_AFTER.match(after):
            return match.group(0)

        start = max(0, match.start() - DADO_CONTEXT_WINDOW)
        end = min(len(text), match.end() + DADO_CONTEXT_WINDOW)
        window = text[start:end]
        return 'Dow' if DADO_CONTEXT_WORDS.search(window) else match.group(0)

    return re.compile(r'\bdado\b', re.IGNORECASE).sub(replace, text)


def correct_text(text):
    """Applies every direct substitution, then every contextual rule, then the three
    wider-window disambiguations (amad, ggl, dado), to a block of text."""
    # Pass 1: direct substitutions (case-insensitive keys, but preserving casing where possible)
    for pattern, replacement in TICKER_CORRECTIONS.items():
        regex = re.compile(pattern, re.IGNORECASE)
        text = regex.sub(replacement, text)

    # Pass 2: contextual rules (surrounding words within the same match)
    for pattern, replacement in CONTEXTUAL_CORRECTIONS:
        regex = re.compile(pattern, re.IGNORECASE)
        text = regex.sub(replacement, text)

    # Pass 3: wider-window disambiguations that need to look beyond the immediate match
    text = _correct_amad(text)
    text = _correct_ggl(text)
    text = _correct_dado(text)

    return text


def list_correctable_files(folder):
    """Returns a sorted list of .txt/.vtt/.srt/.md/.docx files directly inside folder,
    skipping any already-corrected output (name contains '_corrected')."""
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(glob.glob(os.path.join(folder, f"*{ext}")))
        files.extend(glob.glob(os.path.join(folder, f"*{ext.upper()}")))
    files = [f for f in files if "_corrected" not in os.path.basename(f)]
    return sorted(files)


def _correct_docx_paragraph(paragraph):
    """Corrects one paragraph's text in a .docx, guaranteeing no correction is missed at a
    run boundary (Word frequently splits a paragraph into multiple 'runs' even without any
    visible formatting difference, and a regex can't match text split across two runs).

    Tradeoff: if a paragraph actually needs a correction and has more than one run, all of
    its text is consolidated into the first run and the rest are cleared -- so formatting
    that varied *within* that one paragraph (e.g. one bold word) collapses to the first
    run's style. Paragraphs that need no correction, and single-run paragraphs, are left
    completely untouched. For machine-generated transcripts (uniform formatting throughout
    each paragraph) this essentially never bites; it matters most for hand-edited docs.

    Known limitation this does NOT solve: a garbled phrase that spans two paragraphs (e.g.
    two adjacent VTT cues converted one-per-paragraph) can't be fixed here, since each
    paragraph's text is corrected independently -- see the module docstring."""
    if not paragraph.runs:
        return
    original = "".join(run.text for run in paragraph.runs)
    corrected = correct_text(original)
    if corrected == original:
        return
    paragraph.runs[0].text = corrected
    for run in paragraph.runs[1:]:
        run.text = ""


def correct_docx(input_path, output_path):
    """Corrects every paragraph (including inside tables) of a .docx file, preserving
    the document's styles/formatting except where noted in _correct_docx_paragraph."""
    try:
        import docx
    except ImportError:
        raise RuntimeError("python-docx is not installed. Install it with: pip install python-docx")

    document = docx.Document(input_path)

    for paragraph in document.paragraphs:
        _correct_docx_paragraph(paragraph)

    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    _correct_docx_paragraph(paragraph)

    document.save(output_path)


def correct_file(input_path, output_path, log=print):
    """Reads input_path, applies every correction, writes the result to output_path.
    Handles .docx separately (see correct_docx); every other supported extension is
    treated as plain text."""
    log(f"Correcting: {os.path.basename(input_path)}...")

    ext = os.path.splitext(input_path)[1].lower()
    if ext == ".docx":
        correct_docx(input_path, output_path)
    else:
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        corrected_content = correct_text(content)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(corrected_content)

    log(f"--> Success! Saved: {os.path.basename(output_path)}")


def resolve_correction_targets(input_path, log=print):
    """Figures out which file(s) to correct: a single file, or every correctable file
    in a directory. Raises FileNotFoundError on an invalid input."""
    if os.path.isdir(input_path):
        files = list_correctable_files(input_path)
        if not files:
            raise FileNotFoundError(
                f"No .txt/.vtt/.srt/.md/.docx files found in the directory '{input_path}'"
            )
        log(f"Batch mode activated. Found {len(files)} files to correct.")
        return files

    if os.path.isfile(input_path):
        return [input_path]

    raise FileNotFoundError(f"'{input_path}' is not a valid file or directory.")


def run_correction_job(input_path, outdir, log=print, output_name=None):
    """High-level orchestration, mirroring vtt.run_job's shape: resolve input, correct
    every file, write into outdir. output_name only applies when exactly one file is
    being processed. Each file's original extension is preserved EXCEPT for extensions
    in RENAME_TO_TXT_EXTENSIONS (currently just .vtt), which are saved as .txt instead --
    content is untouched, only the output extension changes.

    A batch can contain both e.g. clase1.vtt and clase1.txt (typical after a Transcribe
    run with format="both") -- once .vtt's output also becomes clase1_corrected.txt, that
    would collide with .txt's own output of the same name and silently overwrite one with
    the other. To avoid that, this tracks every output path already used in the batch and,
    on a collision, disambiguates by folding the source extension into the name (e.g.
    clase1_corrected_vtt.txt), logging a note explaining why."""
    files = resolve_correction_targets(input_path, log=log)
    os.makedirs(outdir, exist_ok=True)

    effective_output_name = None
    if output_name:
        if len(files) == 1:
            effective_output_name = output_name
        else:
            log(f"Note: a custom output name was given but {len(files)} files matched -- "
                f"ignoring it and keeping each file's own name.")

    used_output_paths = set()
    for index, input_file in enumerate(files, start=1):
        log(f"\n[File {index}/{len(files)}]")
        base_name, ext = os.path.splitext(os.path.basename(input_file))
        name = sanitize_filename(effective_output_name) if effective_output_name else f"{base_name}_corrected"
        output_ext = ".txt" if ext.lower() in RENAME_TO_TXT_EXTENSIONS else ext
        output_path = os.path.join(outdir, f"{name}{output_ext}")

        if output_path in used_output_paths:
            disambiguated_name = f"{name}_{ext.lstrip('.').lower()}"
            output_path = os.path.join(outdir, f"{disambiguated_name}{output_ext}")
            log(f"Note: '{name}{output_ext}' was already used by another file in this batch -- "
                f"saving this one as '{os.path.basename(output_path)}' instead.")

        used_output_paths.add(output_path)
        correct_file(input_file, output_path, log=log)

    log(f"\nCorrection completed successfully! {len(files)} file(s) processed. Saved in: {outdir}")
    return len(files)


def main():
    parser = argparse.ArgumentParser(
        description="Correct recurring Whisper mis-hearings in transcript files using the trading-class glossary."
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("input", help="Path to a single .txt/.vtt/.srt/.md/.docx file, OR a directory containing them.")
    parser.add_argument("-B", "--outdir", default="folder_B_corrected", help="Folder to save corrected file(s) into. Default: ./folder_B_corrected")
    parser.add_argument("-o", "--output-name", default=None, help="Custom base file name for the output (only used when exactly one file is processed). Default: <source name>_corrected")

    args = parser.parse_args()
    print(f"Transcript Corrector v{__version__}")

    try:
        run_correction_job(args.input, args.outdir, output_name=args.output_name)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
