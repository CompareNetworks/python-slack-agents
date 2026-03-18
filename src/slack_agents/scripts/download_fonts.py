#!/usr/bin/env python3
"""Download DejaVu Sans fonts for PDF generation with Unicode support."""

import io
import zipfile
from pathlib import Path
from urllib.request import urlopen

DEJAVU_VERSION = "2.37"
URL = (
    f"https://github.com/dejavu-fonts/dejavu-fonts/releases/download/"
    f"version_{DEJAVU_VERSION.replace('.', '_')}/dejavu-fonts-ttf-{DEJAVU_VERSION}.zip"
)
NEEDED = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf"]
FONT_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fonts"


def main():
    FONT_DIR.mkdir(exist_ok=True)

    if all((FONT_DIR / f).exists() for f in NEEDED):
        print(f"Fonts already present in {FONT_DIR}")
        return

    print(f"Downloading DejaVu Sans {DEJAVU_VERSION}...")
    data = urlopen(URL).read()  # noqa: S310

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name in NEEDED:
                (FONT_DIR / name).write_bytes(zf.read(member))
                print(f"  Extracted {name}")

    print(f"Done. Fonts saved to {FONT_DIR}")


if __name__ == "__main__":
    main()
