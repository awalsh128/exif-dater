"""What models cost, from the price list in models.json."""

import json
from importlib.resources import files

_DATA = json.loads(files("exif_dater").joinpath("models.json").read_text(encoding="utf-8"))

PRICES_AS_OF: str = _DATA["prices_as_of"]
LISTED_MODELS: list[str] = list(_DATA["usd_per_million_tokens"])
DEFAULT_COMPARISON: list[str] = _DATA["default_comparison"]


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Return the cost in US dollars, or None for a model that isn't in the price list."""
    price = _DATA["usd_per_million_tokens"].get(model)
    if price is None:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def estimated_cost(model: str, photos: int) -> float | None:
    """Guess the cost of observing this many photos before any request is made."""
    tokens = _DATA["estimated_tokens_per_photo"]
    return cost(model, tokens["input"] * photos, tokens["output"] * photos)
