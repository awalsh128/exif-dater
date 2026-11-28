from datetime import date

import pytest
from conftest import TODAY

from exif_dater.dates import date_from_name, parse_photo_date


@pytest.mark.parametrize(
    ("text", "day", "day_known"),
    [
        pytest.param("1987", date(1987, 1, 1), False, id="year fills January first"),
        pytest.param("1987-06", date(1987, 6, 1), False, id="month fills the first"),
        pytest.param("1987-06-14", date(1987, 6, 14), True, id="full date is exact"),
        pytest.param("1826", date(1826, 1, 1), False, id="first year of photography"),
        pytest.param("2026-11-26", TODAY, True, id="today"),
    ],
)
def test_parse_photo_date_valid_text_returns_first_day(text, day, day_known):
    parsed = parse_photo_date(text, TODAY)

    assert (parsed.day, parsed.day_known) == (day, day_known)


@pytest.mark.parametrize(
    "text",
    ["", "1825-12-31", "2026-11-27", "1987-02-30", "06/14/1987", "1987-6", "1987-W24-1"],
)
def test_parse_photo_date_invalid_text_raises(text):
    with pytest.raises(ValueError):
        parse_photo_date(text, TODAY)


@pytest.mark.parametrize(
    ("file", "expected"),
    [
        pytest.param("1987 beach.jpg", "1987", id="year then words"),
        pytest.param("1987-06.jpg", "1987-06", id="year and month"),
        pytest.param("1987-06-14-0002.jpg", "1987-06-14", id="full date with counter"),
        pytest.param("1987_06 14.jpg", "1987-06-14", id="spaces and underscores"),
        pytest.param("1987-123.jpg", "1987", id="year then a three digit counter"),
        pytest.param("1992 Summer/scan001.jpg", "1992", id="folder"),
        pytest.param("1992/1987.jpg", "1987", id="file beats folder"),
        pytest.param("1970/1992/scan.jpg", "1992", id="nearest folder wins"),
        pytest.param("19870614.jpg", None, id="unseparated digits"),
        pytest.param("scan 1987.jpg", None, id="date not leading"),
        pytest.param("1987-45.jpg", None, id="impossible month"),
        pytest.param("2025-11-25.jpg", "2025-11-25", id="a year and a day old"),
        pytest.param("2025-11-26.jpg", None, id="exactly a year old"),
        pytest.param("2025-11-27.jpg", None, id="under a year old"),
        pytest.param("scan001.jpg", None, id="no date"),
    ],
)
def test_date_from_name_returns_the_typed_date(file, expected):
    assert date_from_name(file, TODAY) == expected
