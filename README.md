# `pillow_zx_spectrum`

<table>
<tr>
<td><img src="https://bitplane.net/dev/python/pillow-zx-spectrum/jetpac.png" alt="Jetpac"></td>
<td><img src="https://bitplane.net/dev/python/pillow-zx-spectrum/dizzy.png" alt="Treasure Island Dizzy"></td>
<td><img src="https://bitplane.net/dev/python/pillow-zx-spectrum/decathlon.png" alt="Daley Thompson's Decathlon"></td>
<td><img src="https://bitplane.net/dev/python/pillow-zx-spectrum/willy.png" alt="Jet Set Willy II"></td>
</tr>
</table>

Pillow loaders for ZX Spectrum loading screens, extracted from tape, disk
and snapshot files in the wild.

Open a tape, disk image, or snapshot with `Image.open(...)` and Pillow
gives you a 256×192 RGB image with the original Spectrum colours. Files
that contain multiple screens (multi-load games, multi-side disks)
expose them as Pillow frames via `seek()` / `n_frames`.

## Install

```bash
pip install pillow_zx_spectrum
```

## Command line

Open a Spectrum image up in your image viewer, or write a found frame to disk:

```bash
zx-screen "Glug Glug (1984)(CRL).tap"
zx-screen "Moonwalker (Erbe).dsk" --frame 1 -o moonwalker.png
```

## Usage

```python
from PIL import Image
import pillow_zx_spectrum  # registers the plugins on import

img = Image.open("Glug Glug (1984)(CRL).tap")
img.save("loading-screen.png")
print(img.size)                   # (256, 192)
print(img.info["pixel_aspect_ratio"])  # (1, 1)
```

Multi-screen containers (disks, multi-load tapes) expose every screen
they hold. Iterate with the stock Pillow helper:

```python
from PIL import ImageSequence

img = Image.open("Moonwalker (Erbe).dsk")
print(img.n_frames)  # e.g. 2
for i, frame in enumerate(ImageSequence.Iterator(img)):
    frame.save(f"moonwalker.{i}.png")
```

## How it works

A file becomes a stream of `(addr, body, name, kind)` events, and if one is the
same size as a `SCREEN$`, or it overlaps the `$4000-$5AFF` graphics region, then
you'll get an image for each one (assuming it looks sensible).

## Supported formats

Most of them tbh:

### Tape

| Ext    | Format          | Notes |
| ------ | --------------- | ----- |
| `.tap` | TAP             | Plain ROM-loader block stream |
| `.tzx` | TZX             | Versioned tape with timing/meta blocks; we extract from standard (0x10) and turbo-speed (0x11) data blocks. Custom-loader pure-data blocks (SpeedLock / BleepLoad / Alkatraz, block 0x14) are skipped — they need per-protection decoders. |
| `.scr` | SCREEN$         | Raw 6912-byte screen dump |

### Disk

| Ext    | Format          | Notes |
| ------ | --------------- | ----- |
| `.dsk` | CPC DSK         | Spectrum +3 / Amstrad CPC, both standard and "Extended" variants. CP/M file system parsed (handles fragmented allocation). Auto-detects single/double-sided and various reserved-track conventions. |
| `.scl` | TR-DOS packed   | "SINCLAIR" magic, compact distribution format used in Russian-speaking scene |
| `.trd` | TR-DOS raw      | Sector-by-sector TR-DOS floppy dump; tolerant of truncated images |
| `.mgt` | DISCiPLE / +D   | Side-interleaved 80-track disk for the MGT DISCiPLE & +D interfaces. CODE/SCREEN$ files reassembled by following the per-sector chain. |
| `.d40`, `.d80` | Didaktik MDOS | Czechoslovak Didaktik D40/D80 floppy. MDOS file system with non-standard FAT12 packing (the 12-bit entries' high nibbles are byte-swapped relative to MS FAT12). Auto-detects geometry from the boot sector "SDOS" marker. Most TOSEC Czech games use packed loaders so plain SCREEN$ extraction is rare. |

### Microdrive

| Ext    | Format          | Notes |
| ------ | --------------- | ----- |
| `.mdr` | Microdrive cart | Sector-by-sector dump of a Spectrum Microdrive cartridge (543-byte sectors with mod-255 checksums). Files reconstructed by gathering all records belonging to a filename and stripping the inline 9-byte header. PRINT# files are not handled. |

### Snapshot

| Ext    | Format          | Notes |
| ------ | --------------- | ----- |
| `.sna` | SNA             | Classic 48K (49179 bytes) / 128K (131103 or 147487 bytes) snapshot |
| `.z80` | Z80             | Gerton Lunter format (v1, v2, v3); 48K and 128K with bank decompression |
| `.szx` | SZX / ZX-State  | Spectaculator's modern chunked snapshot, with zlib-compressed RAM pages |
| `.slt` | SLT             | "Super Level Loader" — a Z80 snapshot with a table of additional screens (each becomes a frame) |

For 128K snapshots the shadow screen (bank 7) might be exposed as an extra
frame.

### Unsupported

You'll get `UnidentifiedImageError`s for sampled audio tapes, custom loaders, 
copy protected games and other weird things. It doesn't run any Z80 machine code
so programmatically made screens aren't decoded.

## License

WTFPL with one additional clause:

1. Don't blame me.

Do what you like, but you're to blame.
