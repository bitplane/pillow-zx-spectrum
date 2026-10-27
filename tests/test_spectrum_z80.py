import struct

import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401  (registers plugins)
from pillow_zx_spectrum.snapshot import MachineType
from pillow_zx_spectrum.spectrum_z80 import (
    _validate_header,
    decompress_z80,
    parse_z80,
)

from ._helpers import make_screen, make_z80_v1, make_z80_v3


# --- Decompression --------------------------------------------------------


def test_decompress_passes_literals():
    out, used = decompress_z80(b"\x00\x01\x02\x03")
    assert out == b"\x00\x01\x02\x03"
    assert used == 4


def test_decompress_single_ed_followed_by_non_ed_is_two_literals():
    # ED 00 -> literal ED, literal 00.
    out, used = decompress_z80(b"\xed\x00")
    assert out == b"\xed\x00"
    assert used == 2


def test_decompress_run_length():
    # ED ED 05 AA -> AAAAA
    out, used = decompress_z80(b"\xed\xed\x05\xaa")
    assert out == b"\xaa" * 5
    assert used == 4


def test_decompress_two_ed_bytes_encoded():
    # ED ED 02 ED -> ED ED  (the canonical encoding for two consecutive EDs)
    out, used = decompress_z80(b"\xed\xed\x02\xed")
    assert out == b"\xed\xed"
    assert used == 4


def test_decompress_terminator_stops_at_count_zero():
    # 00 ED ED 00 FF = literal 00 followed by the 4-byte v1 terminator
    # (ED ED 00 xx with count=0). The 0xFF byte is the count's value field.
    out, used = decompress_z80(b"\x00\xed\xed\x00\xff\xff")
    assert out == b"\x00"
    assert used == 5  # 1 literal + 4-byte terminator
    # The byte after the terminator is not consumed.


def test_decompress_max_output_caps_emission():
    out, used = decompress_z80(b"\xed\xed\x10\xaa", max_output=8)
    # 0x10 (16) AAs requested but max_output is 8 -> we still process the
    # whole 4-byte run and emit all 16 bytes; the cap is checked at the top
    # of the loop, so output may exceed by up to one run.
    assert out == b"\xaa" * 16
    assert used == 4


def test_decompress_truncated_run_raises():
    with pytest.raises(ValueError):
        decompress_z80(b"\xed\xed\x05")  # missing value byte


# --- v1 parsing ----------------------------------------------------------


def test_parse_v1_uncompressed_round_trip():
    ram = bytearray(49152)
    screen = make_screen(0xFF, 0x07)
    ram[:6912] = screen  # at offset 0 in the body == $4000 in RAM
    snap = parse_z80(make_z80_v1(bytes(ram), pc=0x8000))
    assert snap.machine == MachineType.SPECTRUM_48K
    assert snap.screen() == screen


def test_parse_v1_compressed_round_trip():
    # Build a compressed body: 6912 bytes of literal screen, then 42240 zeros
    # encoded as one big run, then v1 terminator.
    screen = make_screen(0xFF, 0x07)
    # ED ED can't carry a count > 255, so split into chunks of 255.
    zeros = bytearray()
    remaining = 42240
    while remaining > 0:
        chunk = min(255, remaining)
        zeros += b"\xed\xed" + bytes([chunk]) + b"\x00"
        remaining -= chunk
    body = screen + bytes(zeros) + b"\x00\xed\xed\x00"
    snap = parse_z80(make_z80_v1(b"\x00" * 49152, pc=0x8000, compressed_body=body))
    assert snap.screen() == screen
    # The rest of RAM should be zeros.
    assert all(b == 0 for b in snap.ram[0x4000 + 6912 : 0x10000])


def test_parse_v1_border_is_extracted():
    # bits 1-3 of byte 12. With border=5 and PC nonzero, byte12 = 5<<1 = 10.
    snap = parse_z80(make_z80_v1(b"\x00" * 49152, border=5))
    assert snap.border == 5


def test_parse_v1_byte12_ff_is_treated_as_one():
    # byte 12 == 0xFF must be treated as 0x01 (so border=0, not compressed)
    raw = bytearray(make_z80_v1(b"\x00" * 49152))
    raw[12] = 0xFF
    snap = parse_z80(bytes(raw))
    assert snap.border == 0
    assert snap.machine == MachineType.SPECTRUM_48K


# --- v3 parsing ----------------------------------------------------------


def test_parse_v3_48k_extracts_screen_from_page_8():
    screen = make_screen(0xFF, 0x07)
    page8 = bytearray(16384)
    page8[:6912] = screen
    pages = {
        4: bytes(16384),  # $8000
        5: bytes(16384),  # $C000
        8: bytes(page8),  # $4000 (screen here)
    }
    snap = parse_z80(make_z80_v3(pages, hardware_mode=0))
    assert snap.machine == MachineType.SPECTRUM_48K
    assert snap.screen() == screen


def test_parse_v3_128k_screen_in_bank_5_page_8():
    screen = make_screen(0xAA, 0x07)
    page8 = bytearray(16384)
    page8[:6912] = screen
    pages = {n: bytes(16384) for n in range(3, 11)}
    pages[8] = bytes(page8)  # bank 5 = file page 8
    snap = parse_z80(make_z80_v3(pages, hardware_mode=4))  # v3 mode 4 = 128K
    assert snap.machine == MachineType.SPECTRUM_128K
    assert snap.screen() == screen
    assert len(snap.banks) == 8


def test_parse_v3_128k_port_7ffd_selects_top_bank():
    """Bank selected by port 0x7FFD bits 0..2 should be mapped to $C000."""
    pages = {n: bytes([n] * 16384) for n in range(3, 11)}
    snap = parse_z80(make_z80_v3(pages, hardware_mode=4, port_7ffd=0x03))
    # Bank 3 = file page 6 -> mapped to $C000 because (port_7ffd & 7) == 3.
    assert bytes(snap.ram[0xC000:0xC004]) == bytes([6, 6, 6, 6])
    # Bank 5 (page 8) at $4000 always.
    assert bytes(snap.ram[0x4000:0x4004]) == bytes([8, 8, 8, 8])
    # Bank 2 (page 5) at $8000 always.
    assert bytes(snap.ram[0x8000:0x8004]) == bytes([5, 5, 5, 5])


def test_parse_v3_compressed_page_with_run_length():
    # Build a compressed page that decompresses to 16384 bytes of 0xCC.
    page_data = b"\xed\xed\xff\xcc" * 64 + b"\xed\xed\x40\xcc"  # 255*64 + 64 = 16384
    page_block = struct.pack("<H", len(page_data)) + b"\x08" + page_data  # page 8
    raw = bytearray(make_z80_v3({}, hardware_mode=0))
    raw += page_block
    snap = parse_z80(bytes(raw))
    assert all(b == 0xCC for b in snap.ram[0x4000:0x8000])


# --- Detection / Pillow integration --------------------------------------


def test_validate_header_v1():
    # PC nonzero, plausible v1 file size -> v1 candidate
    assert _validate_header(bytes([0] * 6) + b"\x00\x80" + bytes([0] * 22), 49182)


def test_validate_header_v1_rejects_oversized():
    # PC nonzero but file is far too big for v1 -> reject (e.g. a DSK)
    assert not _validate_header(bytes([0] * 6) + b"\x00\x80" + bytes([0] * 22), 175360)


def test_validate_header_v3():
    head = bytes([0] * 30) + struct.pack("<H", 54)  # PC=0, extra_len=54
    assert _validate_header(head, 32 + 54 + 16384 * 8)


def test_validate_header_rejects_bad_extra_len():
    bad = bytes([0] * 30) + b"\x00\x00"
    assert not _validate_header(bad, 1024)


def test_pillow_open_v3_snapshot(tmp_path):
    screen = make_screen(0xFF, 0x07)
    page8 = bytearray(16384)
    page8[:6912] = screen
    raw = make_z80_v3({4: bytes(16384), 5: bytes(16384), 8: bytes(page8)})
    p = tmp_path / "snap.z80"
    p.write_bytes(raw)
    img = Image.open(p)
    assert img.format == "ZXZ80"
    assert img.size == (256, 192)
    img.load()


def test_pillow_open_v1_snapshot(tmp_path):
    ram = bytearray(49152)
    screen = make_screen(0xAA, 0x02)
    ram[:6912] = screen
    p = tmp_path / "v1.z80"
    p.write_bytes(make_z80_v1(bytes(ram)))
    img = Image.open(p)
    assert img.format == "ZXZ80"
    img.load()


def test_pillow_rejects_obviously_not_z80(tmp_path):
    p = tmp_path / "bad.z80"
    p.write_bytes(bytes(30) + b"\x00\x00")  # PC=0, extra_len=0 (invalid)
    with pytest.raises(UnidentifiedImageError):
        Image.open(p)


@pytest.mark.parametrize("paged_bank", range(8))
def test_128k_fixed_and_paged_mappings(paged_bank):
    banks = {i: bytes([i + 1]) * 16384 for i in range(8)}
    pages = {i + 3: body for i, body in banks.items()}
    snap = parse_z80(make_z80_v3(pages, hardware_mode=4, port_7ffd=paged_bank))
    assert snap.banks == banks
    assert len(snap.ram) == 65536
    assert snap.ram[0x4000:0x8000] == banks[5]
    assert snap.ram[0x8000:0xC000] == banks[2]
    assert snap.ram[0xC000:] == banks[paged_bank]
