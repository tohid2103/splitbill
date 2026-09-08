"""
Photo -> structured Bill, via Google Gemini vision (free tier).

Why Gemini here: Google AI Studio gives an ongoing free tier (not just a
one-time trial credit) that includes vision, which fits a personal tool you
run occasionally rather than something with predictable paid volume. Get a
key at https://aistudio.google.com/apikey (Google account, no card needed).

Model names in this space change often — Google has deprecated/renamed
preview models with little notice before. If MODEL below starts failing,
check https://ai.google.dev/gemini-api/docs/models for the current
recommended flash-tier model and swap it in; nothing else needs to change.

Same design choice as before: the model self-reports a confidence per field
in the same JSON call, used purely as a triage signal for the review screen,
not a calibrated probability.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types

from models import (
    Bill, LineItem, Charge, ChargeType, ConfidenceField, TextConfidenceField,
    ExtractionMeta,
)

MODEL = "gemini-3.6-flash"  # free-tier eligible, vision-capable

EXTRACTION_SYSTEM_PROMPT = """You are a meticulous restaurant bill reader. \
You will be shown one or more photos of a single restaurant bill/receipt \
(if more than one photo, they are continuations of the same bill — combine \
them into one set of items, don't duplicate).

Read every line item, tax line, service charge, and discount you can see. \
Bills may be in poor condition: dim light, glare, crumpled paper, steep \
angle, faded thermal print, handwriting, or mixed scripts/languages \
(e.g. Hindi + English). Do your best and be honest about uncertainty via \
the confidence field rather than guessing silently.

Return ONLY valid JSON (no markdown fences, no prose) matching exactly this \
shape:

{
  "restaurant_name": {"value": "...", "confidence": 0.0-1.0} or null,
  "date": {"value": "...", "confidence": 0.0-1.0} or null,
  "items": [
    {
      "name": {"value": "...", "confidence": 0.0-1.0},
      "quantity": {"value": <number>, "confidence": 0.0-1.0},
      "unit_price": {"value": <number>, "confidence": 0.0-1.0},
      "line_total": {"value": <number>, "confidence": 0.0-1.0}
    }
  ],
  "charges": [
    {
      "label": {"value": "...", "confidence": 0.0-1.0},
      "charge_type": "tax" | "service_charge" | "discount" | "other",
      "amount": {"value": <number, negative for discounts>, "confidence": 0.0-1.0},
      "is_percentage_of_subtotal": true/false,
      "percentage": <number> or null
    }
  ],
  "printed_subtotal": {"value": <number>, "confidence": 0.0-1.0} or null,
  "printed_total": {"value": <number>, "confidence": 0.0-1.0} or null,
  "overall_confidence": 0.0-1.0
}

Rules:
- quantity/unit_price/line_total are numbers, not strings. Strip currency symbols.
- If a bill shows CGST and SGST separately, return them as two separate charge entries.
- If you truly cannot read a field, still return your best guess but set confidence low (e.g. 0.2-0.4). Never omit a required field.
- Handwritten additions (e.g. someone scrawled an extra item) should be included as a normal item, with lower confidence if messy.
- overall_confidence is your holistic sense of how reliable this whole extraction is."""


def _load_image_bytes(path: str) -> tuple[bytes, str]:
    ext = Path(path).suffix.lower().lstrip(".")
    media_type = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "webp": "image/webp",
    }.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return f.read(), media_type


def extract_bill(image_paths: list[str], api_key: str | None = None) -> Bill:
    """Send one or more bill photos to Gemini and parse the result into a
    validated Bill. Raises pydantic.ValidationError if the model's JSON
    doesn't fit the schema (rare, but we don't want to silently coerce
    garbage into a Bill)."""

    client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))

    parts = []
    for p in image_paths:
        data, media_type = _load_image_bytes(p)
        parts.append(types.Part.from_bytes(data=data, mime_type=media_type))
    parts.append(
        f"Here {'is' if len(image_paths) == 1 else 'are'} the bill photo(s). Extract as instructed."
    )

    response = client.models.generate_content(
        model=MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM_PROMPT,
            response_mime_type="application/json",  # asks Gemini to return raw JSON, no fences
            temperature=0.1,
        ),
    )

    raw_text = (response.text or "").strip()
    # Defensive: strip accidental code fences even though response_mime_type should prevent them.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    parsed = json.loads(raw_text)
    return _to_bill(parsed, image_paths)


def _cf(d) -> ConfidenceField | None:
    return None if d is None else ConfidenceField(value=float(d["value"]), confidence=d["confidence"])


def _tcf(d) -> TextConfidenceField | None:
    return None if d is None else TextConfidenceField(value=str(d["value"]), confidence=d["confidence"])


def _to_bill(parsed: dict, image_paths: list[str]) -> Bill:
    items = [
        LineItem(
            name=_tcf(it["name"]),
            quantity=_cf(it["quantity"]),
            unit_price=_cf(it["unit_price"]),
            line_total=_cf(it["line_total"]),
        )
        for it in parsed.get("items", [])
    ]
    charges = [
        Charge(
            label=_tcf(c["label"]),
            charge_type=ChargeType(c["charge_type"]),
            amount=_cf(c["amount"]),
            is_percentage_of_subtotal=c.get("is_percentage_of_subtotal", False),
            percentage=c.get("percentage"),
        )
        for c in parsed.get("charges", [])
    ]
    return Bill(
        restaurant_name=_tcf(parsed.get("restaurant_name")),
        date=_tcf(parsed.get("date")),
        items=items,
        charges=charges,
        printed_subtotal=_cf(parsed.get("printed_subtotal")),
        printed_total=_cf(parsed.get("printed_total")),
        meta=ExtractionMeta(
            source_images=image_paths,
            overall_confidence=parsed.get("overall_confidence", 0.7),
        ),
    )
