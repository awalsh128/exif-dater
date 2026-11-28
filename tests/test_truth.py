import pytest
from conftest import TODAY

from exif_dater.library import Group
from exif_dater.truth import Entry, doubts, load_truth, read_entries, remove_entry, set_entry

HAND_MADE = "file,date,source\na.jpg,1985,album\nb.jpg,,\n"


@pytest.fixture
def library(make_library, tmp_path):
    """A library whose directory holds two photos. Nothing reads their contents."""
    for file in ("a.jpg", "b.jpg"):
        (tmp_path / file).touch()
    return make_library("a.jpg", "b.jpg")


def truth_file(library):
    return library.root / "truth.csv"


def test_read_entries_keeps_blank_dates(library):
    truth_file(library).write_text(HAND_MADE, encoding="utf-8")

    entries, source = read_entries(library)

    assert (entries, source) == (
        [Entry("a.jpg", "1985", False), Entry("b.jpg", "", False)],
        "truth.csv",
    )


def test_read_entries_without_a_file_uses_approved_groups(library):
    library.add_groups(["a.jpg"], [Group(["a.jpg"], date="1986")])
    library.groups[0].approved = True

    entries, source = read_entries(library)

    assert (entries, source) == ([Entry("a.jpg", "1986", False)], "dates approved in review")


def test_set_entry_confirms_and_keeps_other_columns_and_the_first_version(library):
    truth_file(library).write_text(HAND_MADE, encoding="utf-8")

    set_entry(library, "a.jpg", "1985-06", True, TODAY)
    set_entry(library, "a.jpg", "1985-07", True, TODAY)

    assert truth_file(library).read_text(encoding="utf-8").splitlines() == [
        "file,date,source,confirmed",
        "a.jpg,1985-07,album,yes",
        "b.jpg,,,",
    ]
    assert (library.root / "truth.csv.bak").read_text(encoding="utf-8") == HAND_MADE


def test_set_entry_without_a_file_starts_one_from_approvals(library):
    library.add_groups(["a.jpg"], [Group(["a.jpg"], date="1986")])
    library.groups[0].approved = True

    set_entry(library, "b.jpg", "1990", False, TODAY)

    assert [entry.file for entry in read_entries(library)[0]] == ["a.jpg", "b.jpg"]


@pytest.mark.parametrize(
    ("file", "taken", "confirmed", "message"),
    [
        ("a.jpg", "", True, "YYYY"),
        ("a.jpg", "summer", False, "summer"),
        ("../elsewhere.jpg", "1985", False, "isn't a photo"),
    ],
    ids=["confirm-blank", "not-a-date", "not-a-photo"],
)
def test_set_entry_rejects_and_writes_nothing(library, file, taken, confirmed, message):
    with pytest.raises(ValueError, match=message):
        set_entry(library, file, taken, confirmed, TODAY)

    assert not truth_file(library).exists()


def test_remove_entry_takes_the_photo_out(library):
    truth_file(library).write_text(HAND_MADE, encoding="utf-8")

    remove_entry(library, "b.jpg")

    assert [entry.file for entry in read_entries(library)[0]] == ["a.jpg"]


def test_remove_entry_unknown_photo_raises(library):
    with pytest.raises(ValueError, match="no known date"):
        remove_entry(library, "a.jpg")


@pytest.mark.parametrize(
    ("content", "unconfirmed"),
    [("file,date\na.jpg,1985\n", 0), ("file,date,confirmed\na.jpg,1985,\nb.jpg,1990,yes\n", 1)],
    ids=["no-column", "one-unconfirmed"],
)
def test_load_truth_counts_unconfirmed_dates_only_where_recorded(library, content, unconfirmed):
    truth_file(library).write_text(content, encoding="utf-8")

    truth = load_truth(library, None, TODAY)

    assert truth.unconfirmed == unconfirmed


@pytest.mark.parametrize(
    ("file", "taken", "estimates", "expected"),
    [
        ("a.jpg", "", [], "No date"),
        ("a.jpg", "1985-13", [], "Not a usable date"),
        ("a.jpg", "2025-11-27", [], "last year"),
        ("a.jpg", "1985-01-01", [], "placeholder"),
        ("1987 lake.jpg", "1985", [], "name says 1987"),
        ("a.jpg", "1985", ["1988", "1990-05"], "1988, 1990"),
    ],
    ids=["blank", "unusable", "recent", "january-first", "name-disagrees", "models-disagree"],
)
def test_doubts_explains_a_suspect_date(file, taken, estimates, expected):
    found = doubts(Entry(file, taken, False), TODAY, estimates, 2)

    assert len(found) == 1
    assert expected in found[0]


@pytest.mark.parametrize(
    ("file", "taken", "estimates"),
    [
        ("a.jpg", "2025-11-25", []),
        ("a.jpg", "2025-11-26", []),
        ("a.jpg", "1985-01", []),
        ("1987-06-14.jpg", "1987", []),
        ("a.jpg", "1985", ["1987"]),
        ("a.jpg", "1985", ["1985", "1999"]),
        ("a.jpg", "1985", [""]),
    ],
    ids=[
        "over-a-year-old",
        "exactly-a-year-old",
        "month",
        "name-agrees",
        "model-at-limit",
        "one-model-agrees",
        "no-answer",
    ],
)
def test_doubts_accepts_a_plausible_date(file, taken, estimates):
    found = doubts(Entry(file, taken, False), TODAY, estimates, 2)

    assert found == []
