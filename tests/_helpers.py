"""Shared helpers for synthetic SCREEN$ and tape test data."""

import struct


def make_screen(pixel_byte: int = 0x00, attr_byte: int = 0x07) -> bytes:
    """Synthetic SCREEN$ filled with a single pixel byte and attribute byte.

    Default: pixels=0, attribute=white-ink-on-black-paper -> all-black image.
    """
    return bytes([pixel_byte] * 6144) + bytes([attr_byte] * 768)


def with_pixel_byte(offset: int, value: int, attr_byte: int = 0x07) -> bytes:
    """SCREEN$ with all-zero pixels except `value` at `offset` in pixel data."""
    pixels = bytearray(6144)
    pixels[offset] = value
    return bytes(pixels) + bytes([attr_byte] * 768)


# --- Tape block construction --------------------------------------------------


def _checksum(flag: int, payload: bytes) -> int:
    c = flag
    for b in payload:
        c ^= b
    return c & 0xFF


def make_data_block(payload: bytes, flag: int = 0xFF) -> bytes:
    """Standard ROM-format block: flag + payload + xor checksum."""
    return bytes([flag]) + payload + bytes([_checksum(flag, payload)])


def make_header_block(
    type_: int,
    name: str,
    length: int,
    param1: int,
    param2: int = 0x8000,
) -> bytes:
    """17-byte header payload wrapped as a standard ROM-format block."""
    name_bytes = name.encode("ascii")[:10].ljust(10, b" ")
    payload = bytes([type_]) + name_bytes + struct.pack("<HHH", length, param1 & 0xFFFF, param2 & 0xFFFF)
    assert len(payload) == 17
    return make_data_block(payload, flag=0x00)


def make_tap(*standard_blocks: bytes) -> bytes:
    """Assemble a TAP file from a sequence of standard ROM blocks."""
    return b"".join(struct.pack("<H", len(b)) + b for b in standard_blocks)


def tzx_standard_block(block_bytes: bytes, pause_ms: int = 1000) -> bytes:
    """Wrap a standard ROM block as a TZX 0x10 (standard-speed) block."""
    return bytes([0x10]) + struct.pack("<HH", pause_ms, len(block_bytes)) + block_bytes


def make_tzx(*standard_blocks: bytes, version: tuple[int, int] = (1, 20)) -> bytes:
    """Assemble a TZX file from a sequence of standard ROM blocks."""
    out = b"ZXTape!\x1a" + bytes(version)
    for b in standard_blocks:
        out += tzx_standard_block(b)
    return out


# --- Z80 snapshot construction -----------------------------------------------


def make_z80_v1(
    ram48k: bytes,
    *,
    pc: int = 0x8000,
    border: int = 0,
    compressed_body: bytes | None = None,
) -> bytes:
    """Build a v1 .z80 snapshot. RAM is 49152 bytes for $4000-$FFFF.

    If `compressed_body` is given it's used as the post-header body verbatim
    (and bit 5 of byte 12 is set). Otherwise the RAM is written raw.
    """
    if len(ram48k) != 49152:
        raise ValueError("v1 RAM must be 49152 bytes")
    header = bytearray(30)
    header[6] = pc & 0xFF
    header[7] = (pc >> 8) & 0xFF
    flags1 = (border & 0x07) << 1
    if compressed_body is not None:
        flags1 |= 0x20
    header[12] = flags1
    body = compressed_body if compressed_body is not None else ram48k
    return bytes(header) + body


def make_extended_dsk(
    sectors_per_track: list[list[tuple[int, bytes]]],
    *,
    sides: int = 1,
) -> bytes:
    """Build an Extended CPC DSK image.

    `sectors_per_track[t]` is a list of (sector_id, data) for track t in
    the physical (on-disk) order. Sector data must all be the same length
    (the sector size code is derived from len(data[0])).
    """
    n_tracks = len(sectors_per_track)
    if n_tracks == 0:
        raise ValueError("need at least one track")

    # Disk-Info header (256 bytes)
    disk_info = bytearray(256)
    disk_info[0:23] = b"EXTENDED CPC DSK File\r\n"
    disk_info[23:34] = b"Disk-Info\r\n"
    disk_info[34:48] = b"pytest        "[:14]
    disk_info[48] = n_tracks
    disk_info[49] = sides

    # Track size table at offset 52
    track_blocks: list[bytes] = []
    for t, sectors in enumerate(sectors_per_track):
        if not sectors:
            disk_info[52 + t] = 0
            continue
        size_code = _size_code_for(len(sectors[0][1]))
        sector_size = 128 << size_code
        track_info = bytearray(256)
        track_info[0:12] = b"Track-Info\r\n"
        track_info[16] = t
        track_info[17] = 0  # side
        track_info[20] = size_code
        track_info[21] = len(sectors)
        track_info[22] = 0x4E  # GAP3
        track_info[23] = 0xE5  # filler
        for i, (sid, sdata) in enumerate(sectors):
            if len(sdata) != sector_size:
                raise ValueError("sector size mismatch within track")
            entry = bytes([t, 0, sid, size_code, 0, 0]) + struct.pack("<H", sector_size)
            track_info[24 + i * 8 : 32 + i * 8] = entry
        track_data = bytes(track_info) + b"".join(s for _, s in sectors)
        # Pad to 256-byte boundary so the track-size byte fits.
        if len(track_data) % 256:
            track_data += b"\x00" * (256 - len(track_data) % 256)
        disk_info[52 + t] = len(track_data) // 256
        track_blocks.append(track_data)

    return bytes(disk_info) + b"".join(track_blocks)


def make_scl(files: list[tuple[str, int, int, int, bytes]]) -> bytes:
    """Build an SCL container.

    `files` is a list of (name, type_byte, param1, param2, body). Each body
    must already be padded to a multiple of 256 bytes.
    """
    n = len(files)
    if n > 128:
        raise ValueError("too many files (max 128)")
    out = bytearray(b"SINCLAIR" + bytes([n]))
    for name, type_, p1, p2, body in files:
        if len(body) % 256 != 0:
            raise ValueError("SCL file body must be a multiple of 256 bytes")
        sectors = len(body) // 256
        name_bytes = name.encode("ascii")[:8].ljust(8, b" ")
        out += name_bytes + bytes([type_]) + struct.pack("<HH", p1 & 0xFFFF, p2 & 0xFFFF) + bytes([sectors])
    for _, _, _, _, body in files:
        out += body
    # 4-byte LE checksum (sum of all preceding bytes mod 2^32).
    out += struct.pack("<I", sum(out) & 0xFFFFFFFF)
    return bytes(out)


def make_trd(
    files: list[tuple[str, int, int, int, bytes]],
    *,
    total_size: int = 655360,
    label: str = "TEST",
) -> bytes:
    """Build a TR-DOS raw disk image with the given files allocated
    contiguously starting from track 1 sector 0."""
    n = len(files)
    if n > 128:
        raise ValueError("too many files (max 128)")

    # Build directory (8 sectors at start of track 0 = 2048 bytes).
    directory = bytearray(2048)
    cursor_track = 1
    cursor_sector = 0
    body_blocks: list[tuple[int, bytes]] = []
    for i, (name, type_, p1, p2, body) in enumerate(files):
        if len(body) % 256 != 0:
            raise ValueError("TRD file body must be a multiple of 256 bytes")
        sectors = len(body) // 256
        name_bytes = name.encode("ascii")[:8].ljust(8, b" ")
        entry = (
            name_bytes
            + bytes([type_])
            + struct.pack("<HH", p1 & 0xFFFF, p2 & 0xFFFF)
            + bytes([sectors, cursor_sector, cursor_track])
        )
        directory[i * 16 : (i + 1) * 16] = entry
        offset = cursor_track * 4096 + cursor_sector * 256
        body_blocks.append((offset, body))
        # Advance cursor.
        cursor_sector += sectors
        cursor_track += cursor_sector // 16
        cursor_sector %= 16

    # System sector at offset 0x800. Mostly zeros + magic byte 0x10 at offset 0xE7.
    sys_sector = bytearray(256)
    sys_sector[0xE1] = cursor_sector  # first free sector
    sys_sector[0xE2] = cursor_track  # first free track
    sys_sector[0xE3] = 0x16  # disk type = DS80
    sys_sector[0xE4] = n  # file count
    sys_sector[0xE7] = 0x10  # TR-DOS magic
    label_bytes = label.encode("ascii")[:8].ljust(8, b" ")
    sys_sector[0xF5 : 0xF5 + 8] = label_bytes  # disk label

    out = bytearray(total_size)
    out[0:2048] = directory
    out[0x800:0x900] = sys_sector
    for offset, body in body_blocks:
        out[offset : offset + len(body)] = body
    return bytes(out)


def make_sna_48k(ram48k: bytes, *, border: int = 0) -> bytes:
    """Build a minimal valid 48K SNA snapshot (49179 bytes total)."""
    if len(ram48k) != 49152:
        raise ValueError("48K SNA RAM must be 49152 bytes")
    header = bytearray(27)
    header[26] = border & 0x07
    return bytes(header) + ram48k


def make_szx(pages: dict[int, bytes], *, machine_id: int = 1) -> bytes:
    """Build a minimal SZX with the given RAMP pages (uncompressed)."""
    out = bytearray()
    out += b"ZXST" + bytes([1, 4, machine_id, 0])
    for page, page_data in pages.items():
        if len(page_data) != 16384:
            raise ValueError(f"page {page} must be 16384 bytes")
        body = struct.pack("<H", 0) + bytes([page]) + page_data  # flags=0 (uncompressed)
        out += b"RAMP" + struct.pack("<I", len(body)) + body
    return bytes(out)


def make_slt(snapshot_bytes: bytes, screens: list[bytes]) -> bytes:
    """Build an SLT file: a Z80 snapshot + 3 NULs + 'SLT' + table + data.

    Each screen is added as a type-3 entry, stored uncompressed (using a
    simple all-literal byte stream, no ED ED runs needed since we don't
    use the compression scheme — but the parser tolerates that).
    """
    out = bytearray(snapshot_bytes)
    out += b"\x00\x00\x00SLT"
    # Table: each entry = type(2) + id(2) + size(4)
    for i, s in enumerate(screens):
        out += struct.pack("<HHI", 3, i, len(s))
    out += struct.pack("<HHI", 0, 0, 0)  # END marker
    for s in screens:
        out += s
    return bytes(out)


def _size_code_for(sector_bytes: int) -> int:
    """sector size = 128 << code; standard +3 sector is 512 bytes (code 2)."""
    n = sector_bytes // 128
    code = 0
    while n > 1:
        n >>= 1
        code += 1
    if (128 << code) != sector_bytes:
        raise ValueError(f"sector size {sector_bytes} is not a power-of-two * 128")
    return code


def make_mgt(
    files: list[tuple[str, int, int, int, bytes]],
    *,
    sides: int = 2,
) -> bytes:
    """Build a side-interleaved MGT disk with the given files.

    `files` = list of (name, mgt_type, addr, length, data). Each file is
    given a 9-byte +3DOS-style header (type=3 CODE for type 4/7) prefix
    in the body. Files are placed contiguously in the data area starting
    at track 4 (after the 4-track directory).
    """
    SECTOR = 512
    DATA_PER_SECTOR = 510
    TRACK = SECTOR * 10
    SIDE = TRACK * 80
    total = SIDE * sides
    out = bytearray(total)

    def offset(track: int, sector: int) -> int:
        side = (track >> 7) & 1
        phys = track & 0x7F
        return (phys * 2 + side) * TRACK + (sector - 1) * SECTOR

    cursor_track, cursor_sector = 4, 1
    for i, (name, mgt_type, addr, length, data) in enumerate(files):
        # +3DOS-style header: type(1) + length(2) + addr(2) + zeros + ffff
        spectrum_type = 3 if mgt_type in (4, 7) else 0
        header = bytes([spectrum_type]) + struct.pack("<HH", length & 0xFFFF, addr & 0xFFFF) + b"\x00\x00\xff\xff"
        body = header + data

        # Write directory entry at slot i
        entry = bytearray(256)
        entry[0] = mgt_type
        entry[1:11] = name.encode("ascii")[:10].ljust(10, b" ")
        # Sector count: ceil(body / DATA_PER_SECTOR)
        sectors = (len(body) + DATA_PER_SECTOR - 1) // DATA_PER_SECTOR
        entry[11] = (sectors >> 8) & 0xFF  # big-endian
        entry[12] = sectors & 0xFF
        entry[13] = cursor_track
        entry[14] = cursor_sector
        # Inline header at 211..219
        entry[211] = spectrum_type
        struct.pack_into("<HH", entry, 212, length & 0xFFFF, addr & 0xFFFF)
        entry[218:220] = b"\xff\xff"
        directory_offset = offset(i // 20, 1) + (i % 20) * 256
        out[directory_offset : directory_offset + 256] = entry

        # Write body, chunked into sectors with chain pointers
        body_cursor = 0
        for s in range(sectors):
            off = offset(cursor_track, cursor_sector)
            chunk = body[body_cursor : body_cursor + DATA_PER_SECTOR]
            out[off : off + len(chunk)] = chunk
            # Chain to next sector
            cursor_sector += 1
            if cursor_sector > 10:
                cursor_sector = 1
                cursor_track += 1
            if s < sectors - 1:
                out[off + DATA_PER_SECTOR] = cursor_track
                out[off + DATA_PER_SECTOR + 1] = cursor_sector
            body_cursor += DATA_PER_SECTOR

    return bytes(out)


def make_z80_v3(
    pages: dict[int, bytes],
    *,
    hardware_mode: int = 0,
    border: int = 0,
    pc: int = 0x8000,
    port_7ffd: int = 0,
) -> bytes:
    """Build a v3 .z80 snapshot. `pages` maps page number -> 16384-byte data.

    All pages are stored uncompressed (length = 0xFFFF).
    """
    header = bytearray(30)  # PC stays 0 to mark v2/v3
    header[12] = (border & 0x07) << 1
    extra = bytearray(54)
    extra[0] = pc & 0xFF
    extra[1] = (pc >> 8) & 0xFF
    extra[2] = hardware_mode
    extra[3] = port_7ffd
    body = bytearray()
    for page_num, page_data in pages.items():
        if len(page_data) != 16384:
            raise ValueError(f"page {page_num} must be 16384 bytes")
        body += b"\xff\xff" + bytes([page_num]) + page_data
    return bytes(header) + struct.pack("<H", 54) + bytes(extra) + bytes(body)
