from pillow_zx_spectrum.loader import KIND_CODE, KIND_RAW, LoadEvent, extract_screens


def test_declared_code_screen_survives_zero_quality_score():
    screen = bytes(6144) + bytes(range(128, 256)) * 6
    assert extract_screens([LoadEvent(body=screen, addr=0x4000, kind=KIND_CODE)]) == [screen]


def test_unidentified_raw_data_still_gets_filtered():
    data = bytes(6144) + bytes(range(128, 256)) * 6
    assert extract_screens([LoadEvent(body=data, kind=KIND_RAW)]) == []
