import struct

import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401  (registers plugins)
from pillow_zx_spectrum.blocks import TYPE_CODE
from pillow_zx_spectrum.spectrum_tzx import (
    extract_screen,
    iter_tzx_blocks,
)

from ._helpers import (
    make_data_block,
    make_header_block,
    make_screen,
    make_tzx,
    tzx_standard_block,
)


def test_iter_tzx_yields_only_standard_blocks():
    screen = make_screen(0xFF, 0x07)
    header = make_header_block(TYPE_CODE, "scr", 6912, 0x4000)
    data = make_data_block(screen)
    # Insert a 0x30 text desc and 0x32 archive info between header & data.
    text = b"made for tests"
    desc_block = bytes([0x30, len(text)]) + text
    arch_payload = b"\x01\x05hello"
    arch_block = bytes([0x32]) + struct.pack("<H", len(arch_payload)) + arch_payload
    tzx = b"ZXTape!\x1a\x01\x14" + desc_block + tzx_standard_block(header) + arch_block + tzx_standard_block(data)
    blocks = list(iter_tzx_blocks(tzx))
    assert len(blocks) == 2
    assert blocks[0].is_header()
    assert blocks[1].is_data()
    assert blocks[1].payload == screen


def test_extract_screen_from_synthetic_tzx():
    screen = make_screen(0xFF, 0x07)
    tzx = make_tzx(
        make_header_block(TYPE_CODE, "scr", 6912, 0x4000),
        make_data_block(screen),
    )
    assert extract_screen(tzx) == screen


def test_extract_screen_falls_back_to_6912_byte_block():
    """Hobbit-style: header points elsewhere; we still find the screen."""
    screen = make_screen(0xAA, 0x07)
    tzx = make_tzx(
        make_header_block(TYPE_CODE, "p", 6912, 0x9C40),
        make_data_block(screen),
    )
    assert extract_screen(tzx) == screen


def test_extract_screen_with_basic_loader_and_screen_and_code():
    screen = make_screen(0xFF, 0x02)
    main_code = b"\x00" * 1000
    tzx = make_tzx(
        make_header_block(0, "loader", 50, 5),  # BASIC header
        make_data_block(b"\x00" * 50),  # BASIC data
        make_header_block(TYPE_CODE, "scr", 6912, 0x4000),
        make_data_block(screen),
        make_header_block(TYPE_CODE, "main", len(main_code), 0x8000),
        make_data_block(main_code),
    )
    assert extract_screen(tzx) == screen


def test_iter_tzx_rejects_bad_magic():
    with pytest.raises(ValueError):
        list(iter_tzx_blocks(b"NOTZX!\x1a\x01\x14"))


@pytest.mark.parametrize(
    "tail",
    [b"\x10", b"\x11" + bytes(5), b"\x10\x00\x00\x20\x00\xff\x00"],
)
def test_iter_tzx_rejects_truncated_blocks(tail):
    with pytest.raises(ValueError, match="truncated TZX"):
        list(iter_tzx_blocks(b"ZXTape!\x1a\x01\x14" + tail))


def test_iter_tzx_raises_on_unsupported_block():
    # 0x99 isn't a defined TZX block ID; we should raise rather than
    # silently misinterpret bytes.
    bad = b"ZXTape!\x1a\x01\x14" + bytes([0x99])
    with pytest.raises(NotImplementedError):
        list(iter_tzx_blocks(bad))


# --- Pillow integration ---------------------------------------------------


def test_pillow_open_synthetic_tzx(tmp_path):
    screen = make_screen(0xFF, 0x07)
    tzx = make_tzx(
        make_header_block(TYPE_CODE, "scr", 6912, 0x4000),
        make_data_block(screen),
    )
    p = tmp_path / "synth.tzx"
    p.write_bytes(tzx)
    img = Image.open(p)
    assert img.format == "ZXTZX"
    assert img.mode == "RGB"
    assert img.size == (256, 192)
    assert img.info["pixel_aspect_ratio"] == (1, 1)
    img.load()  # decode through the PyDecoder


def test_pillow_rejects_non_tzx(tmp_path):
    p = tmp_path / "junk.tzx"
    p.write_bytes(b"not a tape file")
    with pytest.raises(UnidentifiedImageError):
        Image.open(p)
