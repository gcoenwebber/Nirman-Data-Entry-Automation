"""
Type-aware field validation for the NIRMAN extraction pipeline.

Validates raw extracted values against FieldDefinition constraints,
handles sentinel values, Devanagari numeral conversion, and type-specific
normalisation (numeric ranges, categorical membership, checkbox booleans, dates).

Never converts sentinels to zero or empty string.
Never invents values — unclear data is flagged for review.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from nirman.config import (
    FIELD_MISSING,
    ILLEGIBLE,
    INVALID_VALUE,
    OUT_OF_RANGE,
    REVIEW_SENTINELS,
)
from nirman.models import (
    ExtractionRecord,
    ExtractionStatus,
    FieldDefinition,
    FieldType,
    FieldValue,
    FormSchema,
    RecordStatus,
)

logger = logging.getLogger(__name__)

# ── Devanagari Digit Conversion ────────────────────────────────

DEVANAGARI_DIGIT_MAP: dict[str, str] = {
    "०": "0",
    "१": "1",
    "२": "2",
    "३": "3",
    "४": "4",
    "५": "5",
    "६": "6",
    "७": "7",
    "८": "8",
    "९": "9",
}


def convert_devanagari(text: str) -> str:
    """Convert Devanagari digits in *text* to Arabic (Western) digits.

    Non-digit characters are left unchanged.

    Args:
        text: Input string that may contain Devanagari numerals.

    Returns:
        String with every Devanagari digit replaced by its Arabic equivalent.
    """
    if not text:
        return text
    return "".join(DEVANAGARI_DIGIT_MAP.get(ch, ch) for ch in text)


# ── Single-Field Validation ───────────────────────────────────

def validate_field(field_def: FieldDefinition, raw_value: Any) -> FieldValue:
    """Validate a single extracted value against its field definition.

    Applies type-specific rules and sentinel handling:
    * ``"ILLEGIBLE"`` → status *illegible*, needs_review
    * ``"FIELD_MISSING"`` / ``None`` → status *missing*, needs_review if required
    * Numeric/rating — range check, Devanagari conversion
    * Text — accept any non-empty string
    * Categorical — case-insensitive membership check
    * Checkbox — normalise to ``True``/``False``
    * Date — parse and validate format

    Args:
        field_def: The schema definition for this field.
        raw_value: The raw value returned by the AI extractor.

    Returns:
        A populated :class:`FieldValue` instance.
    """

    # ── Sentinel: ILLEGIBLE ────────────────────────────────────
    if _is_sentinel(raw_value, ILLEGIBLE):
        return FieldValue(
            raw_value=ILLEGIBLE,
            validated_value=ILLEGIBLE,
            status=ExtractionStatus.ILLEGIBLE,
            needs_review=True,
            review_note=f"Field '{field_def.name}' is illegible",
        )

    # ── Sentinel: FIELD_MISSING / None ─────────────────────────
    if raw_value is None or _is_sentinel(raw_value, FIELD_MISSING):
        needs_review = field_def.required
        return FieldValue(
            raw_value=FIELD_MISSING,
            validated_value=FIELD_MISSING,
            status=ExtractionStatus.MISSING,
            needs_review=needs_review,
            review_note=(
                f"Field '{field_def.name}' is missing (required)"
                if needs_review
                else f"Field '{field_def.name}' is missing (optional)"
            ),
        )

    # ── Sentinel: any other known sentinel passed through ──────
    if isinstance(raw_value, str) and raw_value.strip().upper() in {
        OUT_OF_RANGE,
        INVALID_VALUE,
    }:
        sentinel = raw_value.strip().upper()
        return FieldValue(
            raw_value=sentinel,
            validated_value=sentinel,
            status=(
                ExtractionStatus.OUT_OF_RANGE
                if sentinel == OUT_OF_RANGE
                else ExtractionStatus.INVALID
            ),
            needs_review=True,
            review_note=f"Field '{field_def.name}' flagged as {sentinel}",
        )

    # ── Type-specific validation ──────────────────────────────
    try:
        if field_def.field_type in (FieldType.NUMERIC, FieldType.RATING):
            return _validate_numeric(field_def, raw_value)
        if field_def.field_type == FieldType.TEXT:
            return _validate_text(field_def, raw_value)
        if field_def.field_type == FieldType.CATEGORICAL:
            return _validate_categorical(field_def, raw_value)
        if field_def.field_type == FieldType.CHECKBOX:
            return _validate_checkbox(field_def, raw_value)
        if field_def.field_type == FieldType.DATE:
            return _validate_date(field_def, raw_value)
        # FieldType.OTHER — accept as-is
        return FieldValue(
            raw_value=raw_value,
            validated_value=raw_value,
            status=ExtractionStatus.VALID,
        )
    except Exception as exc:
        logger.warning(
            "Unexpected error validating field '%s': %s", field_def.name, exc
        )
        return FieldValue(
            raw_value=raw_value,
            validated_value=INVALID_VALUE,
            status=ExtractionStatus.INVALID,
            needs_review=True,
            review_note=f"Validation error for '{field_def.name}': {exc}",
        )


# ── Record-Level Validation ───────────────────────────────────

def validate_record(
    record: ExtractionRecord, schema: FormSchema
) -> ExtractionRecord:
    """Re-validate every field in *record* against *schema*.

    Updates ``review_required`` and ``review_notes`` on the record.

    Args:
        record: An existing extraction record.
        schema: The form schema that governs this record.

    Returns:
        The same record, mutated with updated validation state.
    """
    flagged: list[str] = []
    for field_def in schema.fields:
        fv = record.fields.get(field_def.name)
        if fv is None:
            # Field absent in record — create a missing entry
            record.fields[field_def.name] = validate_field(field_def, None)
            fv = record.fields[field_def.name]
        else:
            # Re-validate using raw_value
            record.fields[field_def.name] = validate_field(
                field_def, fv.raw_value
            )
            fv = record.fields[field_def.name]

        if fv.needs_review:
            flagged.append(f"{field_def.name}: {fv.review_note}")

    record.review_required = len(flagged) > 0
    record.review_notes = "; ".join(flagged) if flagged else ""

    # Recalculate overall status
    if record.status != RecordStatus.FAILED:
        total = len(schema.fields)
        valid_count = sum(
            1
            for fd in schema.fields
            if record.fields.get(fd.name)
            and record.fields[fd.name].status == ExtractionStatus.VALID
        )
        if valid_count == total:
            record.status = RecordStatus.SUCCESS
        elif valid_count > 0:
            record.status = RecordStatus.PARTIAL
        else:
            record.status = RecordStatus.FAILED

    return record


# ── Private helpers ────────────────────────────────────────────

def _is_sentinel(value: Any, sentinel: str) -> bool:
    """Check whether *value* matches a sentinel string (case-insensitive)."""
    if isinstance(value, str):
        return value.strip().upper() == sentinel
    return False


def _validate_numeric(
    field_def: FieldDefinition, raw_value: Any
) -> FieldValue:
    """Validate a numeric or rating field value.

    Handles string-to-number conversion and Devanagari digit replacement.
    Checks min/max range if defined in the field definition.
    """
    converted = raw_value

    # String conversion: Devanagari → Arabic, then parse
    if isinstance(raw_value, str):
        cleaned = convert_devanagari(raw_value.strip())
        try:
            # Try integer first, then float
            converted = int(cleaned) if "." not in cleaned else float(cleaned)
        except ValueError:
            return FieldValue(
                raw_value=raw_value,
                validated_value=INVALID_VALUE,
                status=ExtractionStatus.INVALID,
                needs_review=True,
                review_note=(
                    f"Field '{field_def.name}': cannot convert "
                    f"'{raw_value}' to number"
                ),
            )
    elif isinstance(raw_value, (int, float)):
        converted = raw_value
    else:
        return FieldValue(
            raw_value=raw_value,
            validated_value=INVALID_VALUE,
            status=ExtractionStatus.INVALID,
            needs_review=True,
            review_note=(
                f"Field '{field_def.name}': unexpected type "
                f"{type(raw_value).__name__}"
            ),
        )

    # Range check
    if field_def.min_value is not None and converted < field_def.min_value:
        return FieldValue(
            raw_value=raw_value,
            validated_value=OUT_OF_RANGE,
            status=ExtractionStatus.OUT_OF_RANGE,
            needs_review=True,
            review_note=(
                f"Field '{field_def.name}': value {converted} "
                f"below minimum {field_def.min_value}"
            ),
        )
    if field_def.max_value is not None and converted > field_def.max_value:
        return FieldValue(
            raw_value=raw_value,
            validated_value=OUT_OF_RANGE,
            status=ExtractionStatus.OUT_OF_RANGE,
            needs_review=True,
            review_note=(
                f"Field '{field_def.name}': value {converted} "
                f"above maximum {field_def.max_value}"
            ),
        )

    return FieldValue(
        raw_value=raw_value,
        validated_value=converted,
        status=ExtractionStatus.VALID,
    )


def _validate_text(
    field_def: FieldDefinition, raw_value: Any
) -> FieldValue:
    """Validate a free-text field value."""
    text = str(raw_value).strip() if raw_value is not None else ""

    if not text and field_def.required:
        return FieldValue(
            raw_value=raw_value,
            validated_value=FIELD_MISSING,
            status=ExtractionStatus.MISSING,
            needs_review=True,
            review_note=f"Field '{field_def.name}': empty text (required field)",
        )

    return FieldValue(
        raw_value=raw_value,
        validated_value=text,
        status=ExtractionStatus.VALID,
    )


def _validate_categorical(
    field_def: FieldDefinition, raw_value: Any
) -> FieldValue:
    """Validate a categorical field value against allowed_values."""
    text = str(raw_value).strip()

    if not field_def.allowed_values:
        # No constraint defined — accept as-is
        return FieldValue(
            raw_value=raw_value,
            validated_value=text,
            status=ExtractionStatus.VALID,
        )

    # Case-insensitive lookup — return the canonical version
    lookup = {v.lower(): v for v in field_def.allowed_values}
    canonical = lookup.get(text.lower())

    if canonical is not None:
        return FieldValue(
            raw_value=raw_value,
            validated_value=canonical,
            status=ExtractionStatus.VALID,
        )

    return FieldValue(
        raw_value=raw_value,
        validated_value=INVALID_VALUE,
        status=ExtractionStatus.INVALID,
        needs_review=True,
        review_note=(
            f"Field '{field_def.name}': '{text}' not in allowed values "
            f"{field_def.allowed_values}"
        ),
    )


# Canonical truthy / falsy tokens for checkbox normalisation
_TRUTHY_TOKENS = {"true", "yes", "1", "checked", "on", "✓", "☑"}
_FALSY_TOKENS = {"false", "no", "0", "unchecked", "off", "☐"}


def _validate_checkbox(
    field_def: FieldDefinition, raw_value: Any
) -> FieldValue:
    """Normalise a checkbox value to ``True`` or ``False``."""
    if isinstance(raw_value, bool):
        return FieldValue(
            raw_value=raw_value,
            validated_value=raw_value,
            status=ExtractionStatus.VALID,
        )

    token = str(raw_value).strip().lower()

    if token in _TRUTHY_TOKENS:
        return FieldValue(
            raw_value=raw_value,
            validated_value=True,
            status=ExtractionStatus.VALID,
        )
    if token in _FALSY_TOKENS:
        return FieldValue(
            raw_value=raw_value,
            validated_value=False,
            status=ExtractionStatus.VALID,
        )

    return FieldValue(
        raw_value=raw_value,
        validated_value=INVALID_VALUE,
        status=ExtractionStatus.INVALID,
        needs_review=True,
        review_note=(
            f"Field '{field_def.name}': cannot interpret "
            f"'{raw_value}' as checkbox (true/false)"
        ),
    )


# Common date formats to try when the schema doesn't specify one
_COMMON_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
    "%d-%m-%y",
    "%m/%d/%Y",
    "%Y/%m/%d",
]

# Map schema format tokens to strftime directives
_DATE_FORMAT_MAP: dict[str, str] = {
    "DD/MM/YYYY": "%d/%m/%Y",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD": "%Y-%m-%d",
    "MM/DD/YYYY": "%m/%d/%Y",
    "DD/MM/YY": "%d/%m/%y",
    "DD-MM-YY": "%d-%m-%y",
}


def _validate_date(
    field_def: FieldDefinition, raw_value: Any
) -> FieldValue:
    """Validate and parse a date field value."""
    text = str(raw_value).strip()

    if not text:
        if field_def.required:
            return FieldValue(
                raw_value=raw_value,
                validated_value=FIELD_MISSING,
                status=ExtractionStatus.MISSING,
                needs_review=True,
                review_note=f"Field '{field_def.name}': empty date (required)",
            )
        return FieldValue(
            raw_value=raw_value,
            validated_value=FIELD_MISSING,
            status=ExtractionStatus.MISSING,
        )

    # Convert any Devanagari digits in the date string
    text = convert_devanagari(text)

    # Determine format(s) to try
    formats_to_try: list[str] = []
    if field_def.date_format:
        fmt = _DATE_FORMAT_MAP.get(
            field_def.date_format.upper(), field_def.date_format
        )
        formats_to_try.append(fmt)
    formats_to_try.extend(_COMMON_DATE_FORMATS)

    for fmt in formats_to_try:
        try:
            parsed = datetime.strptime(text, fmt)
            # Return date as a normalised string in the FIRST matching format
            return FieldValue(
                raw_value=raw_value,
                validated_value=parsed.strftime(fmt),
                status=ExtractionStatus.VALID,
            )
        except ValueError:
            continue

    return FieldValue(
        raw_value=raw_value,
        validated_value=INVALID_VALUE,
        status=ExtractionStatus.INVALID,
        needs_review=True,
        review_note=(
            f"Field '{field_def.name}': cannot parse date '{raw_value}'"
        ),
    )
