"""A long task run in the background, whose progress the page polls."""

import threading
from collections.abc import Callable

# Takes a function that records a line of progress, and an event that asks it to stop.
Work = Callable[[Callable[[str], None], threading.Event], object]


class Job:
    def __init__(self, kind: str, work: Work) -> None:
        self.kind = kind
        self.lines: list[str] = []
        self.error = ""
        self.result: object = None
        self.stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(work,))
        self._thread.start()

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def wait(self) -> None:
        self._thread.join()

    def state(self) -> dict:
        # Read first, so a finished job is never reported with half its outcome.
        running = self.running
        return {
            "kind": self.kind,
            "running": running,
            "lines": list(self.lines),
            "error": self.error,
            "result": self.result,
        }

    def _run(self, work: Work) -> None:
        try:
            self.result = work(self.lines.append, self.stop)
        except Exception as error:  # noqa: BLE001 - anything that ends the thread has to reach the page.
            self.error = str(error)
