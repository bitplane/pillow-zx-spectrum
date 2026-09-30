import struct
import zlib

import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401
from pillow_zx_spectrum.spectrum_szx import _decode_ramp, iter_chunks, parse_szx

from ._helpers import make_screen, make_szx


def test_iter_chunks_yields_ramp():
    page5 = bytearray(16384)
    page5[:6912] = make_screen(0xFF, 0x07)
    szx = make_szx({5: bytes(page5)})
    chunks = list(iter_chunks(szx))
    assert len(chunks) == 1
    assert chunks[0][0] == b"RAMP"


def test_parse_szx_extracts_screen_from_page_5():
    page5 = bytearray(16384)
    s = make_screen(0xFF, 0x07)
    page5[:6912] = s
    snap = parse_szx(make_szx({5: bytes(page5)}))
    assert snap.screen() == s


def test_parse_szx_rejects_truncated_header():
    with pytest.raises(ValueError, match="too short for header"):
        parse_szx(b"ZXST")


def test_decode_ramp_handles_zlib_compression():
    page5 = bytearray(16384)
    page5[:6912] = make_screen(0xFF, 0x07)
    raw = bytes(page5)
    compressed_body = zlib.compress(raw)
    chunk = struct.pack("<H", 1) + bytes([5]) + compressed_body  # flags=1 (compressed)
    page, body = _decode_ramp(chunk)
    assert page == 5
    assert body == raw


def test_decode_ramp_rejects_oversized_compressed_page():
    chunk = struct.pack("<H", 1) + bytes([5]) + zlib.compress(b"\x00" * (16384 * 100))
    with pytest.raises(ValueError, match="exceeds 16384 bytes"):
        _decode_ramp(chunk)


def test_pillow_open_szx(tmp_path):
    page5 = bytearray(16384)
    page5[:6912] = make_screen(0xFF, 0x07)
    p = tmp_path / "synth.szx"
    p.write_bytes(make_szx({5: bytes(page5)}))
    img = Image.open(p)
    assert img.format == "ZXSZX"
    img.load()


def test_pillow_rejects_bad_magic(tmp_path):
    p = tmp_path / "junk.szx"
    p.write_bytes(b"NOTSZX!\x00" + b"\x00" * 100)
    with pytest.raises(UnidentifiedImageError):
        Image.open(p)
