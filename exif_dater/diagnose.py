"""Find what stops the app from working, and fix what can be fixed from the page. No model is asked."""

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anthropic

from exif_dater.claude import BILLING_URL, DEFAULT_MODEL, billing_problem, has_credentials
from exif_dater.photos import list_photos

# The name and Ubuntu package of each program the app runs, by command.
TOOLS = {"magick": ("ImageMagick", "imagemagick"), "exiftool": ("ExifTool", "libimage-exiftool-perl")}
# Long enough for a slow connection, short enough that the help panel doesn't appear stuck.
CHECK_SECONDS = 15


@dataclass
class Finding:
    """One thing that was checked, and what to do if it's wrong."""

    title: str
    status: str  # "ok", "unknown", "warning" or "problem".
    detail: str = ""
    command: str = ""  # For the user to run in a terminal.
    link: str = ""
    action: str = ""  # A fix the page can run: "sign-in", "check-credit" or "retry".


def diagnose(
    root: Path,
    client: anthropic.Anthropic,
    credit: str | None = None,
    failed_run: str = "",
    can_retry: bool = False,
) -> list[Finding]:
    """Check the tools, the photo directory, the sign-in, the credit and the last run.

    credit is None when nothing is known, empty after a successful credit check, and otherwise the API's
    explanation of why it wouldn't bill the account. Nothing checked here costs money.
    """
    findings = [_tool(command) for command in TOOLS]
    findings += [_directory(root), _sign_in(client), _credit(credit)]
    if failed_run:
        findings.append(
            Finding("The last run didn't finish", "warning", failed_run, action="retry" if can_retry else "")
        )
    return findings


def check_credit(client: anthropic.Anthropic) -> str:
    """Send the smallest possible paid request. Return "" if it was billed, else why it couldn't be.

    The API has no free way to ask whether an account can pay. This costs far less than a cent.
    """
    try:
        client.with_options(timeout=CHECK_SECONDS, max_retries=0).messages.create(
            model=DEFAULT_MODEL, max_tokens=1, messages=[{"role": "user", "content": "Hi"}]
        )
    except anthropic.AnthropicError as error:
        problem = billing_problem(error)
        if problem:
            return problem
        raise RuntimeError(f"couldn't check the credit: {error}") from error
    return ""


def claude_cli() -> str | None:
    """Return the path of Anthropic's "ant" command, which signs in through the browser."""
    # "ant" is also the name of Apache's Java build tool, so each candidate is asked what it is.
    for candidate in (shutil.which("ant"), shutil.which("ant", path=str(Path.home() / ".local/bin"))):
        if candidate:
            version = subprocess.run([candidate, "--version"], check=False, capture_output=True, text=True)
            if "apache ant" not in (version.stdout + version.stderr).lower():
                return candidate
    return None


def sign_in(log: Callable[[str], None], stop: threading.Event) -> None:
    """Run the browser sign-in until it finishes or stop is set."""
    command = claude_cli()
    if not command:
        raise RuntimeError("the ant command isn't installed, run ./setup.sh")
    log("Finish signing in on the page that opens in your browser.")
    process = subprocess.Popen(
        [command, "auth", "login"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def forward() -> None:
        for line in process.stdout:
            log(line.rstrip())

    # A browser started by the sign-in can hold the pipe open, so nothing waits long for this thread.
    reader = threading.Thread(target=forward, daemon=True)
    reader.start()
    while process.poll() is None:
        if stop.wait(0.2):
            process.terminate()
            break
    process.wait()
    reader.join(timeout=1)
    if stop.is_set():
        raise RuntimeError("sign-in cancelled")
    if process.returncode != 0:
        raise RuntimeError("the sign-in didn't finish")
    log("Signed in.")


def _tool(command: str) -> Finding:
    name, package = TOOLS[command]
    if shutil.which(command):
        return Finding(name, "ok", "Installed.")
    return Finding(name, "problem", "Not installed.", command=f"sudo apt install {package}")


def _directory(root: Path) -> Finding:
    title = "Photo folder"
    if not os.access(root, os.W_OK):
        return Finding(
            title, "problem", f"{root} can't be written to, so progress and dated copies can't be saved."
        )
    count = len(list_photos(root))
    if not count:
        return Finding(title, "warning", f"No photos found in {root}. Start the app with your scans' folder.")
    # Analyzing sends every one of these, so the number is worth a look.
    return Finding(title, "ok", f"{count} photos in {root}.")


def _sign_in(client: anthropic.Anthropic) -> Finding:
    title = "Claude sign-in"
    if not has_credentials(client):
        return _signed_out(title, "You aren't signed in.")
    try:
        client.with_options(timeout=CHECK_SECONDS, max_retries=0).models.retrieve(DEFAULT_MODEL)
    except anthropic.AuthenticationError as error:
        return _signed_out(title, f"The API rejected your sign-in: {error.api_message or error}")
    except anthropic.APIConnectionError:
        return Finding(
            title, "problem", "Can't reach the Claude API. Check your internet connection, then check again."
        )
    except anthropic.AnthropicError as error:
        return Finding(title, "problem", f"The sign-in check failed: {error}")
    return Finding(title, "ok", f"Signed in, and {DEFAULT_MODEL} is available.")


def _signed_out(title: str, detail: str) -> Finding:
    if os.environ.get("ANTHROPIC_API_KEY"):
        # The browser sign-in can't help while the variable takes priority over it.
        return Finding(
            title,
            "problem",
            f"{detail} ANTHROPIC_API_KEY is set and overrides browser sign-in. Remove it from your shell "
            "or replace the saved key, then restart the app.",
            command="./setup.sh",
        )
    if claude_cli():
        return Finding(title, "problem", detail, action="sign-in")
    return Finding(title, "problem", detail, command="./setup.sh")


def _credit(credit: str | None) -> Finding:
    title = "API credit"
    if credit:
        detail = (
            f"{credit} Add credit in the organization you signed in to, then test it. Max, Ultra and Team "
            "plans include credit that you have to claim: see below."
        )
        return Finding(title, "problem", detail, link=BILLING_URL, action="check-credit")
    if credit == "":
        return Finding(title, "ok", "A test request was accepted.")
    return Finding(title, "unknown", "Not tested.", link=BILLING_URL, action="check-credit")
