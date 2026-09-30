"""Unified screen-extraction pipeline.

Every container format (tape, disk, snapshot) is reduced to a sequence of
"load events" — each event is "shove these bytes into RAM at this address".
Walk the events into a 64K bytearray; after each write, harvest two kinds
of candidate:

1. **Direct**: if the event's body is exactly 6912 bytes (the SCREEN$
   shape), it might itself be a screen — emit regardless of address.
2. **RAM snapshot**: take the slice ram[$4000..$5AFF] after the write; if
   it's a new picture we haven't seen yet, emit it. This catches screens
   that get loaded as part of a larger CODE block, screens that overwrite
   other memory, etc.

Then rank by priority (filename hint > $4000 address > everything else)
and quality (attribute byte distribution of a real screen). Drop pure
noise. Pillow plugins all share this pipeline; per-format adapters just
yield LoadEvents.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

from .spectrum_screen import SCREEN_BYTES

RAM_SIZE = 0x10000
SCREEN_ADDR = 0x4000

# Filename substrings (case-insensitive) that strongly suggest a screen
# file. Used by rank_screens() to push hinted candidates to frame 0.
SCREEN_NAME_HINTS = ("SCR", "PIC", "TITL", "LOAD", "INTRO", "FRONT", "MAIN")

# Event "kind" tags. Direct emission only fires for kinds where the body
# could plausibly be a screen — never for BASIC programs.
KIND_CODE = "code"
KIND_BASIC = "basic"
KIND_RAW = "raw"
KIND_SNAPSHOT = "snapshot"  # already-populated RAM bank from a snapshot file


@dataclass
class LoadEvent:
    body: bytes
    addr: Optional[int] = None  # load address; None = no defined target
    name: str = ""  # filename (used for ranking hints)
    kind: str = KIND_RAW  # one of KIND_*


@dataclass
class ScreenCandidate:
    body: bytes  # 6912 bytes
    event: LoadEvent
    origin: str  # "direct" or "ram"


def collect_screens(events: Iterable[LoadEvent]) -> list[ScreenCandidate]:
    """Walk a sequence of load events into 64K RAM, harvesting candidates.

    Two kinds of emission, with deliberately different rules:

    **Direct** (explicit): if the event's body is exactly 6912 bytes and
    the kind permits it (any kind except BASIC), emit it as a candidate.
    No dedup — a tape that loads the same screen 5 times yields 5 frames.
    No zero filter either: an explicitly-loaded blank screen is a real
    thing the user might want to see.

    **RAM snapshot** (implicit): after each write, if the slice at
    $4000-$5AFF is non-zero, has *changed* since the previous event, and
    isn't the same bytes we just emitted as a direct candidate from this
    event, emit it. Filters out the trivial "wrote a 6912 CODE block to
    $4000 → both direct and ram fire with identical bytes" case.
    """
    ram = bytearray(RAM_SIZE)
    last_ram_state = bytes(ram[SCREEN_ADDR : SCREEN_ADDR + SCREEN_BYTES])
    out: list[ScreenCandidate] = []

    for event in events:
        # Only write into RAM if the target lies in actual RAM ($4000-$FFFF).
        # Loads to the ROM area ($0000-$3FFF) can't be literal — typically
        # the on-disk header is lying and a runtime loader relocates the
        # data. Modelling those as real writes pollutes screen memory with
        # whatever happens to overlap $4000 (e.g. game code in the middle
        # of a 35KB block claiming to load at $0028).
        if event.addr is not None and event.addr >= SCREEN_ADDR and event.body:
            addr = event.addr & 0xFFFF
            end = min(addr + len(event.body), RAM_SIZE)
            if end > addr:
                ram[addr:end] = event.body[: end - addr]

        direct_blob: bytes | None = None
        if len(event.body) == SCREEN_BYTES and event.kind != KIND_BASIC:
            direct_blob = bytes(event.body)
            out.append(ScreenCandidate(body=direct_blob, event=event, origin="direct"))

        ram_state = bytes(ram[SCREEN_ADDR : SCREEN_ADDR + SCREEN_BYTES])
        if any(ram_state) and ram_state != last_ram_state and ram_state != direct_blob:
            out.append(ScreenCandidate(body=ram_state, event=event, origin="ram"))
        last_ram_state = ram_state

    return out


def screen_quality(body: bytes) -> int:
    """Heuristic 0..80 score: how plausibly is `body` a real SCREEN$?

    Looks at the attribute byte distribution. Real Spectrum screens have:
        - 3..120 distinct attribute values (random data has many more)
        - Low FLASH usage (typically <10% of cells)
    A score of 0 means the bytes look like pure random data — drop those.
    """
    if len(body) < SCREEN_BYTES:
        return 0
    attrs = body[6144:SCREEN_BYTES]
    unique = len(set(attrs))
    flash = sum(1 for b in attrs if b & 0x80)
    score = 0
    if 3 <= unique <= 120:
        score += 50
    elif unique <= 2:
        score += 20  # uniform attributes — could be a monochrome screen
    if flash <= 50:
        score += 30
    elif flash <= 200:
        score += 10
    return score


def _name_hinted(name: str) -> bool:
    upper = name.upper()
    return any(h in upper for h in SCREEN_NAME_HINTS)


def _priority(c: ScreenCandidate) -> int:
    """Lower = better. Filename hints win, then $4000 address, then default."""
    if c.event.name and _name_hinted(c.event.name):
        return 0
    if c.event.addr == SCREEN_ADDR:
        return 1
    if c.origin == "ram":
        return 1
    return 2


def rank_screens(candidates: Iterable[ScreenCandidate]) -> list[bytes]:
    """Sort by (priority, -quality, original order), drop pure noise.

    Identical bodies are NOT deduped — explicit duplicates in the source
    file (e.g. a tape that loads the same screen 5 times) are preserved
    as separate frames.
    """
    scored: list[tuple[int, int, int, bytes]] = []
    for order, c in enumerate(candidates):
        q = screen_quality(c.body)
        # A declared CODE/snapshot screen (or a block aimed at screen RAM)
        # remains valid even when its attribute pattern defeats the heuristic.
        explicit_screen = c.origin == "direct" and (
            c.event.kind in (KIND_CODE, KIND_SNAPSHOT) or c.event.addr == SCREEN_ADDR
        )
        if q == 0 and not explicit_screen:
            continue
        scored.append((_priority(c), -q, order, c.body))
    scored.sort()
    return [body for _, _, _, body in scored]


def extract_screens(events: Iterable[LoadEvent]) -> list[bytes]:
    """End-to-end: events -> candidates -> ranked screen bodies.

    Returns an empty list if the file had no extractable screen. The
    per-format Pillow plugin then raises SyntaxError ("no SCREEN$
    found in container") which surfaces as UnidentifiedImageError —
    honest about "we recognised the format but there's nothing to
    decode" rather than producing a misleading black frame.
    """
    return rank_screens(collect_screens(events))
