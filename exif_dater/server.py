"""The local web front end: analyze, approve and write dates, check known dates, test models and diagnose."""

import hmac
import json
import threading
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import anthropic

from exif_dater import prices
from exif_dater.analyze import analyze, needs_requests, new_photos, unnamed_photos
from exif_dater.apply import apply
from exif_dater.claude import DEFAULT_MODEL, Analyzer, billing_problem, has_credentials
from exif_dater.diagnose import check_credit, diagnose, sign_in
from exif_dater.evaluate import Evaluation, cached_dates
from exif_dater.jobs import Job, Work
from exif_dater.library import Library
from exif_dater.photos import OUTPUT_DIR, list_photos, preview
from exif_dater.truth import check_entries, load_truth, remove_entry, set_entry

THUMBNAIL_EDGE = 640
# For the viewer: enough to zoom in on a printed date stamp.
VIEWER_EDGE = 3000

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
_WEB = files("exif_dater").joinpath("web")
# The page's own files, the only ones served without a token. None of them holds anything private.
_ASSETS = {f"/{file.name}": file.name for file in _WEB.iterdir() if Path(file.name).suffix in _CONTENT_TYPES}
_ASSETS["/"] = "index.html"


class AppServer(ThreadingHTTPServer):
    """Serves the front end on the loopback interface only, because it shows private photos.

    Every request for data must carry the token, which stops other web sites open in the same browser
    from reading photos, approving dates or spending money.
    """

    daemon_threads = False  # Closing the server lets a write that is in progress finish.

    def __init__(
        self,
        library: Library,
        token: str,
        zone: str,
        client: anthropic.Anthropic,
        notes: str = "",
        today: Callable[[], date] = date.today,
        connect: Callable[[], anthropic.Anthropic] = anthropic.Anthropic,
    ) -> None:
        super().__init__(("127.0.0.1", 0), _AppRequest)
        self.library = library
        self.token = token
        self.zone = zone
        self.client = client
        self.notes = notes
        self.today = today
        self.connect = connect  # Makes a client that sees the current sign-in.
        self.job: Job | None = None
        self.credit: str | None = None  # As diagnose() takes it.
        self.lock = threading.Lock()  # Guards library and job.

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/?token={self.token}"

    def reconnect(self) -> None:
        """Pick up a sign-in made since the client was created, in this app or in a terminal."""
        try:
            self.client = self.connect()
        except anthropic.AnthropicError:
            pass  # The old client stays, and its next request reports what is wrong.

    def start(self, kind: str, work: Work) -> dict:
        """Run work in the background, noting whether it ran out of credit."""

        def noting_credit(log: Callable[[str], None], stop: threading.Event) -> object:
            try:
                return work(log, stop)
            except Exception as error:
                self.credit = billing_problem(error) or billing_problem(error.__cause__)
                raise

        self.credit = None
        self.job = Job(kind, noting_credit)
        return self.job.state()

    def server_close(self) -> None:
        if self.job:
            # Requests already sent finish and are saved; no new ones start.
            self.job.stop.set()
            self.job.wait()
        super().server_close()


class _AppRequest(BaseHTTPRequestHandler):
    server: AppServer

    def do_GET(self) -> None:
        path, query = self._target()
        if path in _ASSETS:
            name = _ASSETS[path]
            self._reply(_WEB.joinpath(name).read_bytes(), _CONTENT_TYPES[Path(name).suffix])
        elif not self._authorized(query):
            self.send_error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        elif path == "/api/state":
            with self.server.lock:
                state = self._state()
            self._reply_json(state)
        elif path == "/api/job":
            job = self.server.job
            self._reply_json(job.state() if job else None)
        elif path == "/api/diagnose":
            self._reply_json(self._diagnosis())
        elif path == "/api/truth":
            try:
                with self.server.lock:
                    known = self._known_dates(query)
            except (OSError, ValueError) as error:
                self._reply_error(str(error), HTTPStatus.BAD_REQUEST)
                return
            self._reply_json(known)
        elif path == "/thumbnail":
            edge = VIEWER_EDGE if "large" in query else THUMBNAIL_EDGE
            self._thumbnail(query.get("file", ""), edge)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path, query = self._target()
        if not self._authorized(query):
            self.send_error(HTTPStatus.FORBIDDEN, "missing or wrong token")
            return
        actions = {
            "/api/review": self._review,
            "/api/split": self._split,
            "/api/apply": self._apply,
            "/api/analyze": self._analyze,
            "/api/evaluate": self._evaluate,
            "/api/cancel": self._cancel,
            "/api/sign-in": self._sign_in,
            "/api/check-credit": self._check_credit,
            "/api/truth": self._set_truth,
            "/api/truth/remove": self._remove_truth,
        }
        if path not in actions:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            with self.server.lock:
                busy = self.server.job is not None and self.server.job.running
                if busy and path != "/api/cancel":
                    self._reply_error("wait for the running task to finish", HTTPStatus.CONFLICT)
                    return
                result = actions[path](body)
            self._reply_json(result)
        except (KeyError, TypeError, ValueError, OSError, RuntimeError) as error:
            self._reply_error(str(error), HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        pass  # Request lines contain the token, and the page reports its own errors.

    # The actions run while holding the server's lock.

    def _review(self, body: dict) -> dict:
        self.server.library.review(
            body["id"], body["date"], body.get("description", ""), body["approved"], self.server.today()
        )
        self.server.library.save()
        return self._state()

    def _split(self, body: dict) -> dict:
        self.server.library.split(body["file"])
        self.server.library.save()
        return self._state()

    def _apply(self, body: dict) -> dict:
        library = self.server.library
        written, errors = apply(library, self.server.zone, self.server.today())
        return {
            "written": written,
            "folder": str(library.root / OUTPUT_DIR),
            "error": "\n".join(errors),
            "state": self._state(),
        }

    def _analyze(self, body: dict) -> dict:
        server = self.server
        library = server.library
        model = self._listed(body["model"])
        new = new_photos(library)
        if needs_requests(library, unnamed_photos(new, server.today())):
            self._require_credentials()

        def work(log: Callable[[str], None], stop: threading.Event) -> None:
            unnamed = library.date_by_name(new, server.today())
            library.save()
            log(f"dated {len(new) - len(unnamed)} photos from their names")
            analyzer = Analyzer(server.client, model, server.notes)
            try:
                analyze(analyzer, library, unnamed, log, stop)
            finally:
                log(f"tokens used: {analyzer.input_tokens} input, {analyzer.output_tokens} output")

        return server.start("analyze", work)

    def _evaluate(self, body: dict) -> dict:
        server = self.server
        models = [self._listed(model) for model in body["models"]]
        truth = load_truth(server.library, None, server.today())
        evaluation = Evaluation(server.library, truth, models, server.notes)
        if evaluation.pending():
            self._require_credentials()
        within, target = int(body["within"]), int(body["target"])

        def work(log: Callable[[str], None], stop: threading.Event) -> dict:
            report = evaluation.run(server.client, within, target, server.today(), log, stop)
            return asdict(report)

        return server.start("evaluate", work)

    def _cancel(self, body: dict) -> dict | None:
        if not self.server.job:
            return None
        self.server.job.stop.set()
        return self.server.job.state()

    def _sign_in(self, body: dict) -> dict:
        server = self.server

        def work(log: Callable[[str], None], stop: threading.Event) -> None:
            sign_in(log, stop)
            server.reconnect()

        return server.start("sign-in", work)

    def _check_credit(self, body: dict) -> list[dict]:
        self.server.reconnect()
        self.server.credit = check_credit(self.server.client)
        return self._diagnosis()

    # Not an action: it changes nothing the lock guards, and its API check shouldn't hold up the page.

    def _diagnosis(self) -> list[dict]:
        server = self.server
        job = server.job.state() if server.job else None
        if not job or not job["running"]:
            server.reconnect()
        failed = job["error"] if job and not job["running"] else ""
        # A billing failure is already reported as a credit problem.
        if server.credit:
            failed = ""
        findings = diagnose(
            server.library.root,
            server.client,
            server.credit,
            failed,
            can_retry=bool(job) and job["kind"] == "analyze",
        )
        return [asdict(finding) for finding in findings]

    def _set_truth(self, body: dict) -> dict:
        server = self.server
        set_entry(server.library, body["file"], body["date"], bool(body["confirmed"]), server.today())
        return self._known_dates(body)

    def _remove_truth(self, body: dict) -> dict:
        remove_entry(self.server.library, body["file"])
        return self._known_dates(body)

    def _known_dates(self, choices: dict) -> dict:
        server = self.server

        def estimates(photos: list[str]) -> dict[str, list[str]]:
            return cached_dates(server.library, photos, server.notes)

        within = int(choices.get("within", 2))
        return check_entries(server.library, server.today(), within, estimates)

    def _listed(self, model: str) -> str:
        # The page offers only priced models, so its cost estimates always apply.
        if model not in prices.LISTED_MODELS:
            raise ValueError(f"unknown model {model!r}")
        return model

    def _require_credentials(self) -> None:
        if not has_credentials(self.server.client):
            self.server.reconnect()
        if not has_credentials(self.server.client):
            raise ValueError("no Claude API credentials, use the help button (?) to sign in")

    def _state(self) -> dict:
        server = self.server
        library = server.library
        new = new_photos(library)
        try:
            truth = load_truth(library, None, server.today())
            known = {"source": truth.source, "count": len(truth.dates), "error": ""}
        except (OSError, ValueError) as error:
            known = {"source": "", "count": 0, "error": str(error)}
        return {
            "groups": [asdict(group) for group in library.groups],
            "new_photos": len(new),
            "photos_to_send": len(unnamed_photos(new, server.today())),
            "default_model": DEFAULT_MODEL,
            "default_comparison": prices.DEFAULT_COMPARISON,
            "models": [
                {"id": model, "cost_per_photo": prices.estimated_cost(model, 1)}
                for model in prices.LISTED_MODELS
            ],
            "truth": known,
            "job": server.job.state() if server.job else None,
        }

    def _target(self) -> tuple[str, dict[str, str]]:
        parts = urlsplit(self.path)
        return parts.path, {name: values[0] for name, values in parse_qs(parts.query).items()}

    def _authorized(self, query: dict[str, str]) -> bool:
        return hmac.compare_digest(query.get("token", "").encode(), self.server.token.encode())

    def _thumbnail(self, file: str, edge: int) -> None:
        # Only photos found in the directory, so a request can't name any other file.
        if file not in list_photos(self.server.library.root):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            jpeg = preview(self.server.library.root / file, edge)
        except (OSError, RuntimeError):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "cannot render this photo")
            return
        self._reply(jpeg, "image/jpeg")

    def _reply_json(self, value: object) -> None:
        self._reply(json.dumps(value).encode(), "application/json")

    def _reply_error(self, message: str, status: HTTPStatus) -> None:
        self._reply(message.encode(), "text/plain; charset=utf-8", status)

    def _reply(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
