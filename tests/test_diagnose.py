import threading

import anthropic
import httpx2
import pytest
from conftest import FakeApi, client_for

from exif_dater import diagnose as diagnosis
from exif_dater.claude import BILLING_URL
from exif_dater.diagnose import check_credit, claude_cli, diagnose, sign_in

REJECTED = (401, {"type": "authentication_error", "message": "invalid x-api-key"})
LOW_CREDIT = (400, {"type": "invalid_request_error", "message": "Your credit balance is too low."})


@pytest.fixture
def fake_ant(tmp_path, monkeypatch):
    """Return a factory that puts a stand-in "ant" command alone on the PATH."""

    def install(login: str = "echo opened", version: str = "ant 2.14.0") -> None:
        folder = tmp_path / "bin"
        folder.mkdir(exist_ok=True)
        script = folder / "ant"
        script.write_text(f'#!/bin/sh\n[ "$1" = --version ] && {{ echo "{version}"; exit 0; }}\n{login}\n')
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
        monkeypatch.setenv("HOME", str(tmp_path))

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return install


def finding(findings, title):
    return next(found for found in findings if found.title == title)


def test_diagnose_working_setup_reports_sign_in_ok_and_sends_nothing_billable(tmp_path):
    (tmp_path / "a.jpg").touch()
    api = FakeApi()

    found = diagnose(tmp_path, client_for(api))

    assert finding(found, "Claude sign-in").status == "ok"
    assert finding(found, "Photo folder").status == "ok"
    assert api.requests == []


def test_diagnose_missing_tool_gives_the_install_command(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda tool, path=None: None)

    found = finding(diagnose(tmp_path, client_for(FakeApi())), "ExifTool")

    assert (found.status, found.command) == ("problem", "sudo apt install libimage-exiftool-perl")


def test_diagnose_folder_without_photos_warns(tmp_path):
    found = diagnose(tmp_path, client_for(FakeApi()))

    assert finding(found, "Photo folder").status == "warning"


def test_diagnose_rejected_sign_in_offers_to_sign_in(tmp_path, fake_ant):
    fake_ant()

    found = finding(diagnose(tmp_path, client_for(FakeApi(error=REJECTED))), "Claude sign-in")

    assert (found.status, found.action) == ("problem", "sign-in")
    assert "invalid x-api-key" in found.detail


def test_diagnose_rejected_key_in_environment_explains_that_it_overrides(tmp_path, fake_ant, monkeypatch):
    fake_ant()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    found = finding(diagnose(tmp_path, client_for(FakeApi(error=REJECTED))), "Claude sign-in")

    assert (found.action, found.command) == ("", "./setup.sh")
    assert "ANTHROPIC_API_KEY" in found.detail


def test_diagnose_unreachable_api_reports_the_connection(tmp_path):
    def refuse(request):
        raise httpx2.ConnectError("refused", request=request)

    client = anthropic.Anthropic(
        api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(refuse))
    )

    found = finding(diagnose(tmp_path, client), "Claude sign-in")

    assert found.status == "problem"
    assert "internet" in found.detail


@pytest.mark.parametrize(
    ("credit", "status"),
    [(None, "unknown"), ("", "ok"), ("Your credit balance is too low.", "problem")],
    ids=["untested", "accepted", "refused"],
)
def test_diagnose_credit_reports_what_is_known(tmp_path, credit, status):
    found = finding(diagnose(tmp_path, client_for(FakeApi()), credit), "API credit")

    assert found.status == status


def test_diagnose_billing_problem_links_the_billing_page(tmp_path):
    found = finding(diagnose(tmp_path, client_for(FakeApi()), "Payment failed."), "API credit")

    assert (found.link, found.action) == (BILLING_URL, "check-credit")


def test_diagnose_failed_analysis_offers_a_retry(tmp_path):
    found = diagnose(tmp_path, client_for(FakeApi()), failed_run="2 photos failed", can_retry=True)

    assert finding(found, "The last run didn't finish").action == "retry"


def test_check_credit_accepted_request_returns_nothing():
    api = FakeApi()

    problem = check_credit(client_for(api))

    assert (problem, api.requests[0]["max_tokens"]) == ("", 1)


def test_check_credit_low_balance_returns_the_explanation():
    problem = check_credit(client_for(FakeApi(error=LOW_CREDIT)))

    assert problem == "Your credit balance is too low."


def test_check_credit_other_failure_raises():
    with pytest.raises(RuntimeError, match="couldn't check"):
        check_credit(client_for(FakeApi(error=REJECTED)))


def test_claude_cli_ignores_apache_ant(fake_ant):
    fake_ant(version="Apache Ant(TM) version 1.10.15")

    assert claude_cli() is None


def test_sign_in_runs_the_login_and_forwards_its_output(fake_ant):
    fake_ant(login='echo "$1 $2"')
    log = []

    sign_in(log.append, threading.Event())

    assert "auth login" in log


def test_sign_in_failed_login_raises(fake_ant):
    fake_ant(login="exit 1")

    with pytest.raises(RuntimeError, match="didn't finish"):
        sign_in(lambda line: None, threading.Event())


def test_sign_in_stopped_ends_the_login(fake_ant):
    fake_ant(login="exec sleep 30")
    stop = threading.Event()
    stop.set()

    with pytest.raises(RuntimeError, match="cancelled"):
        sign_in(lambda line: None, stop)


def test_sign_in_without_the_command_explains(monkeypatch):
    monkeypatch.setattr(diagnosis, "claude_cli", lambda: None)

    with pytest.raises(RuntimeError, match="setup.sh"):
        sign_in(lambda line: None, threading.Event())
