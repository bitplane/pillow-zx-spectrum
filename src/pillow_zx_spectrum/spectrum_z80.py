"""Pillow plugin for ZX Spectrum .z80 snapshots (Gerton Lunter format).

Three versions exist:
- v1: 30-byte header, then 48K of RAM (compressed or raw, depending on
  bit 5 of byte 12). RAM order is $4000-$7FFF, $8000-$BFFF, $C000-$FFFF.
  v1 is always 48K.
- v2: 30-byte header (PC=0) + 2-byte extra-header length (=23) + 23-byte
  extra header, then page blocks.
- v3: same as v2 but extra-header length is 54 or 55.

Page blocks (v2/v3) are 3-byte sub-header (2-byte compressed length, 1-byte
page number) + data. Length 0xFFFF means the data is uncompressed 16384 bytes.

Compression is `ED ED nn xx` -> byte xx repeated nn times. Used only for
runs of >= 5 identical bytes (or runs of 2+ ED bytes). A single ED followed
by anything-not-ED is two literal bytes. v1 terminates the compressed stream
with `00 ED ED 00` (a count-of-0 run, which never appears in valid encoded
data).

Page-to-address mapping:
- 48K snapshots use pages 4, 5, 8 -> $8000, $C000, $4000.
- 128K snapshots use pages 3..10 -> RAM banks 0..7. Bank 5 (= page 8) is
  always at $4000 (the screen). Bank 2 (= page 5) is always at $8000. The
  bank at $C000 is selected by port 0x7FFD bits 0..2 (in the extra header).

References: https://worldofspectrum.org/faq/reference/z80format.htm
"""

import struct

from PIL import Image

from .pillow_screen import ScreenSequenceImageFile
from .snapshot import RAM_SIZE, MachineType, Snapshot

V1_RAM_BYTES = 49152
PAGE_BYTES = 16384
HEADER_LEN = 30
COMPRESSED_FLAG = 0x20
TERMINATOR_FLAG = 0xFF  # byte 12 == 0xFF must be treated as 0x01

# A v1 .z80 file is at most 30 (header) + 49152 (raw RAM) + 4 (compressed
# terminator) bytes. Anything larger cannot be a v1 snapshot.
V1_MAX_FILE_SIZE = HEADER_LEN + V1_RAM_BYTES + 4


def decompress_z80(data: bytes, max_output: int | None = None) -> tuple[bytes, int]:
    """Decompress a Z80 RLE byte stream.

    Stops when `max_output` bytes have been emitted, or when input is
    exhausted, or on a count-of-0 sentinel (the v1 end-of-data marker).
    Returns (output_bytes, input_consumed).
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if max_output is not None and len(out) >= max_output:
            break
        b = data[i]
        if b == 0xED and i + 1 < n and data[i + 1] == 0xED:
            if i + 3 >= n:
                raise ValueError("truncated ED ED run-length sequence")
            count = data[i + 2]
            value = data[i + 3]
            i += 4
            if count == 0:
                # End-of-data sentinel (v1 terminator).
                break
            out.extend(bytes([value]) * count)
        else:
            out.append(b)
            i += 1
    return bytes(out), i


def _classify_machine(mode: int, version: int) -> MachineType:
    """Hardware mode byte at file offset 34 -> MachineType."""
    if version == 2:
        if mode in (0, 1):
            return MachineType.SPECTRUM_48K
        if mode == 2:
            return MachineType.SAMRAM
        if mode in (3, 4):
            return MachineType.SPECTRUM_128K
    else:  # v3
        if mode in (0, 1, 3):
            return MachineType.SPECTRUM_48K
        if mode == 2:
            return MachineType.SAMRAM
        if mode in (4, 5, 6):
            return MachineType.SPECTRUM_128K
    if mode == 7:
        return MachineType.SPECTRUM_PLUS3
    if mode == 9:
        return MachineType.PENTAGON
    return MachineType.UNKNOWN


def parse_z80(data: bytes) -> Snapshot:
    """Parse a .z80 snapshot file into a `Snapshot`."""
    if len(data) < HEADER_LEN:
        raise ValueError(f"z80 file too short ({len(data)} bytes, need >= {HEADER_LEN})")

    pc = data[6] | (data[7] << 8)
    flags1 = data[12]
    if flags1 == TERMINATOR_FLAG:
        flags1 = 0x01
    border = (flags1 >> 1) & 0x07
    v1_compressed = bool(flags1 & COMPRESSED_FLAG)

    if pc != 0:
        return _parse_v1(data, border, v1_compressed)

    if len(data) < HEADER_LEN + 2:
        raise ValueError("z80 v2/v3 missing extra-header length")
    extra_len = struct.unpack_from("<H", data, HEADER_LEN)[0]
    if extra_len not in (23, 54, 55):
        raise ValueError(f"unsupported z80 extra-header length {extra_len}")
    version = 2 if extra_len == 23 else 3

    body_start = HEADER_LEN + 2 + extra_len
    if len(data) < body_start:
        raise ValueError("z80 file ends inside extra header")

    hardware_mode = data[34]
    machine = _classify_machine(hardware_mode, version)
    is_128k_layout = machine in (
        MachineType.SPECTRUM_128K,
        MachineType.SPECTRUM_PLUS3,
        MachineType.PENTAGON,
    )
    port_7ffd = data[35] if is_128k_layout else 0

    snap = Snapshot(machine=machine, border=border)
    _walk_pages(data[body_start:], snap, is_128k_layout, port_7ffd)
    return snap


def _parse_v1(data: bytes, border: int, compressed: bool) -> Snapshot:
    body = data[HEADER_LEN:]
    if compressed:
        ram, _ = decompress_z80(body, max_output=V1_RAM_BYTES)
    else:
        ram = body[:V1_RAM_BYTES]
    if len(ram) < V1_RAM_BYTES:
        raise ValueError(f"v1 RAM truncated: {len(ram)} bytes (need {V1_RAM_BYTES})")
    snap = Snapshot(machine=MachineType.SPECTRUM_48K, border=border)
    snap.ram[0x4000:RAM_SIZE] = ram
    return snap


def _walk_pages(body: bytes, snap: Snapshot, is_128k: bool, port_7ffd: int) -> None:
    i = 0
    n = len(body)
    while i < n:
        if i + 3 > n:
            raise ValueError("truncated z80 page sub-header")
        length = body[i] | (body[i + 1] << 8)
        page = body[i + 2]
        i += 3
        if length == 0xFFFF:
            if i + PAGE_BYTES > n:
                raise ValueError(f"page {page}: uncompressed data truncated")
            page_data = bytes(body[i : i + PAGE_BYTES])
            i += PAGE_BYTES
        else:
            if i + length > n:
                raise ValueError(f"page {page}: compressed data truncated")
            page_data, _ = decompress_z80(body[i : i + length], max_output=PAGE_BYTES)
            i += length
        if len(page_data) != PAGE_BYTES:
            raise ValueError(f"page {page}: decompressed to {len(page_data)} bytes (expected {PAGE_BYTES})")
        _place_page(snap, page, page_data, is_128k, port_7ffd)


_PAGE_TO_ADDR_48K = {4: 0x8000, 5: 0xC000, 8: 0x4000}


def _place_page(snap: Snapshot, page: int, page_data: bytes, is_128k: bool, port_7ffd: int) -> None:
    if is_128k:
        bank = page - 3
        if not 0 <= bank <= 7:
            return  # ROM pages (0, 1, 2) — ignored
        snap.banks[bank] = page_data
        if bank == 5:
            snap.ram[0x4000:0x8000] = page_data
        elif bank == 2:
            snap.ram[0x8000:0xC000] = page_data
        if bank == port_7ffd & 0x07:
            snap.ram[0xC000:RAM_SIZE] = page_data
    else:
        addr = _PAGE_TO_ADDR_48K.get(page)
        if addr is not None:
            snap.ram[addr : addr + PAGE_BYTES] = page_data


# --- Pillow plugin --------------------------------------------------------


def iter_z80_events(data: bytes):
    """Snapshot RAM is already populated; emit one event per relevant bank.

    Bank 5 (always at $4000) and bank 7 (the 128K shadow screen) are
    surfaced as separate writes to $4000. Empty banks (all zeros) are
    skipped — they're the synthetic "no screen captured" state, not an
    explicit load by the user.
    """
    from .loader import KIND_SNAPSHOT, LoadEvent

    snap = parse_z80(data)
    main = snap.screen()
    if any(main):
        yield LoadEvent(body=main, addr=0x4000, name="bank5", kind=KIND_SNAPSHOT)
    shadow = snap.shadow_screen()
    if shadow is not None and any(shadow):
        yield LoadEvent(body=shadow, addr=0x4000, name="shadow", kind=KIND_SNAPSHOT)


def extract_screens(data: bytes) -> list[bytes]:
    from .loader import extract_screens as _ext

    return _ext(iter_z80_events(data))


class ZXZ80ImageFile(ScreenSequenceImageFile):
    format = "ZXZ80"
    format_description = "ZX Spectrum Z80 snapshot"

    def _open(self):
        self.fp.seek(0, 2)
        size = self.fp.tell()
        self.fp.seek(0)
        if size < HEADER_LEN:
            raise SyntaxError("z80 file too short")
        head = self.fp.read(HEADER_LEN + 2 if size >= HEADER_LEN + 2 else HEADER_LEN)
        self.fp.seek(0)
        if not _validate_header(head, size):
            raise SyntaxError("not a z80 snapshot")
        _validate_v1_body(self.fp, head, size)
        data = self.fp.read()
        try:
            screens = extract_screens(data)
        except ValueError as e:
            raise SyntaxError(f"failed to parse z80: {e}") from e
        self._set_frames(screens)


V1_TERMINATOR = b"\x00\xed\xed\x00"


def _validate_v1_body(fp, head: bytes, size: int) -> None:
    """For v1 candidates, confirm the body matches the format (raw 49152 or
    compressed ending with `00 ED ED 00`). Raises SyntaxError otherwise."""
    pc = head[6] | (head[7] << 8)
    if pc == 0:
        return  # v2/v3, validated via extra-header length already
    flags1 = head[12] if head[12] != TERMINATOR_FLAG else 0x01
    compressed = bool(flags1 & COMPRESSED_FLAG)
    if compressed:
        if size < HEADER_LEN + len(V1_TERMINATOR):
            raise SyntaxError("z80 v1 compressed file is too short")
        fp.seek(-len(V1_TERMINATOR), 2)
        tail = fp.read(len(V1_TERMINATOR))
        fp.seek(0)
        if tail != V1_TERMINATOR:
            raise SyntaxError("z80 v1 compressed body missing terminator")
    else:
        if size != HEADER_LEN + V1_RAM_BYTES:
            raise SyntaxError(f"z80 v1 raw size {size} != expected {HEADER_LEN + V1_RAM_BYTES}")


def _validate_header(head: bytes, file_size: int) -> bool:
    """Check enough of the header to distinguish z80 from random data.

    The first 30 bytes are CPU registers with no fixed values, so for v1
    snapshots (PC != 0) we additionally require the file size to be in the
    valid v1 range (no v1 file can exceed header + 49152 + 4 bytes). For
    v2/v3 (PC == 0), the extra-header length at offset 30-31 must be 23,
    54, or 55.
    """
    if len(head) < HEADER_LEN:
        return False
    pc = head[6] | (head[7] << 8)
    if pc != 0:
        return file_size <= V1_MAX_FILE_SIZE
    if len(head) < HEADER_LEN + 2:
        return False
    extra_len = head[HEADER_LEN] | (head[HEADER_LEN + 1] << 8)
    return extra_len in (23, 54, 55)


def _accept(prefix: bytes) -> bool:
    # Pillow only passes the first 16 bytes — not enough to fingerprint the
    # extra-header length. Be permissive here; _open does the real check.
    return len(prefix) >= 16


def register() -> None:
    Image.register_open(ZXZ80ImageFile.format, ZXZ80ImageFile, _accept)
    Image.register_extension(ZXZ80ImageFile.format, ".z80")


__all__ = [
    "ZXZ80ImageFile",
    "decompress_z80",
    "extract_screens",
    "parse_z80",
    "register",
]
