import pytest
from PIL import Image, UnidentifiedImageError

import pillow_zx_spectrum  # noqa: F401
from pillow_zx_spectrum.snapshot import MachineType
from pillow_zx_spectrum.spectrum_sna import parse_sna

from ._helpers import make_screen, make_sna_48k


def test_parse_48k_sna_screen():
    ram = bytearray(49152)
    s = make_screen(0xFF, 0x07)
    ram[:6912] = s
    snap = parse_sna(make_sna_48k(bytes(ram), border=4))
    assert snap.machine == MachineType.SPECTRUM_48K
    assert snap.border == 4
    assert snap.screen() == s


def test_parse_rejects_wrong_size():
    with pytest.raises(ValueError):
        parse_sna(b"\x00" * 100)


def test_pillow_open_sna(tmp_path):
    ram = bytearray(49152)
    ram[:6912] = make_screen(0xFF, 0x07)
    p = tmp_path / "synth.sna"
    p.write_bytes(make_sna_48k(bytes(ram)))
    img = Image.open(p)
    assert img.format == "ZXSNA"
    img.load()


def test_pillow_rejects_wrong_size(tmp_path):
    p = tmp_path / "junk.sna"
    p.write_bytes(b"\x00" * 1234)
    with pytest.raises(UnidentifiedImageError):
        Image.open(p)


@pytest.mark.parametrize("paged_bank", range(8))
def test_128k_sna_banks_and_pillow(paged_bank):
    import io

    banks = {i: make_screen(255, i) + bytes([i]) * (16384 - 6912) for i in range(8)}
    data = (
        bytes(27)
        + banks[5]
        + banks[2]
        + banks[paged_bank]
        + bytes([0, 128, paged_bank, 0])
        + b"".join(banks[i] for i in range(8) if i not in {2, 5, paged_bank})
    )
    snap = parse_sna(data)
    assert snap.machine == MachineType.SPECTRUM_128K
    assert snap.banks == banks
    assert snap.ram[0xC000:] == banks[paged_bank]
    assert snap.screen() == banks[5][:6912]
    assert snap.shadow_screen() == banks[7][:6912]
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "ZXSNA"
        assert image.n_frames == 2
        image.load()
    if paged_bank in (2, 5):
        with pytest.raises(ValueError, match="paged bank"):
            parse_sna(data[:-16384])
        damaged = bytearray(data)
        damaged[27 + 32768] ^= 1
        with pytest.raises(ValueError, match="duplicated bank"):
            parse_sna(damaged)
