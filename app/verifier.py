"""Compare extracted label fields against the application values.

Each field gets a verdict (PASS / FAIL / NEEDS REVIEW / CANNOT VERIFY), a
confidence level, and a short human-readable reason. Rules per field type:

- Brand name, class/type, net contents: fuzzy match, ignoring case and
  punctuation. Real differences become NEEDS REVIEW (never a hard FAIL).
- ABV: parse the number from both sides; PASS within a tiny tolerance,
  otherwise FAIL.
- Government warning: strict. Exact text match AND the literal
  "GOVERNMENT WARNING" must appear in all caps, or it FAILs.
- Any field the vision step couldn't read (blank) becomes CANNOT VERIFY
  rather than a guessed verdict.
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from rapidfuzz import fuzz

from app.extractor import LabelFields

# ---- Status + confidence constants ----

PASS = "PASS"
FAIL = "FAIL"
NEEDS_REVIEW = "NEEDS REVIEW"
CANNOT_VERIFY = "CANNOT VERIFY — request better image"

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

# A fuzzy score at or above this (0-100) counts as a match.
FUZZY_PASS_SCORE = 90
# Allowed absolute difference between two ABV percentages.
ABV_TOLERANCE = 0.1

# Field key -> human label. Order is the display order.
FIELD_LABELS = {
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "alcohol_content": "Alcohol Content (ABV)",
    "net_contents": "Net Contents",
    "government_warning": "Government Warning",
}


class ApplicationValues(BaseModel):
    """The values the compliance agent typed into the application form."""

    brand_name: str = ""
    class_type: str = ""
    alcohol_content: str = ""
    net_contents: str = ""
    government_warning: str = ""


class FieldResult(BaseModel):
    """One field's verdict."""

    field: str
    label: str
    status: str
    confidence: str
    reason: str
    label_value: str
    application_value: str
    score: float | None = None


class VerificationResult(BaseModel):
    """All field verdicts plus a rolled-up summary."""

    overall: str
    counts: dict[str, int]
    fields: list[FieldResult]


# ---- Normalization helpers ----


def _normalize(text: str) -> str:
    """Lowercase, drop apostrophes, turn other punctuation into spaces."""
    text = text.lower().replace("’", "'")  # curly -> straight apostrophe
    text = re.sub(r"['`]", "", text)            # drop apostrophes entirely
    text = re.sub(r"[^\w\s]", " ", text)         # other punctuation -> space
    return re.sub(r"\s+", " ", text).strip()


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace (incl. line breaks) to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def _parse_abv(text: str) -> float | None:
    """Pull an ABV percentage out of free text.

    Prefers an explicit percentage; falls back to 'NN proof' (= proof / 2),
    then to the first bare number. Returns None if no number is present.
    """
    if not text:
        return None
    lowered = text.lower()
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", lowered)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*proof", lowered)
    if match:
        return float(match.group(1)) / 2.0
    match = re.search(r"(\d+(?:\.\d+)?)", lowered)
    if match:
        return float(match.group(1))
    return None


# ---- Per-field comparisons ----


def _result(field: str, status: str, confidence: str, reason: str,
            label_value: str, application_value: str,
            score: float | None = None) -> FieldResult:
    return FieldResult(
        field=field,
        label=FIELD_LABELS[field],
        status=status,
        confidence=confidence,
        reason=reason,
        label_value=label_value,
        application_value=application_value,
        score=score,
    )


def _fuzzy_field(field: str, label_value: str, application_value: str) -> FieldResult:
    if not label_value.strip():
        return _result(field, CANNOT_VERIFY, LOW,
                       "Could not read this field from the image.",
                       label_value, application_value)
    if not application_value.strip():
        return _result(field, NEEDS_REVIEW, MEDIUM,
                       "No application value was entered to compare against.",
                       label_value, application_value)

    score = fuzz.token_sort_ratio(_normalize(label_value), _normalize(application_value))

    if score >= FUZZY_PASS_SCORE:
        confidence = HIGH if score >= 97 else MEDIUM
        reason = ("Matches the application value (case and punctuation ignored)."
                  if score < 100 else "Matches the application value exactly.")
        return _result(field, PASS, confidence, reason,
                       label_value, application_value, round(score, 1))

    # A real difference — send to a human rather than failing automatically.
    confidence = HIGH if score < 60 else MEDIUM
    reason = (f"Label shows “{label_value}” but the application says "
              f"“{application_value}”.")
    return _result(field, NEEDS_REVIEW, confidence, reason,
                   label_value, application_value, round(score, 1))


def _abv_field(field: str, label_value: str, application_value: str) -> FieldResult:
    label_abv = _parse_abv(label_value)
    app_abv = _parse_abv(application_value)

    if not label_value.strip() or label_abv is None:
        return _result(field, CANNOT_VERIFY, LOW,
                       "Could not read an alcohol-content number from the image.",
                       label_value, application_value)
    if app_abv is None:
        return _result(field, NEEDS_REVIEW, MEDIUM,
                       "No valid alcohol content was entered to compare against.",
                       label_value, application_value)

    difference = abs(label_abv - app_abv)
    if difference <= ABV_TOLERANCE:
        return _result(field, PASS, HIGH,
                       f"Both are {label_abv:g}% (within tolerance).",
                       label_value, application_value)

    return _result(field, FAIL, HIGH,
                   f"Label is {label_abv:g}% but the application says {app_abv:g}%.",
                   label_value, application_value)


def _warning_field(field: str, label_value: str, application_value: str) -> FieldResult:
    if not label_value.strip():
        return _result(field, CANNOT_VERIFY, LOW,
                       "Could not read the warning text from the image.",
                       label_value, application_value)
    if not application_value.strip():
        return _result(field, NEEDS_REVIEW, MEDIUM,
                       "No warning text was entered to compare against.",
                       label_value, application_value)

    # Strict: the literal phrase must be present in ALL CAPS (case-sensitive).
    has_all_caps = "GOVERNMENT WARNING" in label_value
    text_matches = _collapse_ws(label_value) == _collapse_ws(application_value)

    if has_all_caps and text_matches:
        return _result(field, PASS, HIGH,
                       "Exact match and “GOVERNMENT WARNING” is in all capitals.",
                       label_value, application_value)
    if not has_all_caps and not text_matches:
        return _result(field, FAIL, HIGH,
                       "“GOVERNMENT WARNING” is not in all capitals and the "
                       "text does not exactly match the application.",
                       label_value, application_value)
    if not has_all_caps:
        return _result(field, FAIL, HIGH,
                       "“GOVERNMENT WARNING” must be in all capital letters; "
                       "the label uses different casing.",
                       label_value, application_value)
    return _result(field, FAIL, HIGH,
                   "The warning text does not exactly match the application.",
                   label_value, application_value)


# ---- Public entry point ----

_COMPARATORS = {
    "brand_name": _fuzzy_field,
    "class_type": _fuzzy_field,
    "alcohol_content": _abv_field,
    "net_contents": _fuzzy_field,
    "government_warning": _warning_field,
}


def verify(extracted: LabelFields, application: ApplicationValues) -> VerificationResult:
    """Compare every field and return per-field verdicts plus a summary."""
    extracted_data = extracted.model_dump()
    application_data = application.model_dump()
    # Fields the vision step flagged as too unclear to read confidently.
    unreadable = set(getattr(extracted, "unreadable_fields", None) or [])

    results: list[FieldResult] = []
    for field, comparator in _COMPARATORS.items():
        if field in unreadable:
            results.append(_result(
                field, CANNOT_VERIFY, LOW,
                "The image was too unclear to read this field — request a better image.",
                extracted_data.get(field, ""), application_data[field]))
            continue
        results.append(
            comparator(field, extracted_data[field], application_data[field])
        )

    counts = {PASS: 0, FAIL: 0, NEEDS_REVIEW: 0, CANNOT_VERIFY: 0}
    for r in results:
        counts[r.status] += 1

    # Overall verdict: worst outcome wins, in this severity order.
    if counts[FAIL]:
        overall = FAIL
    elif counts[CANNOT_VERIFY]:
        overall = CANNOT_VERIFY
    elif counts[NEEDS_REVIEW]:
        overall = NEEDS_REVIEW
    else:
        overall = PASS

    return VerificationResult(overall=overall, counts=counts, fields=results)
