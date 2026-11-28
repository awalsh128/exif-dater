import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import TODAY, FakeApi, client_for

from exif_dater.library import Library
from exif_dater.server import AppServer

TOKEN = "secret"


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def server(make_library, api):
    library = make_library("a.jpg")
    library.add_groups(["a.jpg"], [])
    client = client_for(api)
    with AppServer(
        library, TOKEN, "-08:00", client, today=lambda: TODAY, connect=lambda: client_for(api)
    ) as running:
        thread = threading.Thread(target=running.serve_forever)
        thread.start()
        yield running
        running.shutdown()
        thread.join()


def send(server, path, body=None):
    host, port = server.server_address
    data = None if body is None else json.dumps(body).encode()
    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", data) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/state", None),
        ("/api/job", None),
        ("/api/diagnose", None),
        ("/api/check-credit", {}),
        ("/api/sign-in", {}),
        ("/api/truth", None),
        ("/api/truth", {"file": "a.jpg", "date": "1985", "confirmed": True}),
        ("/api/truth/remove", {"file": "a.jpg"}),
        ("/thumbnail?file=a.jpg&token=wrong", None),
        ("/api/apply", {}),
        ("/api/analyze", {"model": "claude-haiku-5-5"}),
    ],
)
def test_request_without_token_is_forbidden(server, path, body):
    status, _ = send(server, path, body)

    assert status == 403


def test_review_approval_is_saved(server):
    decision = {"id": 1, "date": "1986-05", "approved": True}

    status, _ = send(server, f"/api/review?token={TOKEN}", decision)

    saved = Library.open(server.library.root).groups[0]
    assert (status, saved.approved, saved.date) == (200, True, "1986-05")


def test_review_invalid_date_is_bad_request(server):
    decision = {"id": 1, "date": "summer", "approved": True}

    status, message = send(server, f"/api/review?token={TOKEN}", decision)

    assert status == 400
    assert b"summer" in message


def test_thumbnail_of_unknown_file_is_not_found(server):
    status, _ = send(server, f"/thumbnail?file=../../etc/passwd&token={TOKEN}")

    assert status == 404


def finished_job(server):
    server.job.wait()
    return json.loads(send(server, f"/api/job?token={TOKEN}")[1])


@pytest.mark.parametrize(
    ("path", "name"),
    [("/", b"<title>"), ("/app.js", b"fetch("), ("/style.css", b"body"), ("/icon.svg", b"<svg")],
)
def test_page_assets_are_served(server, path, name):
    status, body = send(server, path)

    assert status == 200
    assert name in body


def test_analyze_new_photo_runs_in_the_background(server, write_photo):
    write_photo("new.jpg")

    status, _ = send(server, f"/api/analyze?token={TOKEN}", {"model": "claude-haiku-5-5"})
    job = finished_job(server)

    assert (status, job["running"], job["error"]) == (200, False, "")
    assert "new.jpg" in Library.open(server.library.root).observations


def test_analyze_unlisted_model_is_bad_request(server, api):
    status, _ = send(server, f"/api/analyze?token={TOKEN}", {"model": "made-up"})

    assert (status, api.requests) == (400, [])


def test_evaluate_returns_a_report(server, write_photo):
    write_photo("a.jpg")
    (server.library.root / "truth.csv").write_text("file,date\na.jpg,1985\n", encoding="utf-8")
    choice = {"models": ["claude-haiku-5-5"], "within": 2, "target": 80}

    status, _ = send(server, f"/api/evaluate?token={TOKEN}", choice)
    job = finished_job(server)

    assert (status, job["error"]) == (200, "")
    assert job["result"]["recommended"] == "claude-haiku-5-5"


def test_change_while_a_task_runs_is_a_conflict(server, api, write_photo):
    write_photo("new.jpg")
    release = threading.Event()
    answer = api.__call__
    # Holds the model's reply back so the task is still running during the second request.
    server.client = client_for(lambda request: release.wait(5) and answer(request))
    send(server, f"/api/analyze?token={TOKEN}", {"model": "claude-haiku-5-5"})

    status, _ = send(server, f"/api/review?token={TOKEN}", {"id": 1, "date": "1986", "approved": True})
    release.set()
    finished_job(server)

    assert status == 409


LOW_CREDIT = (400, {"type": "invalid_request_error", "message": "Your credit balance is too low."})


def diagnosed(server, title):
    status, body = send(server, f"/api/diagnose?token={TOKEN}")
    assert status == 200
    return next((found for found in json.loads(body) if found["title"] == title), None)


def test_diagnose_after_a_billing_failure_reports_the_credit(server, api, write_photo):
    write_photo("new.jpg")
    api.error = LOW_CREDIT
    send(server, f"/api/analyze?token={TOKEN}", {"model": "claude-haiku-5-5"})
    finished_job(server)

    credit = diagnosed(server, "API credit")

    assert credit["status"] == "problem"
    assert diagnosed(server, "The last run didn't finish") is None


def test_check_credit_after_adding_it_clears_the_problem(server, api):
    server.credit = "Your credit balance is too low."

    status, body = send(server, f"/api/check-credit?token={TOKEN}", {})

    credit = next(found for found in json.loads(body) if found["title"] == "API credit")
    assert (status, credit["status"], len(api.requests)) == (200, "ok", 1)


def test_sign_in_runs_as_a_task_and_reconnects(server, monkeypatch):
    monkeypatch.setattr("exif_dater.server.sign_in", lambda log, stop: log("Signed in."))
    before = server.client

    status, _ = send(server, f"/api/sign-in?token={TOKEN}", {})
    job = finished_job(server)

    assert (status, job["kind"], job["error"]) == (200, "sign-in", "")
    assert server.client is not before


def known_entry(reply, file):
    return next((entry for entry in json.loads(reply)["entries"] if entry["file"] == file), None)


def test_truth_lists_known_dates_and_the_photos_without_one(server, write_photo):
    write_photo("a.jpg")
    write_photo("b.jpg")
    (server.library.root / "truth.csv").write_text("file,date\na.jpg,\ngone.jpg,1985\n", encoding="utf-8")

    status, reply = send(server, f"/api/truth?token={TOKEN}")

    assert status == 200
    assert json.loads(reply)["unlisted"] == ["b.jpg"]
    assert "No date" in known_entry(reply, "a.jpg")["doubts"][0]
    assert known_entry(reply, "gone.jpg")["present"] is False


def test_truth_after_a_test_doubts_a_date_no_model_agrees_with(server, write_photo):
    write_photo("a.jpg")
    (server.library.root / "truth.csv").write_text("file,date\na.jpg,1960\n", encoding="utf-8")
    send(server, f"/api/evaluate?token={TOKEN}", {"models": ["claude-haiku-5-5"], "within": 2, "target": 80})
    finished_job(server)

    _, reply = send(server, f"/api/truth?within=2&token={TOKEN}")

    assert "1985" in known_entry(reply, "a.jpg")["doubts"][0]


def test_truth_confirmation_is_saved(server, write_photo):
    write_photo("a.jpg")
    decision = {"file": "a.jpg", "date": "1985-06", "confirmed": True}

    status, reply = send(server, f"/api/truth?token={TOKEN}", decision)

    assert (status, known_entry(reply, "a.jpg")["confirmed"]) == (200, True)
    assert "a.jpg,1985-06,yes" in (server.library.root / "truth.csv").read_text(encoding="utf-8")


def test_truth_remove_drops_the_photo(server, write_photo):
    write_photo("a.jpg")
    (server.library.root / "truth.csv").write_text("file,date\na.jpg,1985\n", encoding="utf-8")

    status, reply = send(server, f"/api/truth/remove?token={TOKEN}", {"file": "a.jpg"})

    assert (status, known_entry(reply, "a.jpg")) == (200, None)


def test_truth_file_without_a_header_is_bad_request(server):
    (server.library.root / "truth.csv").write_text("a.jpg,1985\n", encoding="utf-8")

    status, message = send(server, f"/api/truth?token={TOKEN}")

    assert status == 400
    assert b"header" in message


@pytest.mark.parametrize("size", ["", "&large=1"], ids=["thumbnail", "large"])
def test_thumbnail_of_a_photo_not_yet_analyzed_is_served(server, write_photo, size):
    write_photo("new.jpg")

    status, body = send(server, f"/thumbnail?file=new.jpg{size}&token={TOKEN}")

    assert (status, body[:2]) == (200, b"\xff\xd8")
