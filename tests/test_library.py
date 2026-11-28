import pytest
from conftest import TODAY

from exif_dater.library import Group, Library


def group_files(library):
    return [group.files for group in library.groups]


def test_add_groups_flawed_proposal_places_each_file_once(make_library):
    library = make_library("a.jpg", "b.jpg", "c.jpg")
    proposed = [Group(["b.jpg", "a.jpg", "invented.jpg"], approved=True), Group(["a.jpg"])]

    library.add_groups(["a.jpg", "b.jpg", "c.jpg"], proposed)

    assert group_files(library) == [["a.jpg", "b.jpg"], ["c.jpg"]]
    assert not library.groups[0].approved


def test_date_by_name_groups_same_date_and_returns_the_rest(make_library):
    library = make_library()
    files = ["1987 a.jpg", "1987 b.jpg", "1990.jpg", "scan.jpg"]

    remaining = library.date_by_name(files, TODAY)
    library.add_groups(library.ungrouped(), [])

    assert remaining == ["scan.jpg"]
    assert group_files(library) == [["1987 a.jpg", "1987 b.jpg"], ["1990.jpg"]]


def test_review_approval_without_valid_date_raises(make_library):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])

    with pytest.raises(ValueError):
        library.review(1, "summer", "", True, TODAY)

    assert not library.groups[0].approved


def test_review_changed_date_marks_group_unwritten(make_library):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])
    library.groups[0].approved = library.groups[0].written = True

    library.review(1, "1991", "", True, TODAY)

    assert not library.groups[0].written


def test_split_moves_photo_to_own_unapproved_group(make_library):
    library = make_library("a.jpg", "b.jpg")
    library.add_groups(["a.jpg", "b.jpg"], [Group(["a.jpg", "b.jpg"], date="1985")])

    library.split("b.jpg")

    assert group_files(library) == [["a.jpg"], ["b.jpg"]]
    moved = library.groups[1]
    assert (moved.id, moved.date, moved.approved) == (2, "1985", False)


def test_split_lone_photo_raises(make_library):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])

    with pytest.raises(ValueError):
        library.split("a.jpg")


def test_save_then_open_restores_progress(make_library):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])
    library.outputs.add("a.jpg")
    library.save()

    reopened = Library.open(library.root)

    assert reopened == library
