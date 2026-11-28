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
    with AppServer(library, TOKEN, "-08:00", client_for(api), today=lambda: TODAY) as running:
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
    ("path", "name"), [("/", b"<title>"), ("/app.js", b"fetch("), ("/style.css", b"body")]
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
