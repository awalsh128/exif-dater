import threading

import pytest
from conftest import TODAY, FakeApi, client_for

from exif_dater.analyze import analyze
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
