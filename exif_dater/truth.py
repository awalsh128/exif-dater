"""The dates already known for some photos, which models are tested against: read, check and edit them."""

import csv
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

from exif_dater.dates import date_from_name, parse_photo_date
from exif_dater.library import Library
from exif_dater.photos import list_photos

TRUTH_FILE = "truth.csv"
# The file as it was before this app first changed it.
BACKUP_FILE = "truth.csv.bak"
APPROVALS = "dates approved in review"
# The column this app adds to record that the owner checked a date.
CONFIRMED = "confirmed"
_YES = {"yes", "true", "1"}


@dataclass
class Truth:
    """The known date of each photo, and where those dates came from."""

    dates: dict[str, str]
    source: str
    from_approvals: bool = False
    unconfirmed: int = 0  # Counted only in a file that records confirmations.


@dataclass
class Entry:
    """One known date as written, which may be blank or wrong until the owner fixes it."""

    file: str
    date: str
    confirmed: bool


def load_truth(library: Library, path: Path | None, today: date) -> Truth:
    """Read known dates from a CSV with "file" and "date" columns, and check that every one is usable.

    Without a path, use truth.csv in the photo directory, or else the dates approved in review.
    """
    if path is None and (library.root / TRUTH_FILE).exists():
        path = library.root / TRUTH_FILE
    if path is None:
        truth = Truth({row["file"]: row["date"] for row in _approvals(library)}, APPROVALS, True)
    else:
        columns, rows = _read(path)
        entries = {entry.file: entry for entry in map(_entry, rows)}
        unconfirmed = sum(not entry.confirmed for entry in entries.values()) if CONFIRMED in columns else 0
        truth = Truth(
            {file: entry.date for file, entry in entries.items()}, path.name, unconfirmed=unconfirmed
        )

    if not truth.dates:
        raise ValueError(f"no known dates: add {TRUTH_FILE} to the photo directory or approve some groups")
    photos = set(list_photos(library.root))
    for file, taken in truth.dates.items():
        if file not in photos:
            raise ValueError(f"{truth.source} names {file!r}, which isn't a photo in the directory")
        parse_photo_date(taken, today)
    return truth


def read_entries(library: Library) -> tuple[list[Entry], str]:
    """Return every known date, usable or not, and where they came from ("" when there are none)."""
    _, rows, source = _current(library)
    entries = {entry.file: entry for entry in map(_entry, rows)}
    return list(entries.values()), source


def check_entries(
    library: Library, today: date, within_years: int, estimates: Callable[[list[str]], dict[str, list[str]]]
) -> dict:
    """Return the known dates with the reasons to doubt each, and the photos that have none.

    estimates takes photos and returns what tested models answered for each.
    """
    entries, source = read_entries(library)
    photos = list_photos(library.root)
    present = set(photos)
    answered = estimates([entry.file for entry in entries if entry.file in present])
    checked = []
    for entry in entries:
        if entry.file in present:
            found = doubts(entry, today, answered.get(entry.file, []), within_years)
        else:
            found = ["Not in the photo folder. Remove it."]
        checked.append(asdict(entry) | {"present": entry.file in present, "doubts": found})
    listed = {entry.file for entry in entries}
    unlisted = [file for file in photos if file not in listed]
    return {"source": source, "entries": checked, "unlisted": unlisted}


def set_entry(library: Library, file: str, taken: str, confirmed: bool, today: date) -> None:
    """Add or change the known date of a photo in truth.csv.

    The date may be blank only while unconfirmed. If there is no truth.csv yet, it starts from the
    approved dates.
    """
    taken = taken.strip()
    if taken or confirmed:
        parse_photo_date(taken, today)
    if file not in list_photos(library.root):
        raise ValueError(f"{file!r} isn't a photo in the directory")
    columns, rows, _ = _current(library)
    matching = [row for row in rows if _entry(row).file == file]
    row = matching[-1] if matching else {"file": file}
    row.update({"file": file, "date": taken, CONFIRMED: "yes" if confirmed else ""})
    # One row for each photo, in the place it already had.
    kept = [other for other in rows if other is row or other not in matching]
    if not matching:
        kept.append(row)
    _write(library, columns, kept)


def remove_entry(library: Library, file: str) -> None:
    """Take a photo out of truth.csv, so that tests no longer use it."""
    columns, rows, _ = _current(library)
    kept = [row for row in rows if _entry(row).file != file]
    if len(kept) == len(rows):
        raise ValueError(f"{file!r} has no known date")
    _write(library, columns, kept)


def doubts(entry: Entry, today: date, estimates: list[str], within_years: int) -> list[str]:
    """Return the reasons to look at a known date again. Estimates are what tested models answered."""
    if not entry.date:
        return ["No date. Add one, or remove the photo."]
    try:
        known = parse_photo_date(entry.date, today)
    except ValueError:
        return ["Not a usable date. Write it as YYYY, YYYY-MM or YYYY-MM-DD."]

    found = []
    if known.day > today - timedelta(days=365):
        found.append("Dated within the last year, which for a scan is likely the day it was scanned.")
    if known.day_known and (known.day.month, known.day.day) == (1, 1):
        found.append("January 1 is often a placeholder. If you don't know the day, shorten it to the year.")
    named = date_from_name(entry.file, today)
    # A year agrees with any date in that year.
    if named and not (named.startswith(entry.date) or entry.date.startswith(named)):
        found.append(f"The file or folder name says {named}.")
    years = []
    for estimate in estimates:
        try:
            years.append(parse_photo_date(estimate, today).day.year)
        except ValueError:
            continue
    if years and all(abs(year - known.day.year) > within_years for year in years):
        answers = ", ".join(sorted({str(year) for year in years}))
        found.append(
            f"Every tested model answered something else ({answers}). Check your source, but don't change "
            "a date just to agree with a model."
        )
    return found


def _approvals(library: Library) -> list[dict]:
    return [
        {"file": file, "date": group.date}
        for group in library.groups
        if group.approved
        for file in group.files
    ]


def _current(library: Library) -> tuple[list[str], list[dict], str]:
    path = library.root / TRUTH_FILE
    if path.exists():
        columns, rows = _read(path)
        return columns, rows, TRUTH_FILE
    rows = _approvals(library)
    return ["file", "date"], rows, APPROVALS if rows else ""


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8-sig") as table:
        reader = csv.DictReader(table)
        columns = list(reader.fieldnames or [])
        if not {"file", "date"} <= set(columns):
            raise ValueError(f"{path.name} needs a header line with the columns file,date")
        return columns, list(reader)


def _entry(row: dict) -> Entry:
    confirmed = (row.get(CONFIRMED) or "").strip().lower() in _YES
    return Entry((row["file"] or "").strip(), (row["date"] or "").strip(), confirmed)


def _write(library: Library, columns: list[str], rows: list[dict]) -> None:
    """Replace truth.csv, keeping any other columns the owner added and a copy of the first version."""
    path = library.root / TRUTH_FILE
    backup = library.root / BACKUP_FILE
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    if CONFIRMED not in columns:
        columns = [*columns, CONFIRMED]
    temporary = path.with_name(TRUTH_FILE + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as table:
        writer = csv.DictWriter(table, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
