import json

import pytest
from conftest import TODAY, FakeApi, client_for

from exif_dater.evaluate import Evaluation, format_report
from exif_dater.library import Group
from exif_dater.truth import Truth, load_truth

CHEAP = "claude-haiku-5-5"
STRONG = "claude-opus-5-5"


def guess(date, earliest=1983, latest=1988, confidence="medium"):
    return {"date": date, "earliest_year": earliest, "latest_year": latest, "confidence": confidence}


@pytest.fixture
def labelled(make_library, write_photo):
    """Return a factory for a library whose photos have the given known dates."""

    def make(**dates):
        files = [f"{name}.jpg" for name in dates]
        for file in files:
            write_photo(file)
        return make_library(), Truth(dict(zip(files, dates.values(), strict=True)), "truth.csv")

    return make


def run(library, truth, api, models=(CHEAP,), within=2, target=80, notes=""):
    evaluation = Evaluation(library, truth, list(models), notes)
    return evaluation.run(client_for(api), within, target, TODAY, lambda line: None)


@pytest.mark.parametrize(
    ("known", "within_count"),
    [
        pytest.param("1986", 1, id="one year off"),
        pytest.param("1987", 1, id="exactly the allowed error"),
        pytest.param("1988", 0, id="one year beyond"),
    ],
)
def test_run_counts_answers_within_the_allowed_years(labelled, known, within_count):
    library, truth = labelled(a=known)

    report = run(library, truth, FakeApi(observations={CHEAP: guess("1985")}))

    assert report.scores[0].within == within_count


def test_run_scores_exact_year_month_range_and_confidence(labelled):
    library, truth = labelled(a="1985-06-01", b="1995")
    api = FakeApi(observations={CHEAP: guess("1985-06", confidence="high")})

    score = run(library, truth, api).scores[0]

    assert (score.exact_year, score.month_correct, score.month_known) == (1, 1, 1)
    assert (score.median_year_error, score.year_in_range) == (5, 1)
    assert score.by_confidence == {"high": [1, 2]}


def test_run_undated_answer_counts_as_a_miss(labelled):
    library, truth = labelled(a="1985")

    score = run(library, truth, FakeApi(observations={CHEAP: guess("")})).scores[0]

    assert (score.dated, score.within, score.median_year_error) == (0, 0, None)


def test_run_recommends_the_cheapest_model_that_meets_the_target(labelled):
    library, truth = labelled(a="1985")

    report = run(library, truth, FakeApi(), models=[STRONG, CHEAP])

    assert report.recommended == CHEAP
    assert report.scores[0].cost_per_100 > report.scores[1].cost_per_100


def test_run_skips_a_cheaper_model_that_misses_the_target(labelled):
    library, truth = labelled(a="1985")
    api = FakeApi(observations={CHEAP: guess("1950")})

    report = run(library, truth, api, models=[CHEAP, STRONG])

    assert report.recommended == STRONG


@pytest.mark.parametrize(("target", "recommended"), [(49, CHEAP), (50, CHEAP), (51, None)])
def test_run_target_is_met_at_exactly_that_percent(labelled, target, recommended):
    library, truth = labelled(a="1985", b="1950")

    report = run(library, truth, FakeApi(), target=target)

    assert report.recommended == recommended


def test_run_does_not_reveal_file_names_to_the_model(labelled):
    library, truth = labelled(**{"1985 secret": "1985"})
    api = FakeApi()

    run(library, truth, api)

    assert "secret" not in json.dumps(api.requests)


def test_run_second_time_reuses_answers_and_spends_nothing(labelled):
    library, truth = labelled(a="1985")
    api = FakeApi()
    run(library, truth, api)

    report = run(library, truth, api)

    assert (len(api.requests), report.spent, report.scores[0].within) == (1, 0, 1)


def test_run_changed_notes_ask_again(labelled):
    library, truth = labelled(a="1985")
    api = FakeApi()
    run(library, truth, api)

    run(library, truth, api, notes="moved house in 1986")

    assert len(api.requests) == 2


def test_run_leaves_the_review_progress_alone(labelled):
    library, truth = labelled(a="1985")
    before = dict(library.observations)

    run(library, truth, FakeApi())

    assert (library.observations, library.groups) == (before, [])


def test_run_declined_requests_are_reported_as_failures(labelled):
    library, truth = labelled(a="1985")

    report = run(library, truth, FakeApi("refusal"))

    assert (report.scores[0].failed, report.recommended) == (1, None)
    assert "failed" in format_report(report)


@pytest.mark.parametrize(("count", "warned"), [(19, True), (20, False)])
def test_run_warns_when_too_few_photos_have_known_dates(labelled, count, warned):
    library, truth = labelled(**{f"p{number}": "1985" for number in range(count)})

    report = run(library, truth, FakeApi())

    assert any("known dates" in warning for warning in report.warnings) == warned


def test_load_truth_reads_truth_csv_in_the_directory(make_library, write_photo):
    library = make_library()
    write_photo("a, b.jpg")
    (library.root / "truth.csv").write_text('﻿file,date\n"a, b.jpg", 1985-06\n', encoding="utf-8")

    truth = load_truth(library, None, TODAY)

    assert (truth.dates, truth.source) == ({"a, b.jpg": "1985-06"}, "truth.csv")


def test_load_truth_without_csv_uses_only_approved_groups(make_library, write_photo):
    library = make_library("a.jpg", "b.jpg")
    write_photo("a.jpg")
    write_photo("b.jpg")
    library.add_groups(["a.jpg", "b.jpg"], [Group(["a.jpg"], date="1985"), Group(["b.jpg"], date="1990")])
    library.review(1, "1985", "", True, TODAY)

    truth = load_truth(library, None, TODAY)

    assert (truth.dates, truth.from_approvals) == ({"a.jpg": "1985"}, True)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        pytest.param("file,date\n", "no known dates", id="empty"),
        pytest.param("name,when\na.jpg,1985\n", "header", id="wrong header"),
        pytest.param("file,date\nmissing.jpg,1985\n", "missing.jpg", id="unknown photo"),
        pytest.param("file,date\na.jpg,summer\n", "summer", id="bad date"),
    ],
)
def test_load_truth_invalid_table_raises(make_library, write_photo, table, message):
    library = make_library()
    write_photo("a.jpg")
    (library.root / "truth.csv").write_text(table, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_truth(library, None, TODAY)
