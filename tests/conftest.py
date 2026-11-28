import itertools
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import anthropic
import httpx2
import pytest

from exif_dater.library import Library, Observation

TODAY = date(2026, 11, 26)

OBSERVATION = {
    "description": "A lake",
    "printed_date": "",
    "clues": "clothing",
    "date": "1985",
    "earliest_year": 1983,
    "latest_year": 1988,
    "confidence": "low",
}
GROUPS = {
    "groups": [
        {
            "title": "Lake trip",
            "files": ["a.jpg", "b.jpg"],
            "date": "1986",
            "confidence": "medium",
            "reason": "same place",
        }
    ]
}


def _message(stop_reason: str | None) -> dict:
    return {
        "id": "m",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": [],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 1},
    }


class FakeApi:
    """Stands in for the Messages API and records the request bodies it receives."""

    def __init__(self, stop_reason="end_turn", observations=None, error=None, successes=0):
        self.error = error  # (status, error object) returned once the successes are used up.
        self.successes = successes
        self._count = itertools.count()
        self.stop_reason = stop_reason
        self.observations = observations or {}  # Per model, overriding OBSERVATION.
        self.requests = []

    def __call__(self, request):
        # A model lookup is free and has no body, so it isn't recorded.
        body = json.loads(request.content) if request.content else None
        if body is not None:
            self.requests.append(body)
        if self.error and next(self._count) >= self.successes:
            status, error = self.error
            # Keeps the client from retrying, so each request is recorded once.
            headers = {"x-should-retry": "false"}
            return httpx2.Response(status, headers=headers, json={"type": "error", "error": error})
        if body is None:
            model = {"id": "m", "type": "model", "display_name": "m", "created_at": "2026-01-01T00:00:00Z"}
            return httpx2.Response(200, json=model)
        if not body.get("stream"):
            return httpx2.Response(200, json=_message("max_tokens"))
        wants_groups = "groups" in body["output_config"]["format"]["schema"]["properties"]
        observation = OBSERVATION | self.observations.get(body["model"], {})
        text = json.dumps(GROUPS if wants_groups else observation)
        events = [
            {"type": "message_start", "message": _message(None)},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
            {"type": "message_stop"},
        ]
        stream = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stream.encode())


def client_for(api: FakeApi) -> anthropic.Anthropic:
    http_client = httpx2.Client(transport=httpx2.MockTransport(api))
    return anthropic.Anthropic(api_key="test-key", http_client=http_client)


@pytest.fixture
def make_library(tmp_path):
    """Return a factory for a library that has observed the given files."""

    def make(*files: str) -> Library:
        library = Library.open(tmp_path)
        for file in files:
            library.observations[file] = Observation(file, "", "", "1990", 1990, 1990, "low")
        return library

    return make


@pytest.fixture
def write_photo(tmp_path):
    """Return a factory that creates a small JPEG under the temporary directory and returns its bytes."""
    if not shutil.which("magick"):
        pytest.skip("magick is not installed")

    def write(file: str) -> bytes:
        path = Path(tmp_path) / file
        path.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.run(
            ["magick", "-size", "64x48", "xc:gray", "jpg:-"], capture_output=True, check=True
        ).stdout
        path.write_bytes(data)
        return data

    return write
