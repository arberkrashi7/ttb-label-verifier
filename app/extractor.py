"""Read an alcohol label image with Claude Vision and return structured fields.

This module is deliberately small and self-contained: given raw image bytes,
it returns a `LabelFields` object with the five values we care about. The
matching/verification logic (a later step) consumes this output.
"""

from __future__ import annotations

import base64
import io
import os

import anthropic
from PIL import Image
from pydantic import BaseModel, Field

# Haiku is the default: this is a simple, speed-critical extraction and we need
# the whole round-trip under 5 seconds. Override with ANTHROPIC_MODEL (e.g.
# "claude-opus-4-8") to trade latency for maximum accuracy.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Downscale large photos before upload. Smaller payloads = faster round-trips,
# and 1568px on the long edge is plenty of resolution for label text.
MAX_EDGE_PX = 1568

# Cap how long we wait on the API so a slow call fails fast instead of hanging.
REQUEST_TIMEOUT_S = 20.0

EXTRACTION_PROMPT = (
    "You are reading the label on an alcohol beverage container for a TTB "
    "(Alcohol and Tobacco Tax and Trade Bureau) compliance check.\n\n"
    "Extract these five fields exactly as they appear on the label:\n"
    "- brand_name: the brand or producer name shown most prominently.\n"
    "- class_type: the class/type designation "
    "(e.g. 'Kentucky Straight Bourbon Whiskey', 'Cabernet Sauvignon').\n"
    "- alcohol_content: the alcohol content statement "
    "(e.g. '45% Alc/Vol', '90 Proof').\n"
    "- net_contents: the net contents / volume (e.g. '750 mL', '12 FL OZ').\n"
    "- government_warning: the FULL text of the GOVERNMENT WARNING statement, "
    "transcribed verbatim including the words 'GOVERNMENT WARNING'.\n\n"
    "Transcribe what is printed; do not infer, correct, or invent values. "
    "If a field is not present on the label, return an empty string for it.\n\n"
    "IMPORTANT — do not guess at text you cannot actually read. If a field is "
    "too blurry, washed out by glare, cut off, or otherwise unclear to read "
    "confidently, set that field's value to an empty string AND add its key to "
    "the 'unreadable_fields' list. Valid keys: brand_name, class_type, "
    "alcohol_content, net_contents, government_warning. If everything is clearly "
    "legible, return an empty 'unreadable_fields' list."
)


class LabelFields(BaseModel):
    """The values Claude extracts from the label image."""

    brand_name: str = Field(description="Brand or producer name on the label")
    class_type: str = Field(description="Class/type designation on the label")
    alcohol_content: str = Field(description="Alcohol content statement on the label")
    net_contents: str = Field(description="Net contents / volume on the label")
    government_warning: str = Field(description="Full GOVERNMENT WARNING text, verbatim")
    unreadable_fields: list[str] = Field(
        description=(
            "Keys of any fields too blurry/glared/cut-off to read confidently. "
            "Empty list if all fields are clearly legible."
        )
    )


class ExtractionError(Exception):
    """Raised when the label image cannot be read into structured fields."""


def _prepare_image(raw: bytes) -> tuple[str, str]:
    """Downscale + re-encode the image; return (base64_data, media_type)."""
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decode failure is user-facing
        raise ExtractionError("That file could not be read as an image.") from exc

    # JPEG can't store alpha; normalize everything to RGB.
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    image.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))  # preserves aspect ratio

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    encoded = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
    return encoded, "image/jpeg"


def extract_label_fields(raw: bytes) -> LabelFields:
    """Send the image to Claude Vision and return the parsed label fields."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ExtractionError(
            "The server is missing its ANTHROPIC_API_KEY. Set it and restart."
        )

    image_data, media_type = _prepare_image(raw)

    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=1)

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
            output_format=LabelFields,
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("The ANTHROPIC_API_KEY is invalid.") from exc
    except anthropic.APITimeoutError as exc:
        raise ExtractionError("Reading the label took too long. Please try again.") from exc
    except anthropic.APIConnectionError as exc:
        raise ExtractionError(
            "Could not reach the label-reading service. Check your connection and try again."
        ) from exc
    except anthropic.APIError as exc:
        raise ExtractionError("The label-reading service had a problem. Please try again.") from exc
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - never surface a raw error to the user
        raise ExtractionError("Something went wrong reading the label. Please try again.") from exc

    fields = response.parsed_output
    if fields is None:
        raise ExtractionError("The label could not be read. Try a clearer photo.")
    return fields


def prewarm() -> None:
    """Compile + cache the output schema so the first real request isn't slow.

    Structured-output schemas are compiled on first use and cached by the API
    for ~24h. Sending one tiny throwaway request at startup moves that one-time
    cost off the first real user. Safe to call when no key is set — it no-ops.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buffer, format="JPEG")
    image_data = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")

    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=0)
    try:
        client.messages.parse(
            model=MODEL,
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
            output_format=LabelFields,
        )
    except Exception:  # noqa: BLE001 - prewarm is best-effort; never block startup
        pass
