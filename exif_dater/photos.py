"""Find photo files and render previews of them."""

import os
import subprocess
from pathlib import Path

# The folder inside the photo directory that receives dated copies.
OUTPUT_DIR = "dated"

# Formats that both ImageMagick can read and exiftool can write.
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"}


def list_photos(root: Path) -> list[str]:
    """Return the photos under root as sorted relative paths, skipping hidden entries and the output."""
    photos = []
    for folder, subfolders, files in os.walk(root):
        here = Path(folder)
        subfolders[:] = [
            name
            for name in subfolders
            if not name.startswith(".") and not (here == root and name == OUTPUT_DIR)
        ]
        for name in files:
            path = here / name
            if name.startswith(".") or path.is_symlink():
                continue
            if path.suffix.lower() in PHOTO_EXTENSIONS:
                photos.append(path.relative_to(root).as_posix())
    return sorted(photos)


def preview(path: Path, max_edge: int) -> bytes:
    """Render the photo as an upright JPEG no longer than max_edge pixels on either side."""
    # The image goes through stdin because ImageMagick treats characters in file names as syntax.
    # "-delete 1--1" keeps only the first page of a multi-page TIFF.
    command = ["magick", "-", "-delete", "1--1", "-auto-orient"]
    command += ["-resize", f"{max_edge}x{max_edge}>", "-quality", "90", "jpg:-"]
    with path.open("rb") as photo:
        result = subprocess.run(command, check=False, stdin=photo, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"cannot convert {path.name}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout
