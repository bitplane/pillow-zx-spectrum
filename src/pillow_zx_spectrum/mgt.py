"""MGT file system parsing (DISCiPLE / +D and SAM Coupé disks).

Geometry: 80 tracks per side, 1 or 2 sides, 10 sectors per track, 512
bytes per sector. The .mgt file is **side-interleaved**: file index N
holds physical track (N >> 1) of side (N & 1). In directory entries
the side bit lives at bit 7 of the track byte (so side 0 = tracks
0..79, side 1 = tracks 128..207).

Directory: occupies the first four tracks of side 0 (= 80 entries × 256
bytes = 20480 bytes).

Directory entry (256 bytes):
    0       file type (low 5 bits) + flags (bit 6 protected, bit 7 hidden)
    1..10   filename (10 bytes, space-padded)
    11..12  sector count (BIG-endian)
    13..14  first sector (track, sector)  — sector numbering 1..10
    15..209 sector address map / chain notes
    211     +3DOS-style "tape header type" (0=BASIC, 1=NumArr, 2=StrArr, 3=CODE)
    212..213 file body length (LE)
    214..215 start address (LE)  — load address for CODE; autostart for BASIC
    216..217 type-specific
    218..219 autostart line / address
    220..241 snapshot register dump (snapshot file types only)

File body: each sector's last 2 bytes are the (track, sector) of the
NEXT sector in the file — so only the first 510 bytes of every sector
are file data. Follow the chain for `sector_count` sectors then trim
the result to the declared length.

File types we care about:
    1   ZX BASIC
    4   ZX CODE
    7   ZX SCREEN$ (always 6912 bytes loaded at $4000)
    5   ZX 48K snapshot
    9   ZX 128K snapshot
"""

from dataclasses import dataclass
from typing import Iterator

SECTOR_BYTES = 512
DATA_PER_SECTOR = 510  # last 2 bytes are the chain pointer
SECTORS_PER_TRACK = 10
TRACK_BYTES = SECTORS_PER_TRACK * SECTOR_BYTES  # 5120
TRACKS_PER_SIDE = 80
DIR_TRACKS = 4
DIR_ENTRIES = DIR_TRACKS * SECTORS_PER_TRACK * (SECTOR_BYTES // 256)  # 80
DIR_ENTRY_LEN = 256

SS_SIZE = TRACKS_PER_SIDE * TRACK_BYTES  # 409600
DS_SIZE = SS_SIZE * 2  # 819200

TYPE_BASIC = 1
TYPE_NUMBER_ARRAY = 2
TYPE_CHAR_ARRAY = 3
TYPE_CODE = 4
TYPE_SNAPSHOT_48K = 5
TYPE_SCREEN = 7
TYPE_SNAPSHOT_128K = 9


@dataclass(frozen=True)
class MgtFile:
    name: str  # 10-char ASCII, trailing spaces stripped
    type: int  # MGT file type (TYPE_*)
    spectrum_type: int  # +3DOS-style tape header type (0..3)
    length: int  # declared body length
    start_addr: int  # CODE: load address; BASIC: autostart line
    body: bytes  # reconstructed file body


def _sector_offset(track: int, sector: int) -> int:
    """Convert (track, sector) reference to a byte offset in the .mgt layout.

    Side-interleaved: file slot N = physical track (N >> 1) of side
    (N & 1). In directory entries the high bit of `track` selects the
    side (0 = side 0, 0x80 = side 1), low 7 bits give the physical
    track. Sector numbering is 1..10.
    """
    side = (track >> 7) & 1
    phys = track & 0x7F
    file_track_index = phys * 2 + side
    return file_track_index * TRACK_BYTES + (sector - 1) * SECTOR_BYTES


def _directory_entries(data: bytes) -> Iterator[bytes]:
    """Read complete directory entries from tracks 0..3 of side zero."""
    for track in range(DIR_TRACKS):
        start = _sector_offset(track, 1)
        for offset in range(start, start + TRACK_BYTES, DIR_ENTRY_LEN):
            entry = data[offset : offset + DIR_ENTRY_LEN]
            if len(entry) != DIR_ENTRY_LEN:
                return
            yield entry


def parse_mgt_files(data: bytes) -> Iterator[MgtFile]:
    """Yield each non-empty file in the directory, with body reassembled
    by following the per-sector chain pointers."""
    if len(data) < DIR_ENTRIES * DIR_ENTRY_LEN:
        return  # too short even for an empty directory

    for e in _directory_entries(data):
        type_byte = e[0] & 0x1F
        if type_byte == 0:
            continue  # empty / deleted slot
        name = e[1:11].decode("ascii", errors="replace").rstrip()
        sector_count = (e[11] << 8) | e[12]  # big-endian
        first_track = e[13]
        first_sector = e[14]
        spectrum_type = e[211]
        length = e[212] | (e[213] << 8)
        start_addr = e[214] | (e[215] << 8)

        raw = _read_chain(data, first_track, first_sector, sector_count)

        # Types 1..4 and 7 carry a 9-byte +3DOS-style header at the start
        # of the body (type byte, length, addr, params). Strip it so the
        # caller gets the actual file payload.
        if type_byte in (TYPE_BASIC, TYPE_NUMBER_ARRAY, TYPE_CHAR_ARRAY, TYPE_CODE, TYPE_SCREEN) and len(raw) >= 9:
            payload = raw[9:]
        else:
            payload = raw

        # Trim to declared length when we have one.
        if length:
            payload = payload[:length]

        yield MgtFile(
            name=name,
            type=type_byte,
            spectrum_type=spectrum_type,
            length=length,
            start_addr=start_addr,
            body=bytes(payload),
        )


def _read_chain(data: bytes, track: int, sector: int, max_sectors: int) -> bytearray:
    body = bytearray()
    for _ in range(max_sectors):
        if track == 0 and sector == 0:
            break
        offset = _sector_offset(track, sector)
        if offset < 0 or offset + SECTOR_BYTES > len(data):
            break
        sector_data = data[offset : offset + SECTOR_BYTES]
        body.extend(sector_data[:DATA_PER_SECTOR])
        track = sector_data[DATA_PER_SECTOR]
        sector = sector_data[DATA_PER_SECTOR + 1]
    return body


def directory_looks_valid(data: bytes) -> bool:
    """Quick fingerprint: the first non-empty directory entry should have
    a known file type and a printable name."""
    if len(data) < DIR_ENTRY_LEN:
        return False
    # Find the first non-empty entry; require it to look like a real file.
    for e in _directory_entries(data):
        type_byte = e[0] & 0x1F
        if type_byte == 0:
            continue
        if type_byte > 25:
            return False  # outside known MGT type range
        name = bytes(b & 0x7F for b in e[1:11])
        if not all(0x20 <= b < 0x7F or b == 0 for b in name):
            return False
        return True
    return False
