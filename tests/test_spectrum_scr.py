import hashlib

import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401  (registers the plugin)
from pillow_zx_spectrum.palette import SPECTRUM_BRIGHT, SPECTRUM_NORMAL

COLORBARS_PIXEL_SHA256 = "31ad8e99ff03cbca0eaa9ca37802d02f7d6056e4311310dd273ec02eea1609bd"


def test_open_colorbars(data_dir):
    img = Image.open(data_dir / "colorbars.scr")
    assert img.format == "ZXSCR"
    assert img.mode == "RGB"
    assert img.size == (256, 192)
    assert img.info["pixel_aspect_ratio"] == (1, 1)


def test_colorbars_pixel_spot_checks(data_dir):
    img = Image.open(data_dir / "colorbars.scr")
    img.load()
    # Top half (rows 0..95): normal palette. Each bar is 32 px wide.
    # Pick the centre pixel of each bar at y=48 (row 6, sub-row 0).
    for ink in range(8):
        x = ink * 32 + 16
        assert img.getpixel((x, 48)) == SPECTRUM_NORMAL[ink], f"normal bar {ink} at x={x}"
    # Bottom half (rows 96..191): bright palette.
    for ink in range(8):
        x = ink * 32 + 16
        assert img.getpixel((x, 144)) == SPECTRUM_BRIGHT[ink], f"bright bar {ink} at x={x}"


def test_colorbars_pixel_hash(data_dir):
    img = Image.open(data_dir / "colorbars.scr")
    img.load()
    digest = hashlib.sha256(img.tobytes()).hexdigest()
    assert digest == COLORBARS_PIXEL_SHA256


def test_wrong_size_rejected(tmp_path):
    bad = tmp_path / "short.scr"
    bad.write_bytes(b"\x00" * 100)
    with pytest.raises(UnidentifiedImageError):
        Image.open(bad)


def test_wrong_size_rejected_too_long(tmp_path):
    bad = tmp_path / "long.scr"
    bad.write_bytes(b"\x00" * 7000)
    with pytest.raises(UnidentifiedImageError):
        Image.open(bad)


def test_standard_formats_take_precedence_in_fresh_process():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import io
from PIL import Image
import pillow_zx_spectrum
for format in ('PNG', 'BMP', 'TIFF'):
    stream = io.BytesIO()
    Image.new('RGB', (1, 1), 'red').save(stream, format=format)
    data = stream.getvalue().ljust(6912, b'\\0')
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == format, (format, image.format)
        assert image.size == (1, 1)
        assert image.getpixel((0, 0)) == (255, 0, 0)
# A valid raw screen may resemble a signatureless TGA header.
for data in (bytes(6912), bytes([0, 0, 3]) + bytes(9) + bytes([1, 0, 1, 0, 8, 0]) + bytes(6894)):
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == 'ZXSCR'
        image.load()
# Signatureless formats still work when Spectrum validation rejects them.
stream = io.BytesIO()
Image.new('RGB', (2, 2), 'red').save(stream, format='TGA')
with Image.open(io.BytesIO(stream.getvalue())) as image:
    assert image.format == 'TGA'
    image.load()
""",
        ],
        check=True,
    )
