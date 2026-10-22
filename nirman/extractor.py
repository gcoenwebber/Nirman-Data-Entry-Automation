"""
Schema-driven extraction pipeline.

Two-pass extraction preserved from the original system:
    Pass 1 — detect fields with crossed-out / corrected handwriting.
    Pass 2 — extract all field values using a dynamic, schema-built prompt.

Merges results, validates via ``validator.validate_field()``, and returns
a complete ``ExtractionRecord`` with raw + validated values, review flags,
and audit metadata.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from PIL import Image

from nirman import config
from nirman.ai_provider import call_gemini, parse_json_response
from nirman.models import (
    ExtractionRecord,
    ExtractionStatus,
    FieldType,
    FieldValue,
    FormSchema,
    RecordStatus,
)
from nirman.validator import validate_field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  PASS 1 — DETECT MESSY / CORRECTED FIELDS
# ═══════════════════════════════════════════════════════════════════

def detect_messy_fields(image_path: str, schema: FormSchema) -> set[str]:
    """Ask Gemini to identify fields with ambiguous or corrected handwriting.

    Opens its OWN PIL Image so the file pointer is not shared with the
    extraction call that follows.

    Args:
        image_path: Path to the form image.
        schema:     Form schema listing all expected fields.

    Returns:
        Set of field names that the AI flagged as messy.  Returns an
        empty set if this pass fails (non-fatal).
    """
    field_list = ", ".join(f.name for f in schema.fields)

    scan_prompt = f"""You are a quality-control inspector for handwritten data-entry forms.

SCRIPT NOTE
===========
Answers may be written in Devanagari numerals (e.g. १, २, ३, ४, ५)
or standard Arabic numerals (1, 2, 3, 4, 5). Treat both as equivalent digits
when assessing clarity — focus only on whether the writing is ambiguous,
crossed out, or corrected, regardless of which script is used.

TASK
====
Look at EVERY answer field in this form.
The fields to inspect are: {field_list}

For each field, check whether the handwritten answer shows ANY of:
  - crossing out / strike-through marks
  - scribbles or scratch marks over the original writing
  - overwriting (a digit written on top of another)
  - correction marks (e.g. eraser smudges, arrows, or other edits)
  - multiple values written in the same area making the intended value unclear

OUTPUT
======
Return ONLY a valid JSON object with a single key "messy_fields" whose value
is a list of field names (e.g. ["Q5", "Q26"]) that have ANY of the above
issues.  If no fields are messy, return {{"messy_fields": []}}.
No markdown fences, no extra text — pure JSON only.
"""
    try:
        img = Image.open(image_path)
        img.load()
        response = call_gemini([img, scan_prompt])
        data = parse_json_response(response)
        if data and isinstance(data.get("messy_fields"), list):
            messy = {f.strip() for f in data["messy_fields"] if isinstance(f, str)}
            if messy:
                logger.info("  Pre-scan flagged messy fields: %s", sorted(messy))
            return messy
    except Exception as exc:
        logger.warning(
            "  Pre-scan failed for %s: %s — skipping messy-field override",
            image_path, exc,
        )
    return set()


# ═══════════════════════════════════════════════════════════════════
#  PASS 2 — EXTRACT VALUES (schema-driven dynamic prompt)
# ═══════════════════════════════════════════════════════════════════

def _build_field_description(field) -> str:
    """Build a single-line description of a field for the extraction prompt."""
    parts = [f'"{field.name}"']

    if field.field_type in (FieldType.NUMERIC, FieldType.RATING):
        range_str = ""
        if field.min_value is not None and field.max_value is not None:
            range_str = f", valid range {int(field.min_value)}-{int(field.max_value)}"
        scale_str = f" (scale {field.scale_label}{range_str})" if field.scale_label else f" (numeric{range_str})"
        parts.append(scale_str)
    elif field.field_type == FieldType.TEXT:
        parts.append(" (text field)")
    elif field.field_type == FieldType.CATEGORICAL:
        if field.allowed_values:
            vals = ", ".join(field.allowed_values)
            parts.append(f' (categorical — one of: {vals})')
        else:
            parts.append(" (categorical)")
    elif field.field_type == FieldType.CHECKBOX:
        parts.append(" (checkbox: true/false)")
    elif field.field_type == FieldType.DATE:
        fmt = field.date_format or "any date format"
        parts.append(f" (date, format {fmt})")
    else:
        parts.append(f" ({field.field_type.value})")

    if field.label and field.label != field.name:
        parts.append(f' — "{field.label}"')

    return "   - " + "".join(parts)


def extract_values(image_path: str, schema: FormSchema) -> dict[str, Any] | None:
    """Extract all field values from a form image using the schema.

    Builds a dynamic prompt listing every field from the schema with its
    type and constraints, then calls Gemini.

    Args:
        image_path: Path to the form image.
        schema:     Form schema defining all fields to extract.

    Returns:
        Parsed dict of field_name → raw_value, or ``None`` on failure.
    """
    field_descriptions = "\n".join(
        _build_field_description(f) for f in schema.fields
    )

    # Build the output format example
    example_entries = []
    for f in schema.fields[:3]:  # show first 3 as example
        if f.field_type in (FieldType.NUMERIC, FieldType.RATING):
            example_entries.append(f'  "{f.name}": <number or "ILLEGIBLE" or "FIELD_MISSING">')
        elif f.field_type == FieldType.CHECKBOX:
            example_entries.append(f'  "{f.name}": <true/false or "ILLEGIBLE" or "FIELD_MISSING">')
        elif f.field_type == FieldType.CATEGORICAL:
            example_entries.append(f'  "{f.name}": <"value" or "ILLEGIBLE" or "FIELD_MISSING">')
        else:
            example_entries.append(f'  "{f.name}": <"text" or "ILLEGIBLE" or "FIELD_MISSING">')
    if len(schema.fields) > 3:
        example_entries.append("  ...")
    example_block = ",\n".join(example_entries)

    prompt = f"""You are an expert data-entry assistant specialising in handwritten and printed forms.

TASK
====
Carefully examine the entire form image — read every part of the page for context
(instructions, headers, scale labels, example rows) before extracting values.

FIELDS TO EXTRACT
=================
{field_descriptions}

RULES
=====
1. For numeric/rating fields, return a NUMBER within the stated valid range.
2. For text fields, return the text as a string.
3. For categorical fields, return one of the allowed values as a string.
4. For checkbox fields, return true or false (boolean).
5. For date fields, return the date as a string.
6. Only return a value if you are 95% or more confident. 
7. If the handwriting is crossed out, overwritten, corrected, scribbled, or
   otherwise ambiguous or unclear in any way, return the exact string
   "ILLEGIBLE" for that field — do NOT guess.
8. If a field is clearly blank / not filled in, return "FIELD_MISSING".
9. Do NOT invent values. Do NOT hallucinate.
10. Return ONLY valid JSON — no markdown fences, no extra text.
11. DEVANAGARI SCRIPT: Answers may be written in Devanagari numerals
    (०=0, १=1, २=2, ३=3, ४=4, ५=5, ६=6, ७=7, ८=8, ९=9).
    Convert any Devanagari digit to its Arabic equivalent before returning.
12. If you can estimate your confidence for each field (0.0 to 1.0), include
    a separate key "confidence" in the response with field_name → confidence.

OUTPUT FORMAT (strict JSON)
===========================
{{
{example_block},
  "confidence": {{
    "{schema.fields[0].name if schema.fields else 'field'}": 0.95,
    ...
  }}
}}

Return ALL {len(schema.fields)} field keys every time, even if the value is "FIELD_MISSING".
The "confidence" object is optional but encouraged.
"""

    try:
        img = Image.open(image_path)
        img.load()
        response = call_gemini([img, prompt])
        data = parse_json_response(response)
        if data is None:
            logger.error("Failed to parse JSON from Gemini response for %s", image_path)
        return data
    except Exception as exc:
        logger.error("Gemini API error for %s: %s", image_path, exc)
        return None


# ═══════════════════════════════════════════════════════════════════
#  MAIN EXTRACTION FUNCTION
# ═══════════════════════════════════════════════════════════════════

def extract_form(image_path: str, schema: FormSchema) -> ExtractionRecord:
    """Extract structured data from a form image using the given schema.

    Two-pass extraction:
        Pass 1 — detect messy/corrected fields → force them to ILLEGIBLE.
        Pass 2 — extract all values → validate against schema.

    Args:
        image_path: Path to the form image file.
        schema:     Form schema describing expected fields.

    Returns:
        A complete ``ExtractionRecord`` with both raw and validated values.
    """
    logger.info("Processing: %s", image_path)
    filename = os.path.basename(image_path)

    # ── Verify image is readable ──────────────────────────────
    try:
        img = Image.open(image_path)
        img.verify()
    except Exception as exc:
        logger.error("Cannot open image %s: %s", image_path, exc)
        return create_error_record(
            image_path, schema, config.API_ERROR,
            f"Cannot open image: {exc}",
        )

    # ── Pass 1: detect messy fields ───────────────────────────
    messy_fields = detect_messy_fields(image_path, schema)

    # ── Pass 2: extract values ────────────────────────────────
    raw_data = extract_values(image_path, schema)
    if raw_data is None:
        return create_error_record(
            image_path, schema, config.PARSE_ERROR,
            "Failed to extract or parse values from image",
        )

    # ── Extract confidence scores if provided ─────────────────
    confidence_map: dict[str, float] = {}
    if isinstance(raw_data.get("confidence"), dict):
        confidence_map = raw_data.pop("confidence")

    # ── Build the record ──────────────────────────────────────
    record = ExtractionRecord(
        schema_id=schema.schema_id,
        source_file=filename,
        safe_filename=filename,
        status=RecordStatus.SUCCESS,
    )

    for field_def in schema.fields:
        # Check if this field was flagged as messy in Pass 1
        if field_def.name in messy_fields:
            logger.warning(
                "  [OVERRIDE] %s: forced to ILLEGIBLE (messy handwriting in pre-scan)",
                field_def.name,
            )
            fv = FieldValue(
                raw_value=config.ILLEGIBLE,
                validated_value=config.ILLEGIBLE,
                status=ExtractionStatus.ILLEGIBLE,
                needs_review=True,
                review_note=f"Messy/corrected handwriting detected by pre-scan",
            )
        else:
            raw_val = raw_data.get(field_def.name, config.FIELD_MISSING)
            fv = validate_field(field_def, raw_val)

        # Add confidence if available
        if field_def.name in confidence_map:
            try:
                fv.confidence = float(confidence_map[field_def.name])
            except (ValueError, TypeError):
                pass

        record.fields[field_def.name] = fv

    # ── Compute review status ─────────────────────────────────
    flagged = [
        f"{name}: {fv.review_note}"
        for name, fv in record.fields.items()
        if fv.needs_review
    ]
    record.review_required = len(flagged) > 0
    record.review_notes = "; ".join(flagged) if flagged else ""

    # ── Compute overall status ────────────────────────────────
    total = len(schema.fields)
    valid_count = sum(
        1 for fv in record.fields.values()
        if fv.status == ExtractionStatus.VALID
    )
    if valid_count == total:
        record.status = RecordStatus.SUCCESS
    elif valid_count > 0:
        record.status = RecordStatus.PARTIAL
    else:
        record.status = RecordStatus.FAILED

    logger.info(
        "  [DONE] %s — Status: %s, Review Required: %s, Valid: %d/%d",
        filename, record.status.value, record.review_required, valid_count, total,
    )
    return record


# ═══════════════════════════════════════════════════════════════════
#  ERROR RECORD FACTORY
# ═══════════════════════════════════════════════════════════════════

def create_error_record(
    image_path: str,
    schema: FormSchema,
    sentinel: str,
    error_msg: str = "",
) -> ExtractionRecord:
    """Create a fully-populated error record when extraction fails fatally.

    Every field is set to the given sentinel value with status reflecting
    the error type.

    Args:
        image_path: Path to the form image.
        schema:     The form schema.
        sentinel:   Sentinel value to use (e.g. ``API_ERROR``, ``PARSE_ERROR``).
        error_msg:  Human-readable error description.

    Returns:
        An ``ExtractionRecord`` with ``status=FAILED`` and all fields set
        to the sentinel.
    """
    filename = os.path.basename(image_path)

    status_map = {
        config.API_ERROR: ExtractionStatus.API_ERROR,
        config.PARSE_ERROR: ExtractionStatus.PARSE_ERROR,
    }
    field_status = status_map.get(sentinel, ExtractionStatus.INVALID)

    record = ExtractionRecord(
        schema_id=schema.schema_id,
        source_file=filename,
        safe_filename=filename,
        status=RecordStatus.FAILED,
        review_required=True,
        review_notes=f"Fatal: {sentinel} — {error_msg}" if error_msg else f"Fatal: {sentinel}",
        error_message=error_msg,
    )

    for field_def in schema.fields:
        record.fields[field_def.name] = FieldValue(
            raw_value=sentinel,
            validated_value=sentinel,
            status=field_status,
            needs_review=True,
            review_note=f"Fatal: {sentinel}",
        )

    logger.error("  Error record for %s: %s — %s", filename, sentinel, error_msg)
    return record
