"""Observe new photos and group the results for review."""

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from exif_dater.claude import ANALYSIS_EDGE, Analyzer
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
    """

    def unless_stopped(item: Item) -> Result:
        if stop.is_set():
            raise _Stopped
        return request(item)

    pool = ThreadPoolExecutor(PARALLEL_REQUESTS)
    try:
        futures = {pool.submit(unless_stopped, item): item for item in items}
        for future in as_completed(futures):
            try:
                result = future.result()
            except _Stopped:
                continue
            except anthropic.AuthenticationError:
                raise  # Every other request would fail the same way.
            except (anthropic.APIError, OSError, RuntimeError, ValueError) as error:
                yield futures[future], None, error
                continue
            yield futures[future], result, None
    finally:
        pool.shutdown(cancel_futures=True)


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
