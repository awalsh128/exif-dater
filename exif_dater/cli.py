"""The command line: analyze a photo directory, review it, or test how well models date it."""

import argparse
import json
import re
import secrets
import shutil
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import anthropic

from exif_dater import prices
from exif_dater.analyze import analyze, new_photos
from exif_dater.claude import DEFAULT_MODEL, Analyzer, has_credentials
from exif_dater.evaluate import Evaluation, format_report, load_truth
from exif_dater.library import Library
from exif_dater.server import AppServer

NO_CREDENTIALS = "no Claude API credentials, run ./setup.sh or set ANTHROPIC_API_KEY, then try again"


def main(arguments: list[str] | None = None) -> None:
    options = _parser().parse_args(_join_zone(sys.argv[1:] if arguments is None else arguments))
    try:
        options.run(options)
    except (anthropic.AnthropicError, OSError, RuntimeError, ValueError) as error:
        sys.exit(f"error: {error}")
    except KeyboardInterrupt:
        sys.exit(130)


def _join_zone(arguments: list[str]) -> list[str]:
    """Turn "--tz -08:00" into "--tz=-08:00", because argparse reads a leading dash as an option."""
    joined = []
    remaining = iter(arguments)
    for argument in remaining:
        if argument == "--tz":
            value = next(remaining, None)
            joined.append(argument if value is None else f"{argument}={value}")
        else:
            joined.append(argument)
    return joined


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exif-dater", description=__doc__)
    commands = parser.add_subparsers(required=True)

    analyze_command = commands.add_parser("analyze", help="estimate dates for new photos")
    analyze_command.add_argument("--model", default=DEFAULT_MODEL, help="Claude model (%(default)s)")
    analyze_command.set_defaults(run=_analyze)

    review_command = commands.add_parser("review", help="open the web page for this directory")
    review_command.add_argument("--tz", default="-08:00", help="time zone offset to record (%(default)s)")
    review_command.set_defaults(run=_review)

    evaluate_command = commands.add_parser("evaluate", help="test models on photos with known dates")
    evaluate_command.add_argument(
        "--model", action="append", help="model to test, repeatable (default: Haiku, Sonnet and Opus)"
    )
    evaluate_command.add_argument(
        "--truth", type=Path, help="CSV of file,date (default: truth.csv in the directory, else approvals)"
    )
    evaluate_command.add_argument(
        "--within", type=int, default=2, help="years of error counted as correct (%(default)s)"
    )
    evaluate_command.add_argument(
        "--target", type=int, default=80, help="percent of photos a model must get within (%(default)s)"
    )
    evaluate_command.add_argument("--report", type=Path, help="also write every answer to this JSON file")
    evaluate_command.set_defaults(run=_evaluate)

    for command in (analyze_command, review_command, evaluate_command):
        command.add_argument("directory", type=Path)
        command.add_argument("--notes", type=Path, help="text file of facts that help date the photos")
    for command in (analyze_command, evaluate_command):
        command.add_argument("--yes", action="store_true", help="send photos without asking first")
    return parser


def _analyze(options: argparse.Namespace) -> None:
    _require("magick", "imagemagick")
    library = _open(options.directory)
    notes = _notes(options)

    new = new_photos(library)
    unnamed = library.date_by_name(new, date.today())
    library.save()
    print(f"dated {len(new) - len(unnamed)} photos from their names")
    client = anthropic.Anthropic()
    needs_model = unnamed or any(not library.observations[file].from_name for file in library.ungrouped())
    if needs_model and not has_credentials(client):
        raise RuntimeError(NO_CREDENTIALS)
    if unnamed and not options.yes:
        _confirm(f"Send {len(unnamed)} photos to the Anthropic API? This is billed to your account.")

    analyzer = Analyzer(client, options.model, notes)
    try:
        analyze(analyzer, library, unnamed, print)
    finally:
        print(f"tokens used: {analyzer.input_tokens} input, {analyzer.output_tokens} output")


def _review(options: argparse.Namespace) -> None:
    _require("magick", "imagemagick")
    _require("exiftool", "libimage-exiftool-perl")
    if not re.fullmatch(r"[+-]\d{2}:\d{2}", options.tz):
        raise ValueError(f"--tz must look like -08:00, got {options.tz!r}")
    library = _open(options.directory)
    token = secrets.token_urlsafe()
    with AppServer(library, token, options.tz, anthropic.Anthropic(), _notes(options)) as server:
        print(f"Review at {server.url}\nPress Ctrl+C when done.", flush=True)
        server.serve_forever()


def _evaluate(options: argparse.Namespace) -> None:
    _require("magick", "imagemagick")
    library = _open(options.directory)
    truth = load_truth(library, options.truth, date.today())
    models = options.model or prices.DEFAULT_COMPARISON
    evaluation = Evaluation(library, truth, models, _notes(options))

    client = anthropic.Anthropic()
    pending = evaluation.pending()
    if pending:
        if not has_credentials(client):
            raise RuntimeError(NO_CREDENTIALS)
        if not options.yes:
            costs = [prices.estimated_cost(model, 1) for model, _ in pending]
            about = "an unknown amount" if None in costs else f"about ${sum(costs):.2f}"
            _confirm(f"Send {len(pending)} requests to the Anthropic API, costing {about}?")
    report = evaluation.run(client, options.within, options.target, date.today(), print)
    if options.report:
        options.report.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    print(format_report(report))


def _confirm(question: str) -> None:
    if input(f"{question} [y/N] ").strip().lower() != "y":
        raise RuntimeError("cancelled, nothing was sent")


def _notes(options: argparse.Namespace) -> str:
    return options.notes.read_text(encoding="utf-8") if options.notes else ""


def _open(directory: Path) -> Library:
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory")
    return Library.open(directory)


def _require(tool: str, package: str) -> None:
    if not shutil.which(tool):
        raise RuntimeError(f"{tool} not found, install it with: sudo apt install {package}")
