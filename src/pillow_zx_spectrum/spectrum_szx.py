"""Pillow plugin for ZX Spectrum SZX (ZX-State / Spectaculator) snapshots.

A modern, chunked snapshot format that supersedes Z80. Layout:

    "ZXST"                4 bytes magic
    chMajorVersion (1) chMinorVersion (1) chMachineId (1) chFlags (1)
    chunks*               each = 4-byte ID + 4-byte size (LE) + size bytes

Block IDs we care about:
    "RAMP"  RAM page: wFlags(2 LE) + chPageNo(1) + chData(...)
            wFlags bit 0 = ZXSTRF_COMPRESSED -> chData is zlib stream that
            decompresses to 16384 bytes; otherwise chData is raw 16384.

For screen extraction: page 5 contains $4000-$7FFF on every machine type
that has a screen (the screen is always in bank 5).

Reference: https://www.spectaculator.com/docs/zx-state/
"""

import struct
import zlib
from typing import Iterator

from PIL import Image

from .pillow_screen import ScreenSequenceImageFile
from .snapshot import RAM_SIZE, MachineType, Snapshot

SZX_MAGIC = b"ZXST"
HEADER_LEN = 8

CHUNK_HEADER_LEN = 8
PAGE_BYTES = 16384

ZXSTRF_COMPRESSED = 0x0001

MACHINE_BY_ID = {
    0: MachineType.SPECTRUM_48K,  # 16K
    1: MachineType.SPECTRUM_48K,
    2: MachineType.SPECTRUM_128K,
    3: MachineType.SPECTRUM_PLUS3,
    4: MachineType.SPECTRUM_PLUS3,
    5: MachineType.PENTAGON,
    6: MachineType.SPECTRUM_PLUS3,
    9: MachineType.PENTAGON,
}


def iter_chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    if not data.startswith(SZX_MAGIC):
        raise ValueError("not an SZX file")
    if len(data) < HEADER_LEN:
        raise ValueError("SZX too short for header")
    i = HEADER_LEN
    while i + CHUNK_HEADER_LEN <= len(data):
        chunk_id = bytes(data[i : i + 4])
        size = struct.unpack_from("<I", data, i + 4)[0]
        i += CHUNK_HEADER_LEN
        if i + size > len(data):
            raise ValueError(f"chunk {chunk_id!r} runs past EOF")
        yield chunk_id, bytes(data[i : i + size])
        i += size


def _decode_ramp(chunk_data: bytes) -> tuple[int, bytes]:
    if len(chunk_data) < 3:
        raise ValueError("RAMP chunk too short")
    flags = struct.unpack_from("<H", chunk_data, 0)[0]
    page = chunk_data[2]
    body = chunk_data[3:]
    if flags & ZXSTRF_COMPRESSED:
        decompressor = zlib.decompressobj()
        body = decompressor.decompress(body, PAGE_BYTES + 1)
        if not decompressor.eof:
            raise ValueError(f"RAMP page {page} exceeds {PAGE_BYTES} bytes or has an incomplete zlib stream")
    if len(body) != PAGE_BYTES:
        raise ValueError(f"RAMP page {page} decompressed to {len(body)} bytes (expected {PAGE_BYTES})")
    return page, body


def parse_szx(data: bytes) -> Snapshot:
    if not data.startswith(SZX_MAGIC):
        raise ValueError("not an SZX file")
    machine_id = data[6]
    snap = Snapshot(machine=MACHINE_BY_ID.get(machine_id, MachineType.UNKNOWN))
    is_128k = snap.machine in (
        MachineType.SPECTRUM_128K,
        MachineType.SPECTRUM_PLUS3,
        MachineType.PENTAGON,
    )
    port_7ffd = 0
    pending_pages: dict[int, bytes] = {}

    for chunk_id, body in iter_chunks(data):
        if chunk_id == b"RAMP":
            page, page_data = _decode_ramp(body)
            pending_pages[page] = page_data
        elif chunk_id == b"SPCR":
            # Special chunk: chBorder(1) + ch7ffd(1) + ch1ffd(1) + chFe(1) + ...
            if len(body) >= 2:
                snap.border = body[0] & 0x07
                port_7ffd = body[1]

    for page, page_data in pending_pages.items():
        if is_128k:
            snap.banks[page] = page_data
            if page == 5:
                snap.ram[0x4000:0x8000] = page_data
            elif page == 2:
                snap.ram[0x8000:0xC000] = page_data
            elif page == port_7ffd & 0x07:
                snap.ram[0xC000:RAM_SIZE] = page_data
        else:
            # 48K mode: pages 5, 2, 0 -> $4000, $8000, $C000
            addr = {5: 0x4000, 2: 0x8000, 0: 0xC000}.get(page)
            if addr is not None:
                snap.ram[addr : addr + PAGE_BYTES] = page_data
    return snap


def iter_szx_events(data: bytes):
    from .loader import KIND_SNAPSHOT, LoadEvent

    snap = parse_szx(data)
    main = snap.screen()
    if any(main):
        yield LoadEvent(body=main, addr=0x4000, name="bank5", kind=KIND_SNAPSHOT)
    shadow = snap.shadow_screen()
    if shadow is not None and any(shadow):
        yield LoadEvent(body=shadow, addr=0x4000, name="shadow", kind=KIND_SNAPSHOT)


def extract_screens(data: bytes) -> list[bytes]:
    from .loader import extract_screens as _ext

    return _ext(iter_szx_events(data))


def extract_screen(data: bytes) -> bytes:
    return parse_szx(data).screen()


# --- Pillow plugin --------------------------------------------------------


class ZXSZXImageFile(ScreenSequenceImageFile):
    format = "ZXSZX"
    format_description = "ZX Spectrum SZX (ZX-State) snapshot"

    def _open(self):
        head = self.fp.read(len(SZX_MAGIC))
        self.fp.seek(0)
        if head != SZX_MAGIC:
            raise SyntaxError("not an SZX file")
        data = self.fp.read()
        try:
            screens = extract_screens(data)
        except (ValueError, zlib.error) as e:
            raise SyntaxError(f"failed to parse SZX: {e}") from e
        self._set_frames(screens)


def _accept(prefix: bytes) -> bool:
    return prefix.startswith(SZX_MAGIC)


def register() -> None:
    Image.register_open(ZXSZXImageFile.format, ZXSZXImageFile, _accept)
    Image.register_extension(ZXSZXImageFile.format, ".szx")
