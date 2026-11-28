"""Write approved dates into copies of the photos with exiftool. Originals are never modified."""

import subprocess
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

from exif_dater.dates import PhotoDate, parse_photo_date
from exif_dater.library import Library
from exif_dater.photos import OUTPUT_DIR

# Keyword added to photos whose month or day was filled in.
ESTIMATED_KEYWORD = "date-estimated"


def apply(library: Library, zone: str, today: date) -> tuple[int, list[str]]:
    """Copy every approved, unwritten group into the output folder.

    Return how many files were written and the errors. A failed file doesn't stop the others.
    """
    written = 0
    errors = []
    for group in library.groups:
        if not group.approved or group.written:
            continue
        try:
            taken = parse_photo_date(group.date, today)
        except ValueError as error:
            errors.append(f"group {group.id}: {error}")
            continue
        complete = True
        # Noon keeps time zones from shifting the day.
        noon = datetime.combine(taken.day, time(12))
        for position, file in enumerate(group.files):
            # One second apart keeps the scan order within the group.
            stamp = noon + timedelta(seconds=position)
            try:
                _write_copy(library, file, _exif_arguments(stamp, taken, zone, group.description))
                written += 1
            except (OSError, RuntimeError) as error:
                errors.append(f"{file}: {error}")
                complete = False
        group.written = complete
        library.save()
    return written, errors


def _write_copy(library: Library, file: str, arguments: list[str]) -> None:
    source = library.root / file
    target = library.root / OUTPUT_DIR / file
    if target.exists() and file not in library.outputs:
        raise RuntimeError(f"{target} already exists and was not created by this tool")
    target.parent.mkdir(parents=True, exist_ok=True)
    # exiftool only creates new files, so write beside the target and then replace it.
    with tempfile.TemporaryDirectory(prefix=".writing-", dir=target.parent) as scratch:
        temporary = Path(scratch) / target.name
        command = ["exiftool", *arguments, "-o", str(temporary), str(source)]
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"exiftool: {result.stderr.strip()}")
        temporary.replace(target)
    library.outputs.add(file)


def _exif_arguments(stamp: datetime, taken: PhotoDate, zone: str, description: str) -> list[str]:
    local = stamp.strftime("%Y:%m:%d %H:%M:%S")
    zoned = local + zone
    arguments = [
        f"-AllDates={local}",
        f"-EXIF:OffsetTime*={zone}",
        # Scanners also record dates in XMP, which some apps prefer over EXIF.
        f"-XMP-exif:DateTimeOriginal={zoned}",
        f"-XMP-photoshop:DateCreated={zoned}",
        f"-XMP-xmp:CreateDate={zoned}",
        f"-FileModifyDate={zoned}",
        # Removing before adding avoids a duplicate keyword.
        f"-XMP-dc:Subject-={ESTIMATED_KEYWORD}",
    ]
    if not taken.day_known:
        arguments.append(f"-XMP-dc:Subject+={ESTIMATED_KEYWORD}")
    if description:
        arguments.append(f"-XMP-dc:Description={description}")
    return arguments
