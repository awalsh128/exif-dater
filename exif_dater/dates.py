"""Dates that may only be known to the month or the year, including those typed into file names."""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath

# Photography did not exist before this year, so earlier dates are typos.
FIRST_PHOTO_YEAR = 1826

_DATE_SHAPE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")
# What completes a date of each precision into the first day of its period.
_FIRST_DAY = {len("YYYY"): "-01-01", len("YYYY-MM"): "-01", len("YYYY-MM-DD"): ""}
# A name that starts with a year, optionally followed by a separated month and day, such as "1987 beach"
# or "1987-06-14-0002". Unseparated digits are left alone: scanners and cameras number files that way.
_LEADING_DATE = re.compile(r"(\d{4})(?:[-_ .](\d{2})(?:[-_ .](\d{2}))?)?(?:\D|$)")


@dataclass(frozen=True)
class PhotoDate:
    day: date  # The first day of the period.
    day_known: bool  # False when the month or day was filled in.


def parse_photo_date(text: str, today: date) -> PhotoDate:
    """Parse YYYY, YYYY-MM or YYYY-MM-DD, no later than today. Raise ValueError otherwise."""
    if not _DATE_SHAPE.fullmatch(text):
        raise ValueError(f"date {text!r} is not YYYY, YYYY-MM or YYYY-MM-DD")
    day = date.fromisoformat(text + _FIRST_DAY[len(text)])
    if day.year < FIRST_PHOTO_YEAR or day > today:
        raise ValueError(f"date {text!r} is outside {FIRST_PHOTO_YEAR} to today")
    return PhotoDate(day, day_known=len(text) == len("YYYY-MM-DD"))


def date_from_name(file: str, today: date) -> str | None:
    """Read a date the owner typed into the file name or, failing that, the nearest folder name.

    Dates from the last year are ignored because they are most likely scan dates.
    """
    path = PurePosixPath(file)
    folders = [folder.name for folder in path.parents if folder.name]
    for name in [path.stem, *folders]:
        match = _LEADING_DATE.match(name)
        if not match:
            continue
        text = "-".join(part for part in match.groups() if part)
        try:
            parsed = parse_photo_date(text, today)
        except ValueError:
            continue
        if parsed.day < today - timedelta(days=365):
            return text
    return None
