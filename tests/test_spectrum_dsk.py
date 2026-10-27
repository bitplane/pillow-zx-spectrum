import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401  (registers plugins)
from pillow_zx_spectrum.plus3dos import TYPE_CODE, make_plus3_header
from pillow_zx_spectrum.spectrum_dsk import extract_screen, parse_dsk

from ._helpers import make_extended_dsk, make_screen


def _layout_screen_into_sectors(file_bytes: bytes, n_sectors: int = 14) -> list[bytes]:
    """Pad and split a +3DOS file into N 512-byte sectors."""
    padded = file_bytes + b"\x00" * (n_sectors * 512 - len(file_bytes))
    return [padded[i * 512 : (i + 1) * 512] for i in range(n_sectors)]


def _make_screen_dsk_track(skewed: bool) -> tuple[bytes, bytes]:
    """Build a 14-sector track containing a +3DOS SCREEN$ file.

    Returns (full_dsk_bytes, expected_screen_bytes).
    """
    screen = make_screen(0xFF, 0x07)
    file_bytes = make_plus3_header(TYPE_CODE, len(screen), 0x4000) + screen
    sector_data = _layout_screen_into_sectors(file_bytes)
    # Sector IDs 1..14, but stored in skewed physical order so the parser
    # has to reorder them by ID to read the file correctly.
    if skewed:
        # Classic 2:1 skew on the first 9 -> realistic
        order = [1, 8, 2, 9, 3, 10, 4, 11, 5, 12, 6, 13, 7, 14]
    else:
        order = list(range(1, 15))
    sectors = [(sid, sector_data[sid - 1]) for sid in order]
    dsk = make_extended_dsk([sectors])
    return dsk, screen


def test_parse_dsk_collects_all_sectors():
    dsk, _ = _make_screen_dsk_track(skewed=False)
    img = parse_dsk(dsk)
    assert img.tracks == 1
    assert img.sides == 1
    assert len(img.sectors) == 14


def test_extract_screen_from_in_order_sectors():
    dsk, expected = _make_screen_dsk_track(skewed=False)
    assert extract_screen(dsk) == expected


def test_extract_screen_from_skewed_sectors():
    """Sector IDs are stored physically out of order — must be re-sorted."""
    dsk, expected = _make_screen_dsk_track(skewed=True)
    assert extract_screen(dsk) == expected


def test_extract_screen_raises_when_no_real_screen():
    # Build a DSK with a non-CODE file -> nothing extractable.
    body = b"\x00" * 256
    file_bytes = make_plus3_header(0, len(body), 5) + body  # type=0 (BASIC)
    sectors = _layout_screen_into_sectors(file_bytes, n_sectors=2)
    dsk = make_extended_dsk([[(1, sectors[0]), (2, sectors[1])]])
    with pytest.raises(ValueError):
        extract_screen(dsk)


def test_parse_dsk_rejects_bad_magic():
    bad = b"NOT A DSK FILE" + b"\x00" * 1000
    with pytest.raises(ValueError):
        parse_dsk(bad)


# --- Pillow integration ---------------------------------------------------


def test_pillow_open_synthetic_dsk(tmp_path):
    dsk, expected = _make_screen_dsk_track(skewed=True)
    p = tmp_path / "synth.dsk"
    p.write_bytes(dsk)
    img = Image.open(p)
    assert img.format == "ZXDSK"
    assert img.mode == "RGB"
    assert img.size == (256, 192)
    img.load()


def test_pillow_rejects_non_dsk(tmp_path):
    p = tmp_path / "junk.dsk"
    p.write_bytes(b"random content that is not a dsk")
    with pytest.raises(UnidentifiedImageError):
        Image.open(p)


@pytest.mark.parametrize("with_directory", [False, True])
def test_basic_screen_sized_body_is_not_an_image(with_directory):
    from pillow_zx_spectrum.spectrum_dsk import extract_screens

    file = make_plus3_header(0, 6912, 10) + make_screen(255, 7)
    if with_directory:
        flat = bytearray(9 * 512 * 3)
        flat[4608:6656] = bytes([0xE5]) * 2048
        entry = bytearray(32)
        entry[1:12] = b"PROGRAM BAS"
        entry[15] = 55  # 7040 bytes including the +3DOS header
        entry[16:23] = bytes(range(2, 9))
        flat[4608:4640] = entry
        flat[6656 : 6656 + len(file)] = file
        tracks = [
            [(i + 1, bytes(flat[t + i * 512 : t + (i + 1) * 512])) for i in range(9)] for t in range(0, len(flat), 4608)
        ]
    else:
        sectors = _layout_screen_into_sectors(file)
        tracks = [[(i + 1, sector) for i, sector in enumerate(sectors)]]
    assert extract_screens(make_extended_dsk(tracks)) == []


@pytest.mark.parametrize("file_type", [0, 1, 2])
def test_non_code_parameters_are_not_load_addresses(file_type):
    from pillow_zx_spectrum.cpm import CpmFile
    from pillow_zx_spectrum.loader import extract_screens
    from pillow_zx_spectrum.spectrum_dsk import _event_from_cpm_file

    body = make_screen(255, 7) + bytes(512)
    file = CpmFile(0, "PROGRAM", "DAT", make_plus3_header(file_type, len(body), 0x4000) + body)
    event = _event_from_cpm_file(file)
    assert event.addr is None
    assert extract_screens([event]) == []
