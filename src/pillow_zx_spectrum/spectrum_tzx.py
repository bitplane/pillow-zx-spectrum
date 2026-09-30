"""Pillow plugin for ZX Spectrum TZX tape files.

Walks the TZX block stream, extracts standard ROM-loader blocks (IDs 0x10
and 0x11), translates header/data pairs to `LoadEvent`s, and feeds them
into the unified screen extractor (see loader.py).

Skips meta blocks (text, archive info, hardware type, glue, group/loop
markers, pause). Raises NotImplementedError on audio-only blocks (pure
tone, pulse sequence, pure data, direct recording, CSW, generalized) —
those need an actual loader to interpret and aren't supported.
"""

import struct
from typing import Iterator

from PIL import Image

from .blocks import Block, Header, parse_block
from .loader import KIND_BASIC, KIND_CODE, LoadEvent
from .loader import extract_screens as _extract_screens_from_events
from .pillow_screen import ScreenSequenceImageFile

TZX_MAGIC = b"ZXTape!\x1a"


def iter_tzx_blocks(data: bytes) -> Iterator[Block]:
    """Yield each standard ROM-loader Block in a TZX file (skipping meta)."""
    if not data.startswith(TZX_MAGIC):
        raise ValueError("not a TZX file")
    if len(data) < len(TZX_MAGIC) + 2:
        raise ValueError("TZX too short for version header")
    try:
        yield from _walk_tzx_blocks(data)
    except (IndexError, struct.error) as e:
        raise ValueError("truncated TZX block") from e


def _walk_tzx_blocks(data: bytes) -> Iterator[Block]:
    i = len(TZX_MAGIC) + 2  # magic + version major.minor

    while i < len(data):
        bid = data[i]
        i += 1
        if bid == 0x10:  # standard speed data
            _, length = struct.unpack_from("<HH", data, i)
            i += 4
            if i + length > len(data):
                raise ValueError("truncated TZX standard data block")
            try:
                yield parse_block(data[i : i + length])
            except ValueError:
                pass  # malformed block — skip rather than abort the parse
            i += length
        elif bid == 0x11:  # turbo speed data — same shape, different timing
            i += 0x0F  # skip 15 bytes of timing
            length = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
            i += 3
            if i + length > len(data):
                raise ValueError("truncated TZX turbo data block")
            try:
                yield parse_block(data[i : i + length])
            except ValueError:
                pass
            i += length
        elif bid == 0x12:  # pure tone: 4 bytes (pulse length + count)
            i += 4
        elif bid == 0x13:  # pulse sequence: 1-byte count + count*2 bytes
            i += 1 + data[i] * 2
        elif bid == 0x14:  # pure data: 7-byte timing + 3-byte length + data
            # The data is a custom-loader payload (SpeedLock / BleepLoad /
            # similar). Without per-protection-scheme decoders we can't
            # recover a standard ROM block from it; skip.
            i += 7
            length = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
            i += 3 + length
        elif bid == 0x15:  # direct recording: 5-byte timing + 3-byte length + data
            i += 5
            length = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
            i += 3 + length
        elif bid == 0x18:  # CSW recording: 4-byte length + data
            length = struct.unpack_from("<I", data, i)[0]
            i += 4 + length
        elif bid == 0x19:  # generalized data: 4-byte length + data
            length = struct.unpack_from("<I", data, i)[0]
            i += 4 + length
        elif bid == 0x20:  # pause / stop
            i += 2
        elif bid == 0x21:  # group start: 1-byte length + name
            i += 1 + data[i]
        elif bid == 0x22:  # group end
            pass
        elif bid == 0x23:  # jump to relative
            i += 2
        elif bid == 0x24:  # loop start: 2-byte repetitions
            i += 2
        elif bid == 0x25:  # loop end
            pass
        elif bid == 0x26:  # call sequence: 2-byte count + count*2 bytes
            count = struct.unpack_from("<H", data, i)[0]
            i += 2 + count * 2
        elif bid == 0x27:  # return from sequence
            pass
        elif bid == 0x28:  # select block: 2-byte length + data
            length = struct.unpack_from("<H", data, i)[0]
            i += 2 + length
        elif bid == 0x2A:  # stop tape if 48K
            i += 4
        elif bid == 0x2B:  # set signal level: 4-byte length + 1 byte
            length = struct.unpack_from("<I", data, i)[0]
            i += 4 + length
        elif bid == 0x30:  # text description: 1-byte length + text
            i += 1 + data[i]
        elif bid == 0x31:  # message: 1-byte time + 1-byte length + text
            i += 2 + data[i + 1]
        elif bid == 0x32:  # archive info: 2-byte length
            length = struct.unpack_from("<H", data, i)[0]
            i += 2 + length
        elif bid == 0x33:  # hardware type: 1-byte count + count*3 bytes
            i += 1 + data[i] * 3
        elif bid == 0x34:  # emulation info
            i += 8
        elif bid == 0x35:  # custom info: 16-byte id + 4-byte length
            i += 16
            length = struct.unpack_from("<I", data, i)[0]
            i += 4 + length
        elif bid == 0x40:  # snapshot block: 1 byte + 3-byte length + data
            i += 1
            length = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
            i += 3 + length
        elif bid == 0x5A:  # glue
            i += 9
        else:
            raise NotImplementedError(f"TZX block id 0x{bid:02x} at offset {i - 1} not supported")
        if i > len(data):
            raise ValueError("truncated TZX block")


def iter_tzx_events(tzx_data: bytes) -> Iterator[LoadEvent]:
    """Yield a LoadEvent per `header + data` pair in the TZX."""
    pending: Header | None = None
    for block in iter_tzx_blocks(tzx_data):
        if block.is_header():
            pending = Header.from_block(block)
            continue
        if block.is_data() and pending is not None:
            kind = KIND_CODE if pending.type == 3 else KIND_BASIC
            yield LoadEvent(
                body=block.payload[: pending.length],
                addr=pending.param1 if pending.type == 3 else None,
                name=pending.name,
                kind=kind,
            )
        pending = None


def extract_screens(tzx_data: bytes) -> list[bytes]:
    return _extract_screens_from_events(iter_tzx_events(tzx_data))


def extract_screen(tzx_data: bytes) -> bytes:
    """Return the first SCREEN$ candidate (preserved for single-frame use)."""
    screens = extract_screens(tzx_data)
    if not screens:
        raise ValueError("no screen found in TZX")
    return screens[0]


# --- Pillow plugin --------------------------------------------------------


class ZXTZXImageFile(ScreenSequenceImageFile):
    format = "ZXTZX"
    format_description = "ZX Spectrum TZX tape"

    def _open(self):
        magic = self.fp.read(len(TZX_MAGIC))
        if magic != TZX_MAGIC:
            raise SyntaxError(f"not a TZX file (magic={magic!r})")
        self.fp.seek(0)
        data = self.fp.read()
        try:
            screens = extract_screens(data)
        except (ValueError, NotImplementedError) as e:
            raise SyntaxError(f"failed to parse TZX: {e}") from e
        self._set_frames(screens)


def _accept(prefix: bytes) -> bool:
    return prefix.startswith(TZX_MAGIC)


def register() -> None:
    Image.register_open(ZXTZXImageFile.format, ZXTZXImageFile, _accept)
    Image.register_extension(ZXTZXImageFile.format, ".tzx")
