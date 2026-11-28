"""Ask Claude what photos say about their dates."""

import base64
import json
import threading
from dataclasses import asdict

import anthropic

from exif_dater.library import Group, Observation

# The cheapest current model. The --model option selects a stronger one.
DEFAULT_MODEL = "claude-haiku-5-5"
# The long edge sent to the model: enough to read printed date stamps at a modest token cost.
ANALYSIS_EDGE = 1568

OBSERVE_PROMPT = """\
This is a scan of a physical photograph. Estimate when the photo was taken.

Look first for hard evidence: a date stamp imprinted by the camera, a lab print date, handwriting or a
caption, or a date visible in the scene. Then weigh softer clues: season and holidays, clothing, hairstyles,
vehicles, electronics, packaging, print borders and color rendition. The file's own timestamps are only the
scan date, so ignore them. The path below may contain a hint the owner typed. Don't identify people by their
appearance.

Report:
- description: the scene in one or two sentences, with details that would match other photos from the same
  event (place, clothing, objects, print style).
- printed_date: any date text exactly as it appears, or an empty string.
- clues: the evidence behind your estimate, briefly.
- date: your best estimate as YYYY, YYYY-MM or YYYY-MM-DD, only as precise as the evidence supports, or an
  empty string if you can't estimate.
- earliest_year and latest_year: the plausible range.
- confidence: high for legible written or printed dates, medium for several consistent clues, low otherwise."""

GROUP_PROMPT = """\
Below are observations of scanned physical photographs, one JSON object per photo. Group the photos that were
taken at the same event or in the same short period, judging by their descriptions, print style, path and
neighboring file names (scan order often follows albums or rolls, but not always). A photo with nothing in
common with others gets its own group.

Give each group one date. Evidence in one photo, such as a legible date stamp, dates its whole group. Don't
group photos whose printed dates conflict. Put every file in exactly one group and copy file names exactly.
Use YYYY, YYYY-MM or YYYY-MM-DD, only as precise as the evidence supports, or an empty string if unknown. In
reason, name the evidence and the file it came from."""

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_CONFIDENCE = {"type": "string", "enum": ["high", "medium", "low"]}


def _object_schema(properties: dict) -> dict:
    """Describe an object in which every property is required and no others are allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_OBSERVATION_SCHEMA = _object_schema(
    {
        "description": _STRING,
        "printed_date": _STRING,
        "clues": _STRING,
        "date": _STRING,
        "earliest_year": _INTEGER,
        "latest_year": _INTEGER,
        "confidence": _CONFIDENCE,
    }
)
_GROUPS_SCHEMA = _object_schema(
    {
        "groups": {
            "type": "array",
            "items": _object_schema(
                {
                    "title": _STRING,
                    "files": {"type": "array", "items": _STRING},
                    "date": _STRING,
                    "confidence": _CONFIDENCE,
                    "reason": _STRING,
                }
            ),
        }
    }
)


def has_credentials(client: anthropic.Anthropic) -> bool:
    return bool(client.api_key or client.auth_token or client.credentials)


class Analyzer:
    """Sends requests to Claude and counts the tokens they spend."""

    def __init__(self, client: anthropic.Anthropic, model: str = DEFAULT_MODEL, notes: str = "") -> None:
        self._client = client
        self._model = model
        self._notes = notes  # What the user knows about the collection, such as birthdates or moves.
        self._lock = threading.Lock()
        self.input_tokens = 0
        self.output_tokens = 0

    def observe(self, file: str, jpeg: bytes) -> Observation:
        """Report what one photo, given as a JPEG, says about its date."""
        source = {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}
        content = [
            {"type": "image", "source": source},
            {"type": "text", "text": f"{self._with_notes(OBSERVE_PROMPT)}\n\nPath: {file}"},
        ]
        return Observation(**self._ask(_OBSERVATION_SCHEMA, content))

    def group(self, observations: dict[str, Observation]) -> list[Group]:
        """Propose shared dates for photos that belong together."""
        lines = [json.dumps({"file": file, **asdict(seen)}) for file, seen in observations.items()]
        text = self._with_notes(GROUP_PROMPT) + "\n\n" + "\n".join(lines)
        reply = self._ask(_GROUPS_SCHEMA, [{"type": "text", "text": text}])
        return [Group(**group) for group in reply["groups"]]

    def _with_notes(self, prompt: str) -> str:
        if not self._notes:
            return prompt
        return f"{prompt}\n\nThe owner's notes about this collection:\n{self._notes}"

    def _ask(self, schema: dict, content: list[dict]) -> dict:
        """Send one user turn and decode the schema-constrained reply."""
        request = {
            "model": self._model,
            "max_tokens": 64000,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        }
        # A declined request is retried on a fallback model chosen by the API. Haiku has none.
        if not self._model.startswith("claude-haiku"):
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"
        # Streaming keeps a long grouping reply from hitting the request timeout.
        with self._client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
        with self._lock:
            self.input_tokens += message.usage.input_tokens
            self.output_tokens += message.usage.output_tokens

        if message.stop_reason == "refusal":
            raise RuntimeError("the model declined this request")
        if message.stop_reason == "max_tokens":
            raise RuntimeError("the reply was cut off at the token limit")
        reply = ""
        for block in message.content:
            if block.type == "fallback":
                reply = ""  # Anything before this block came from the model that declined.
            elif block.type == "text":
                reply += block.text
        return json.loads(reply)
