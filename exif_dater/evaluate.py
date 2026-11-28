"""Measure how well models date photos whose dates are already known."""

import csv
import hashlib
import json
import statistics
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import anthropic

from exif_dater import prices
from exif_dater.analyze import run_requests
from exif_dater.claude import ANALYSIS_EDGE, OBSERVE_PROMPT, Analyzer
from exif_dater.dates import parse_photo_date
from exif_dater.library import Library
from exif_dater.photos import list_photos, preview

TRUTH_FILE = "truth.csv"
CACHE_FILE = ".exif-dater-eval.json"
# Below this, one photo moves a score by more than five points.
TRUSTWORTHY_SAMPLE = 20
# The model sees this instead of the real path, which may spell out the answer.
BLIND_NAME = "photo.jpg"


@dataclass
class Truth:
    """The known date of each photo, and where those dates came from."""

    dates: dict[str, str]
    source: str
    from_approvals: bool = False


@dataclass
class Score:
    model: str
    photos: int
    failed: int
    dated: int
    exact_year: int
    within: int
    median_year_error: float | None
    year_in_range: int
    month_correct: int
    month_known: int
    # For each confidence level: [answers within the allowed error, answers].
    by_confidence: dict[str, list[int]]
    cost_per_100: float | None
    meets_target: bool


@dataclass
class Report:
    truth_source: str
    within_years: int
    target_percent: int
    scores: list[Score]
    recommended: str | None
    spent: float | None  # On this run; cached answers cost nothing.
    prices_as_of: str
    warnings: list[str] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)


def load_truth(library: Library, path: Path | None, today: date) -> Truth:
    """Read known dates from a CSV with "file" and "date" columns.

    Without a path, use truth.csv in the photo directory, or else the dates approved in review.
    """
    if path is None and (library.root / TRUTH_FILE).exists():
        path = library.root / TRUTH_FILE
    if path is None:
        dates = {file: group.date for group in library.groups if group.approved for file in group.files}
        truth = Truth(dates, "dates approved in review", from_approvals=True)
    else:
        with path.open(newline="", encoding="utf-8-sig") as table:
            rows = csv.DictReader(table)
            if not {"file", "date"} <= set(rows.fieldnames or []):
                raise ValueError(f"{path.name} needs a header line with the columns file,date")
            dates = {row["file"].strip(): (row["date"] or "").strip() for row in rows}
        truth = Truth(dates, path.name)

    if not truth.dates:
        raise ValueError(f"no known dates: add {TRUTH_FILE} to the photo directory or approve some groups")
    photos = set(list_photos(library.root))
    for file, taken in truth.dates.items():
        if file not in photos:
            raise ValueError(f"{truth.source} names {file!r}, which isn't a photo in the directory")
        parse_photo_date(taken, today)
    return truth


class Evaluation:
    """One comparison of models on photos with known dates.

    Answers are cached by model, prompt, notes and image, so a rerun only pays for what is new.
    """

    def __init__(self, library: Library, truth: Truth, models: list[str], notes: str = "") -> None:
        if not models:
            raise ValueError("choose at least one model")
        self._library = library
        self._truth = truth
        self._models = list(dict.fromkeys(models))
        self._notes = notes
        self._cache_path = library.root / CACHE_FILE
        try:
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._cache = {}
        self._keys = {(model, file): self._key(model, file) for model in self._models for file in truth.dates}

    def pending(self) -> list[tuple[str, str]]:
        """Return the (model, file) requests that still have to be made."""
        return [item for item, key in self._keys.items() if key not in self._cache]

    def run(
        self,
        client: anthropic.Anthropic,
        within_years: int,
        target_percent: int,
        today: date,
        log: Callable[[str], None],
        stop: threading.Event | None = None,
    ) -> Report:
        if within_years < 0 or not 0 <= target_percent <= 100:
            raise ValueError("the allowed error must be 0 or more years and the target 0 to 100 percent")
        stop = stop or threading.Event()

        def request(item: tuple[str, str]) -> dict:
            model, file = item
            analyzer = Analyzer(client, model, self._notes)
            seen = analyzer.observe(BLIND_NAME, preview(self._library.root / file, ANALYSIS_EDGE))
            return {
                "observation": asdict(seen),
                "input_tokens": analyzer.input_tokens,
                "output_tokens": analyzer.output_tokens,
            }

        new_costs = []
        for (model, file), answer, error in run_requests(self.pending(), request, stop):
            if error:
                log(f"failed {model} {file}: {error}")
                continue
            self._cache[self._keys[model, file]] = answer
            self._save_cache()
            new_costs.append(prices.cost(model, answer["input_tokens"], answer["output_tokens"]))
            log(f"{model} {file}: {answer['observation']['date']}")
        if stop.is_set():
            raise RuntimeError("stopped before every photo was tested")

        scores = [self._score(model, within_years, target_percent, today) for model in self._models]
        passing = [score for score in scores if score.meets_target]
        cheapest = min(
            passing,
            key=lambda score: float("inf") if score.cost_per_100 is None else score.cost_per_100,
            default=None,
        )
        return Report(
            truth_source=self._truth.source,
            within_years=within_years,
            target_percent=target_percent,
            scores=scores,
            recommended=cheapest.model if cheapest else None,
            spent=None if None in new_costs else sum(new_costs),
            prices_as_of=prices.PRICES_AS_OF,
            warnings=self._warnings(scores),
            answers=self._answers(),
        )

    def _score(self, model: str, within_years: int, target_percent: int, today: date) -> Score:
        score = Score(model, len(self._truth.dates), 0, 0, 0, 0, None, 0, 0, 0, {}, None, False)
        errors = []
        tokens = [0, 0]
        answered = 0
        for file, known in self._truth.dates.items():
            known_year = parse_photo_date(known, today).day.year
            if len(known) > len("YYYY"):
                score.month_known += 1
            answer = self._cache.get(self._keys[model, file])
            if answer is None:
                score.failed += 1
                continue
            answered += 1
            tokens[0] += answer["input_tokens"]
            tokens[1] += answer["output_tokens"]
            seen = answer["observation"]
            score.year_in_range += seen["earliest_year"] <= known_year <= seen["latest_year"]
            confidence = score.by_confidence.setdefault(seen["confidence"], [0, 0])
            confidence[1] += 1
            try:
                guessed_year = parse_photo_date(seen["date"], today).day.year
            except ValueError:
                continue  # No usable date counts against the model.
            score.dated += 1
            errors.append(abs(guessed_year - known_year))
            score.exact_year += errors[-1] == 0
            if errors[-1] <= within_years:
                score.within += 1
                confidence[0] += 1
            month = len("YYYY-MM")
            score.month_correct += len(known) >= month and seen["date"][:month] == known[:month]
        if errors:
            score.median_year_error = statistics.median(errors)
        total = prices.cost(model, *tokens)
        if answered and total is not None:
            score.cost_per_100 = total / answered * 100
        score.meets_target = score.within * 100 >= target_percent * score.photos
        return score

    def _warnings(self, scores: list[Score]) -> list[str]:
        warnings = []
        if len(self._truth.dates) < TRUSTWORTHY_SAMPLE:
            warnings.append(
                f"Only {len(self._truth.dates)} photos have known dates. "
                f"Use at least {TRUSTWORTHY_SAMPLE} before relying on these scores."
            )
        if self._truth.from_approvals:
            warnings.append(
                "The known dates are your approvals. Where you accepted a model's proposal, the scores "
                f"favor that model. Dates you know independently, in {TRUTH_FILE}, are a fairer test."
            )
        if any(score.failed for score in scores):
            warnings.append("Some requests failed and count as misses. Run the test again to retry them.")
        return warnings

    def _answers(self) -> list[dict]:
        answers = []
        for (model, file), key in self._keys.items():
            seen = self._cache.get(key, {}).get("observation")
            if seen:
                answers.append(
                    {
                        "model": model,
                        "file": file,
                        "known": self._truth.dates[file],
                        "date": seen["date"],
                        "confidence": seen["confidence"],
                        "clues": seen["clues"],
                    }
                )
        return answers

    def _key(self, model: str, file: str) -> str:
        image = hashlib.sha256((self._library.root / file).read_bytes()).hexdigest()
        request = [model, OBSERVE_PROMPT, self._notes, ANALYSIS_EDGE, image]
        return hashlib.sha256(json.dumps(request).encode()).hexdigest()

    def _save_cache(self) -> None:
        temporary = self._cache_path.with_name(CACHE_FILE + ".tmp")
        temporary.touch(mode=0o600)
        temporary.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        temporary.replace(self._cache_path)


def format_report(report: Report) -> str:
    """Lay the scores out as a plain text table."""

    def share(part: int, whole: int) -> str:
        return f"{part}/{whole}" if whole else "-"

    rows = [["model", "within", "exact year", "median error", "month", "failed", "$ per 100"]]
    for score in report.scores:
        error = "-" if score.median_year_error is None else f"{score.median_year_error:g} y"
        price = "unknown" if score.cost_per_100 is None else f"{score.cost_per_100:.3f}"
        rows.append(
            [
                score.model,
                share(score.within, score.photos),
                share(score.exact_year, score.photos),
                error,
                share(score.month_correct, score.month_known),
                str(score.failed),
                price,
            ]
        )
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    lines = [f"Known dates: {report.truth_source}. Within means {report.within_years} years or less."]
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) for row in rows]
    for score in report.scores:
        levels = ", ".join(
            f"{level} {share(*score.by_confidence[level])}"
            for level in ("high", "medium", "low")
            if level in score.by_confidence
        )
        lines.append(f"{score.model} within, by stated confidence: {levels or '-'}")
    if report.recommended:
        lines.append(f"Cheapest model meeting {report.target_percent}% within: {report.recommended}")
    else:
        lines.append(f"No model met {report.target_percent}% within.")
    if report.spent is not None:
        lines.append(f"Spent on this run: ${report.spent:.4f} (prices as of {report.prices_as_of})")
    lines += [f"Warning: {warning}" for warning in report.warnings]
    return "\n".join(lines)
