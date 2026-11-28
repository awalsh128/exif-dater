import shutil
import subprocess

import pytest
from conftest import TODAY

from exif_dater.apply import apply
from exif_dater.library import Group

ZONE = "-08:00"

needs_exiftool = pytest.mark.skipif(not shutil.which("exiftool"), reason="exiftool is not installed")


def read_tags(path, *tags):
    command = ["exiftool", "-s3", *(f"-{tag}" for tag in tags), str(path)]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.splitlines()


@pytest.fixture
def approved_library(make_library):
    def make(taken, *files):
        library = make_library(*files)
        library.add_groups(list(files), [Group(list(files))])
        library.review(1, taken, "At the lake", True, TODAY)
        return library

    return make


@needs_exiftool
def test_apply_approved_group_writes_dated_copies(approved_library, write_photo):
    # A leading dash and brackets check that names aren't read as options or patterns.
    files = ["-a [1].jpg", "sub/b.jpg"]
    library = approved_library("1987-06", *files)
    original = write_photo(files[0])
    write_photo(files[1])

    written, errors = apply(library, ZONE, TODAY)

    assert (written, errors, library.groups[0].written) == (2, [], True)
    tags = read_tags(
        library.root / "dated/sub/b.jpg",
        "EXIF:DateTimeOriginal",
        "EXIF:OffsetTimeOriginal",
        "XMP-dc:Subject",
        "XMP-dc:Description",
    )
    assert tags == ["1987:06:01 12:00:01", ZONE, "date-estimated", "At the lake"]
    assert (library.root / files[0]).read_bytes() == original


@needs_exiftool
def test_apply_corrected_full_date_replaces_copy_without_estimate_keyword(approved_library, write_photo):
    library = approved_library("1987", "a.jpg")
    write_photo("a.jpg")
    apply(library, ZONE, TODAY)
    library.review(1, "1988-07-04", "", True, TODAY)

    written, errors = apply(library, ZONE, TODAY)

    assert (written, errors) == (1, [])
    tags = read_tags(library.root / "dated/a.jpg", "EXIF:DateTimeOriginal", "XMP-dc:Subject")
    assert tags == ["1988:07:04 12:00:00"]


def test_apply_foreign_file_in_output_is_kept(approved_library, write_photo):
    library = approved_library("1987", "a.jpg")
    write_photo("a.jpg")
    foreign = write_photo("dated/a.jpg")

    written, errors = apply(library, ZONE, TODAY)

    assert (written, len(errors), library.groups[0].written) == (0, 1, False)
    assert (library.root / "dated/a.jpg").read_bytes() == foreign


def test_apply_unapproved_group_writes_nothing(make_library):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])

    assert apply(library, ZONE, TODAY) == (0, [])
