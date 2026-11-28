"""Observe new photos and group the results for review."""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import anthropic

from exif_dater.claude import ANALYSIS_EDGE, BILLING_URL, Analyzer, billing_problem
from exif_dater.dates import date_from_name
from exif_dater.library import Library, Observation
from exif_dater.photos import list_photos, preview

PARALLEL_REQUESTS = 4


class _Stopped(Exception):
    pass


def new_photos(library: Library) -> list[str]:
    """Return the photos in the library's directory that have not been observed."""
    return [file for file in list_photos(library.root) if file not in library.observations]


def run_requests[Item, Result](
    items: list[Item], request: Callable[[Item], Result], stop: threading.Event
) -> Iterator[tuple[Item, Result | None, Exception | None]]:
    """Make requests in parallel and yield (item, result, error) as each finishes.

    A failed request doesn't stop the others. Once stop is set, no further request starts.

    A rejected sign-in or a billing problem would fail every request, so the first one also stops new
    requests. Those already sent are still yielded, then the error is raised: AuthenticationError for the
    sign-in, RuntimeError for billing.
    """
    halted = threading.Event()

    def unless_stopped(item: Item) -> Result:
        if stop.is_set() or halted.is_set():
            raise _Stopped
        try:
            return request(item)
        except anthropic.APIError as error:
            # Set here, in the worker, so that it takes no further item.
            if _fails_every_request(error):
                halted.set()
            raise

    fatal = None
    pool = ThreadPoolExecutor(PARALLEL_REQUESTS)
    try:
        futures = {pool.submit(unless_stopped, item): item for item in items}
        for future in as_completed(futures):
            try:
                result = future.result()
            except _Stopped:
                continue
            except (anthropic.APIError, OSError, RuntimeError, ValueError) as error:
                if _fails_every_request(error):
                    fatal = error
                else:
                    yield futures[future], None, error
                continue
            yield futures[future], result, None
    finally:
        pool.shutdown(cancel_futures=True)
    if isinstance(fatal, anthropic.AuthenticationError):
        raise fatal
    if fatal:
        raise RuntimeError(
            f"stopped at a billing problem: {billing_problem(fatal)} See {BILLING_URL}, then run it again."
        ) from fatal


def _fails_every_request(error: Exception) -> bool:
    return isinstance(error, anthropic.AuthenticationError) or billing_problem(error) is not None


def unnamed_photos(photos: list[str], today: date) -> list[str]:
    """Return the photos whose names carry no date, which only the model can look at."""
    return [file for file in photos if not date_from_name(file, today)]


def needs_requests(library: Library, unnamed: list[str]) -> bool:
    """Whether analyzing sends anything: photos to look at, or model observations still to group."""
    ungrouped_from_model = any(not library.observations[file].from_name for file in library.ungrouped())
    return bool(unnamed) or ungrouped_from_model


def analyze(
    analyzer: Analyzer,
    library: Library,
    photos: list[str],
    log: Callable[[str], None],
    stop: threading.Event | None = None,
) -> None:
    """Observe each photo with the model, then group every photo not yet in a group.

    Progress is saved after every photo so a rerun only pays for what is missing. RuntimeError reports
    the number of failed photos at the end.
    """
    stop = stop or threading.Event()

    def observe(file: str) -> Observation:
        return analyzer.observe(file, preview(library.root / file, ANALYSIS_EDGE))

    failed = 0
    for file, seen, error in run_requests(photos, observe, stop):
        if error:
            failed += 1
            log(f"failed   {file}: {error}")
            continue
        library.observations[file] = seen
        library.save()
        log(f"analyzed {file}: {seen.date} ({seen.confidence})")
    if stop.is_set():
        raise RuntimeError("stopped, run analyze again to finish")

    pending = library.ungrouped()
    if pending:
        log(f"grouping {len(pending)} photos")
        # Name-dated photos are grouped locally, and one photo alone isn't worth a request.
        seen_by_model = {
            file: library.observations[file] for file in pending if not library.observations[file].from_name
        }
        proposed = analyzer.group(seen_by_model) if len(seen_by_model) > 1 else []
        library.add_groups(pending, proposed)
        library.save()
    if failed:
        raise RuntimeError(f"{failed} photos failed, run analyze again to retry them")
