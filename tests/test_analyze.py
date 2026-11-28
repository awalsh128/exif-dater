import threading

import anthropic
import pytest
from conftest import TODAY, FakeApi, client_for

from exif_dater.analyze import PARALLEL_REQUESTS, analyze
from exif_dater.claude import DEFAULT_MODEL, Analyzer
from exif_dater.cli import main
from exif_dater.library import Library
from exif_dater.photos import list_photos


def analyzer_for(api, model=DEFAULT_MODEL):
    return Analyzer(client_for(api), model)


def test_analyze_new_photos_saves_observations_and_groups(make_library, write_photo):
    library = make_library()
    write_photo("a.jpg")
    write_photo("b.jpg")
    api = FakeApi()
    analyzer = analyzer_for(api)

    analyze(analyzer, library, ["a.jpg", "b.jpg"], lambda line: None)

    saved = Library.open(library.root)
    assert (len(saved.observations), [group.date for group in saved.groups]) == (2, ["1986"])
    assert (len(api.requests), analyzer.input_tokens, analyzer.output_tokens) == (3, 30, 15)


def test_analyze_name_dated_photos_sends_no_request(make_library):
    library = make_library()
    api = FakeApi()
    remaining = library.date_by_name(["1987 a.jpg", "1990.jpg"], TODAY)

    analyze(analyzer_for(api), library, remaining, lambda line: None)

    assert (api.requests, len(library.groups)) == ([], 2)


def test_analyze_declined_photo_raises_and_stays_new(make_library, write_photo):
    library = make_library()
    write_photo("a.jpg")

    with pytest.raises(RuntimeError, match="1 photos failed"):
        analyze(analyzer_for(FakeApi("refusal")), library, ["a.jpg"], lambda line: None)

    assert "a.jpg" not in Library.open(library.root).observations


@pytest.mark.parametrize(("model", "expected"), [(DEFAULT_MODEL, None), ("claude-opus-5-5", "default")])
def test_analyze_requests_fallbacks_only_for_models_that_have_them(
    make_library, write_photo, model, expected
):
    library = make_library()
    write_photo("a.jpg")
    api = FakeApi()

    analyze(analyzer_for(api, model), library, ["a.jpg"], lambda line: None)

    assert api.requests[0].get("fallbacks") == expected


def test_main_declined_confirmation_sends_nothing(tmp_path, write_photo, monkeypatch):
    write_photo("a.jpg")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("builtins.input", lambda question: "n")

    with pytest.raises(SystemExit, match="nothing was sent"):
        main(["analyze", str(tmp_path)])


LOW_CREDIT = (
    400,
    {"type": "invalid_request_error", "message": "Your credit balance is too low to access the API."},
)
MORE_PHOTOS_THAN_WORKERS = [f"{number}.jpg" for number in range(PARALLEL_REQUESTS * 3)]


@pytest.mark.parametrize(
    "error",
    [
        LOW_CREDIT,
        (402, {"type": "billing_error", "message": "Payment failed."}),
        (
            429,
            {
                "type": "rate_limit_error",
                "message": "You have reached your API usage limits.",
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
        ),
    ],
    ids=["low-credit", "billing-error", "spend-cap"],
)
def test_analyze_billing_problem_stops_sending_and_explains(make_library, write_photo, error):
    library = make_library()
    for file in MORE_PHOTOS_THAN_WORKERS:
        write_photo(file)
    api = FakeApi(error=error)
    log = []

    with pytest.raises(RuntimeError, match=f"billing problem: {error[1]['message']}.*settings/billing"):
        analyze(analyzer_for(api), library, MORE_PHOTOS_THAN_WORKERS, log.append)

    # Only requests already under way when the first one failed can have been sent.
    assert 1 <= len(api.requests) <= PARALLEL_REQUESTS
    assert log == []


def test_analyze_billing_problem_midway_keeps_finished_photos(make_library, write_photo):
    library = make_library()
    for file in MORE_PHOTOS_THAN_WORKERS:
        write_photo(file)
    api = FakeApi(error=LOW_CREDIT, successes=1)

    with pytest.raises(RuntimeError, match="billing problem"):
        analyze(analyzer_for(api), library, MORE_PHOTOS_THAN_WORKERS, lambda line: None)

    assert len(Library.open(library.root).observations) == 1


def test_analyze_ordinary_request_error_tries_every_photo(make_library, write_photo):
    library = make_library()
    for file in MORE_PHOTOS_THAN_WORKERS:
        write_photo(file)
    api = FakeApi(error=(400, {"type": "invalid_request_error", "message": "Image is too large."}))

    with pytest.raises(RuntimeError, match=f"{len(MORE_PHOTOS_THAN_WORKERS)} photos failed"):
        analyze(analyzer_for(api), library, MORE_PHOTOS_THAN_WORKERS, lambda line: None)

    assert len(api.requests) == len(MORE_PHOTOS_THAN_WORKERS)


def test_analyze_rejected_sign_in_stops_sending(make_library, write_photo):
    library = make_library()
    for file in MORE_PHOTOS_THAN_WORKERS:
        write_photo(file)
    api = FakeApi(error=(401, {"type": "authentication_error", "message": "invalid key"}))

    with pytest.raises(anthropic.AuthenticationError):
        analyze(analyzer_for(api), library, MORE_PHOTOS_THAN_WORKERS, lambda line: None)

    assert len(api.requests) <= PARALLEL_REQUESTS


def test_list_photos_skips_output_hidden_and_other_files(tmp_path, write_photo):
    for file in ["b.JPG", "sub/a.png", "dated/b.jpg", ".hidden/c.jpg", "d.jpg_original", "notes.txt"]:
        write_photo(file)

    assert list_photos(tmp_path) == ["b.JPG", "sub/a.png"]


def test_main_without_credentials_explains_how_to_set_them(tmp_path, write_photo, monkeypatch):
    write_photo("a.jpg")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        main(["analyze", "--yes", str(tmp_path)])


def test_analyze_stopped_before_starting_sends_nothing(make_library, write_photo):
    library = make_library()
    write_photo("a.jpg")
    api = FakeApi()
    stop = threading.Event()
    stop.set()

    with pytest.raises(RuntimeError, match="stopped"):
        analyze(analyzer_for(api), library, ["a.jpg"], lambda line: None, stop)

    assert api.requests == []


def test_main_accepts_a_separate_negative_time_zone(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool: tool)

    with pytest.raises(SystemExit, match="not a directory"):
        main(["review", str(tmp_path / "missing"), "--tz", "-05:00"])


@pytest.mark.parametrize("zone", ["-8", "-0800", "Z", "+25:00", "pacific"])
def test_main_review_with_a_malformed_time_zone_explains(tmp_path, monkeypatch, zone):
    monkeypatch.setattr("shutil.which", lambda tool: tool)

    with pytest.raises(SystemExit, match="--tz must look like"):
        main(["review", str(tmp_path / "missing"), f"--tz={zone}"])
