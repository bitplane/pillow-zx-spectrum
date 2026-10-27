"""Pillow plugin for ZX Spectrum +3 / Amstrad CPC disk images (.dsk).

Two on-disk variants:
- "Standard" CPC DSK: magic ``MV - CPC``. All tracks are the same size,
  given by a 16-bit field in the disk-info header. Sector size is fixed
  per track via the size code.
- "Extended" CPC DSK: magic ``EXTENDED CPC DSK File``. Tracks may differ
  in size; the disk-info header carries a track size table (one byte per
  track-side, in 256-byte units; 0 means the track is absent). Sector
  sizes are read from each sector's info entry (used for protected disks
  with weak/long sectors).

Layout:
    [0..255]   Disk-Info (256 bytes)
    [256..]    Per-track blocks, each = 256-byte Track-Info + sectors

Each Track-Info block holds:
    16   track number
    17   side number
    20   sector size code (size = 128 << code)
    21   number of sectors
    24+  Sector-Info List, 8 bytes per sector:
            +0 track, +1 side, +2 sector ID (R), +3 size code,
            +4..5 FDC status, +6..7 actual length (extended DSK only)

After Track-Info, the sectors are stored in physical (on-disk) order. To
read a file, sectors must be re-sorted by sector ID per track.

Screen extraction: parse to logical sectors -> concatenate -> scan for
``PLUS3DOS\\x1A`` headers at sector boundaries -> first CODE file with
length 6912 is the loading screen.
"""

import struct

from PIL import Image

from typing import Iterator

from .cpm import detect_reserved_offset, directory_looks_valid, list_files
from .disk import DiskImage, Sector
from .loader import KIND_BASIC, KIND_CODE, KIND_RAW, LoadEvent
from .loader import extract_screens as _extract_screens_from_events
from .pillow_screen import ScreenSequenceImageFile
from .plus3dos import TYPE_CODE, TYPE_PROGRAM, find_plus3_files, parse_at

DSK_MAGIC_EXT = b"EXTENDED CPC DSK File"
DSK_MAGIC_STD = b"MV - CPC"
DISK_INFO_LEN = 256
TRACK_INFO_LEN = 256
TRACK_INFO_MAGIC = b"Track-Info"


def parse_dsk(data: bytes) -> DiskImage:
    if len(data) < DISK_INFO_LEN:
        raise ValueError("DSK file too short")
    extended = data.startswith(DSK_MAGIC_EXT)
    if not extended and not data.startswith(DSK_MAGIC_STD):
        raise ValueError("not a CPC DSK file")

    n_tracks = data[48]
    n_sides = data[49]
    n_track_blocks = n_tracks * n_sides

    if extended:
        sizes = list(data[52 : 52 + n_track_blocks])
        track_sizes = [s * 256 for s in sizes]
    else:
        std = struct.unpack_from("<H", data, 50)[0]
        track_sizes = [std] * n_track_blocks

    sectors: list[Sector] = []
    offset = DISK_INFO_LEN
    for size in track_sizes:
        if size == 0:
            continue
        if offset + size > len(data):
            # Truncated DSK — keep what we've already read and stop.
            break
        try:
            sectors.extend(_parse_track(data[offset : offset + size], extended))
        except ValueError:
            # Track has a malformed Track-Info marker etc. Skip this track.
            pass
        offset += size

    return DiskImage(tracks=n_tracks, sides=n_sides, sectors=sectors)


def _parse_track(track_data: bytes, extended: bool) -> list[Sector]:
    if not track_data.startswith(TRACK_INFO_MAGIC):
        raise ValueError("missing Track-Info marker")
    track = track_data[16]
    side = track_data[17]
    n_sectors = track_data[21]

    out: list[Sector] = []
    cursor = TRACK_INFO_LEN
    for s in range(n_sectors):
        info = track_data[24 + s * 8 : 24 + s * 8 + 8]
        if len(info) < 8:
            break  # truncated sector-info entry — stop parsing this track
        sector_id = info[2]
        size_code = info[3]
        if extended:
            actual_len = info[6] | (info[7] << 8)
            if actual_len == 0:
                actual_len = 128 << size_code
        else:
            actual_len = 128 << size_code
        # Copy-protected disks sometimes claim sentinel sectors with bogus
        # sizes (e.g. size_code=8 -> 32KB) past the real track data. Stop
        # at the real end rather than aborting.
        if cursor + actual_len > len(track_data):
            break
        out.append(
            Sector(
                track=track,
                side=side,
                sector_id=sector_id,
                data=bytes(track_data[cursor : cursor + actual_len]),
            )
        )
        cursor += actual_len
    return out


def iter_dsk_events(dsk_data: bytes) -> Iterator[LoadEvent]:
    """Yield a LoadEvent per file across all plausible CP/M layouts.

    Tries the track-major interleaved view AND each per-side view (handles
    CPC "Data"-format DS disks). Each file's +3DOS header gives the load
    address; raw 6912-byte chunks (no header) are emitted at $4000.
    """
    img = parse_dsk(dsk_data)

    layouts: list[bytes] = [img.flat()]
    if img.sides > 1:
        for side in range(img.sides):
            layouts.append(img.flat_side(side))

    yielded: set[bytes] = set()  # dedup body across layouts
    for flat in layouts:
        for ev in _events_from_layout(flat, img):
            key = ev.body
            if key in yielded:
                continue
            yielded.add(key)
            yield ev


def _events_from_layout(flat: bytes, img: DiskImage) -> Iterator[LoadEvent]:
    sectors_per_track, sector_bytes = _detect_geometry(img)
    reserved = detect_reserved_offset(
        flat,
        sectors_per_track=sectors_per_track,
        sides=img.sides,
        sector_bytes=sector_bytes,
    )
    if directory_looks_valid(flat, reserved_bytes=reserved):
        for cpm_file in list_files(flat, reserved_bytes=reserved):
            yield _event_from_cpm_file(cpm_file)
    else:
        # No CP/M file system here — fall back to magic-scanning for raw
        # +3DOS files at sector boundaries (no filename info available).
        for f in find_plus3_files(flat):
            yield _event_from_plus3_file(f)


def _event_from_plus3_file(file, name: str = "") -> LoadEvent:
    kind = KIND_CODE if file.type == TYPE_CODE else KIND_BASIC if file.type == TYPE_PROGRAM else KIND_RAW
    addr = file.param1 if file.type == TYPE_CODE else None
    return LoadEvent(body=file.body, addr=addr, name=name, kind=kind)


def _event_from_cpm_file(cpm_file) -> LoadEvent:
    """Decode a CP/M file body into a LoadEvent.

    Files saved by 48-BASIC carry a 128-byte +3DOS header that gives the
    load address; the actual content follows. Files written without that
    header (raw binaries) are emitted as raw with no address.
    """
    p3 = parse_at(cpm_file.body, 0)
    name = f"{cpm_file.name}.{cpm_file.ext}".rstrip(".")
    if p3 is not None:
        return _event_from_plus3_file(p3, name)
    # No +3DOS header — raw bytes. Don't speculate about an address.
    return LoadEvent(body=cpm_file.body, addr=None, name=name, kind=KIND_RAW)


def extract_screens(dsk_data: bytes) -> list[bytes]:
    return _extract_screens_from_events(iter_dsk_events(dsk_data))


def _detect_geometry(img: DiskImage) -> tuple[int, int]:
    """Return (sectors_per_track, sector_bytes) inferred from track 1 side 0
    (or whatever's available)."""
    if not img.sectors:
        return 9, 512
    # Pick the first track-side that's actually populated.
    by_track_side: dict[tuple[int, int], list[Sector]] = {}
    for s in img.sectors:
        by_track_side.setdefault((s.track, s.side), []).append(s)
    sample = by_track_side[next(iter(by_track_side))]
    sector_bytes = max(len(s.data) for s in sample)
    return len(sample), sector_bytes


def extract_screen(dsk_data: bytes) -> bytes:
    """Return the first SCREEN$ file (preserved for single-frame use)."""
    screens = extract_screens(dsk_data)
    if not screens:
        raise ValueError("no SCREEN$ file found in DSK image")
    return screens[0]


# --- Pillow plugin --------------------------------------------------------


class ZXDSKImageFile(ScreenSequenceImageFile):
    format = "ZXDSK"
    format_description = "ZX Spectrum +3 / Amstrad CPC disk image"

    def _open(self):
        head = self.fp.read(len(DSK_MAGIC_EXT))
        self.fp.seek(0)
        if not (head.startswith(DSK_MAGIC_STD) or head.startswith(DSK_MAGIC_EXT)):
            raise SyntaxError("not a CPC DSK file")
        data = self.fp.read()
        try:
            screens = extract_screens(data)
        except ValueError as e:
            raise SyntaxError(f"failed to parse DSK: {e}") from e
        self._set_frames(screens)


def _accept(prefix: bytes) -> bool:
    return prefix.startswith(DSK_MAGIC_STD) or prefix.startswith(DSK_MAGIC_EXT[:8])


def register() -> None:
    Image.register_open(ZXDSKImageFile.format, ZXDSKImageFile, _accept)
    Image.register_extension(ZXDSKImageFile.format, ".dsk")
