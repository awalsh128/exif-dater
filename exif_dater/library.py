"""The saved progress for one photo directory: what is known about each photo and how they're grouped."""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from exif_dater.dates import date_from_name, parse_photo_date

STATE_FILE = ".exif-dater.json"


@dataclass
class Observation:
    """What one photo says about its date."""

    description: str
    printed_date: str
    clues: str
    date: str
    earliest_year: int
    latest_year: int
    confidence: str
    from_name: bool = False  # Read from the file name, with no model involved.


@dataclass
class Group:
    """Photos that share one date and are reviewed together."""

    files: list[str]
    title: str = ""
    date: str = ""
    confidence: str = "low"
    reason: str = ""
    description: str = ""
    id: int = 0
    approved: bool = False
    written: bool = False


@dataclass
class Library:
    root: Path
    observations: dict[str, Observation] = field(default_factory=dict)
    groups: list[Group] = field(default_factory=list)
    # The dated copies this tool created, which it may therefore overwrite.
    outputs: set[str] = field(default_factory=set)

    @classmethod
    def open(cls, root: Path) -> "Library":
        """Load the saved progress in root, or start an empty library."""
        root = root.resolve()
        try:
            saved = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls(root)
        return cls(
            root,
            {file: Observation(**seen) for file, seen in saved["observations"].items()},
            [Group(**group) for group in saved["groups"]],
            set(saved["outputs"]),
        )

    def save(self) -> None:
        """Write through a temporary file so an interruption can't truncate the progress."""
        state = {
            "observations": {file: asdict(seen) for file, seen in self.observations.items()},
            "groups": [asdict(group) for group in self.groups],
            "outputs": sorted(self.outputs),
        }
        temporary = self.root / (STATE_FILE + ".tmp")
        temporary.touch(mode=0o600)
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.root / STATE_FILE)

    def date_by_name(self, files: list[str], today: date) -> list[str]:
        """Observe every photo whose name carries a date and return the photos that still need the model."""
        remaining = []
        for file in files:
            named = date_from_name(file, today)
            if named is None:
                remaining.append(file)
                continue
            year = parse_photo_date(named, today).day.year
            self.observations[file] = Observation(
                description=f"Dated by name: {named}",
                printed_date="",
                clues="date in the file or folder name",
                date=named,
                earliest_year=year,
                latest_year=year,
                # Not high, so that bulk approval still leaves these for a look.
                confidence="medium",
                from_name=True,
            )
        return remaining

    def ungrouped(self) -> list[str]:
        """Return the observed photos that are not in any group yet."""
        grouped = {file for group in self.groups for file in group.files}
        return sorted(file for file in self.observations if file not in grouped)

    def add_groups(self, files: list[str], proposed: list[Group]) -> None:
        """Add proposed groups for files, making sure each file lands in exactly one.

        Unknown and repeated names are dropped. A file the proposal missed joins other name-dated photos
        with the same date, or else gets its own group.
        """
        pending = set(files)
        for group in proposed:
            members = sorted(pending.intersection(group.files))
            if members:
                pending.difference_update(members)
                group.files = members
                self._add(group)
        by_name: dict[str, Group] = {}
        for file in sorted(pending):
            seen = self.observations[file]
            if seen.from_name and seen.date in by_name:
                by_name[seen.date].files.append(file)
                continue
            group = self._group_of_one(file, seen.date)
            self._add(group)
            if seen.from_name:
                by_name[seen.date] = group

    def group(self, group_id: int) -> Group:
        for group in self.groups:
            if group.id == group_id:
                return group
        raise ValueError(f"no group {group_id}")

    def review(self, group_id: int, taken: str, description: str, approved: bool, today: date) -> None:
        """Record the user's decision on a group. Approval requires a valid date."""
        group = self.group(group_id)
        if approved:
            parse_photo_date(taken, today)
        if (group.date, group.description, group.approved) != (taken, description, approved):
            group.written = False
        group.date, group.description, group.approved = taken, description, approved

    def split(self, file: str) -> None:
        """Move one photo out of its group into a new unapproved group with the same date."""
        for group in self.groups:
            if file not in group.files:
                continue
            if len(group.files) == 1:
                raise ValueError(f"{file} is already alone in its group")
            group.files.remove(file)
            self._add(self._group_of_one(file, group.date))
            return
        raise ValueError(f"no group contains {file}")

    def _group_of_one(self, file: str, taken: str) -> Group:
        seen = self.observations[file]
        return Group(
            [file], title=seen.description, date=taken, confidence=seen.confidence, reason=seen.clues
        )

    def _add(self, group: Group) -> None:
        """Append the group under a new ID, unreviewed."""
        group.id = max((existing.id for existing in self.groups), default=0) + 1
        group.approved = group.written = False
        self.groups.append(group)
