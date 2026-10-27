"""Pillow loaders for screens from 8-bit computer tapes."""

from PIL import Image

from . import (
    pillow_screen,
    spectrum_didaktik,
    spectrum_dsk,
    spectrum_mdr,
    spectrum_mgt,
    spectrum_scl,
    spectrum_scr,
    spectrum_slt,
    spectrum_sna,
    spectrum_szx,
    spectrum_tap,
    spectrum_trd,
    spectrum_tzx,
    spectrum_z80,
)

# Register Pillow's signature-based formats before our permissive loaders.
# Image.open() otherwise encounters size-only Spectrum formats first.
Image.init()
# Keep signatureless Pillow formats (notably TGA) behind Spectrum loaders:
# raw screen bytes can also satisfy their permissive header checks.
_signatureless_formats = [name for name in Image.ID if Image.OPEN[name][1] is None]
for _name in _signatureless_formats:
    Image.ID.remove(_name)

# The shared ScreenBytesDecoder is used by every multi-frame plugin.
pillow_screen.register_decoder()

# Pillow tries formats in registration order when several plugins accept
# the same prefix. Register strict-magic formats first, then formats with
# strong in-_open validation (size constraints, system-sector magic),
# then the loose-accept ones last so they don't shadow the others.
spectrum_dsk.register()  # "EXTENDED CPC DSK File" / "MV - CPC"
spectrum_tzx.register()  # "ZXTape!\x1a"
spectrum_szx.register()  # "ZXST"
spectrum_scl.register()  # "SINCLAIR"
spectrum_scr.register()  # exactly 6912 bytes
spectrum_sna.register()  # 48K or either 128K snapshot size
spectrum_trd.register()  # TR-DOS magic byte 0x10 at $8E7
spectrum_mgt.register()  # MGT directory validity check
spectrum_mdr.register()  # Microdrive sector-checksum validity check
spectrum_didaktik.register()  # Didaktik MDOS "SDOS" marker at byte 204
spectrum_slt.register()  # z80 + "\x00\x00\x00SLT" marker
spectrum_z80.register()  # any v1/v2/v3 z80 (loosest)
spectrum_tap.register()  # length+flag fingerprint (loosest tape)
Image.ID.extend(_signatureless_formats)

__all__ = [
    "spectrum_dsk",
    "spectrum_scr",
    "spectrum_tap",
    "spectrum_tzx",
    "spectrum_z80",
]
