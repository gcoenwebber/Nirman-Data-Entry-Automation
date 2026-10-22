"""
Sample Form Analyzer — generates a FormSchema from a single sample form image.

Workflow:
    1. User uploads one sample form image.
    2. `analyze_sample()` sends the image to Gemini with a structured prompt.
    3. Gemini identifies every visible field, its type, constraints, and grouping.
    4. The response is parsed into a `FormSchema` with `FieldDefinition` objects.
    5. Optionally, `refine_schema()` lets the user tweak the generated schema.

This module makes NO assumptions about form content — it handles feedback forms,
registration forms, surveys, checklists, invoices, etc.  Supports both printed
and handwritten forms, including Devanagari script.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from PIL import Image

from nirman.ai_provider import call_gemini, parse_json_response
from nirman.config import GEMINI_MODEL
from nirman.models import FieldDefinition, FieldType, FormSchema

logger = logging.getLogger(__name__)

# ── Internal constants ─────────────────────────────────────────

# Maps the string values the AI may return to our FieldType enum.
_FIELD_TYPE_MAP: dict[str, FieldType] = {
    "numeric": FieldType.NUMERIC,
    "rating": FieldType.RATING,
    "text": FieldType.TEXT,
    "categorical": FieldType.CATEGORICAL,
    "checkbox": FieldType.CHECKBOX,
    "date": FieldType.DATE,
    "other": FieldType.OTHER,
}

# ── Analysis prompt ────────────────────────────────────────────

_ANALYSIS_PROMPT = """You are an expert document analyst.  Examine the attached
form image carefully and produce a complete, structured JSON description of
every field visible on the form.

IMPORTANT RULES:
- Do NOT assume this is a feedback form, rating form, or any specific kind of
  form.  Describe exactly what you see.
- Include ALL fields — headers, names, dates, numbers, ratings, checkboxes,
  free-text areas, categorical choices, etc.
- Support handwritten AND printed content.  If some text is in Devanagari
  script (Hindi), transcribe labels in Devanagari as-is and note it.
- If a field's purpose is ambiguous, include a "notes" string explaining the
  ambiguity.
- Identify any scale labels (e.g. "1-5", "Poor to Excellent") and capture them.
- Group fields logically if the form has visible sections, headers, or numbered
  groups.

Return ONLY a valid JSON object (no markdown fences, no commentary) with this
exact structure:

{
  "form_name": "<descriptive name for this form type>",
  "form_description": "<one-sentence summary of what the form captures>",
  "has_devanagari": <true/false>,
  "notes": "<any general notes about ambiguity, handwriting quality, etc.>",
  "fields": [
    {
      "name": "<machine-readable key, e.g. 'Q1', 'participant_name'>",
      "label": "<human-readable label as printed on the form>",
      "field_type": "<one of: numeric, rating, text, categorical, checkbox, date, other>",
      "required": <true/false — true if it appears mandatory>,
      "description": "<brief description of what the field captures>",
      "min_value": <number or null — for numeric/rating fields>,
      "max_value": <number or null — for numeric/rating fields>,
      "scale_label": "<string like '1-5' or null — for rating fields>",
      "allowed_values": [<list of strings or null — for categorical fields>],
      "date_format": "<expected date format string or null — for date fields>",
      "group": "<section/group name or null>",
      "notes": "<any notes about uncertainty for this specific field>"
    }
  ]
}

Be thorough.  It is far better to include a field you are uncertain about
(with a note) than to silently omit it.
"""


# ── Public API ─────────────────────────────────────────────────

def analyze_sample(image_path: str) -> FormSchema:
    """Analyze a sample form image and generate a FormSchema.

    Opens the image with PIL, sends it to Gemini with a structured prompt,
    parses the JSON response, and returns a fully-populated ``FormSchema``.

    Args:
        image_path: Filesystem path to the sample form image.

    Returns:
        A ``FormSchema`` describing every field detected on the form.

    Raises:
        FileNotFoundError: If *image_path* does not exist.
        ValueError: If the AI response cannot be parsed into a valid schema.
        RuntimeError: If the AI call fails after retries.
    """
    # ------------------------------------------------------------------
    # 1. Validate & open image
    # ------------------------------------------------------------------
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Sample image not found: {image_path}")

    logger.info("Analyzing sample form: %s", path.name)

    image = Image.open(path)
    # Ensure the image is loaded (handles lazy-loading formats)
    image.load()

    # ------------------------------------------------------------------
    # 2. Call Gemini
    # ------------------------------------------------------------------
    logger.debug("Sending sample to Gemini model %s", GEMINI_MODEL)
    response = call_gemini(
        contents=[image, _ANALYSIS_PROMPT],
    )

    # ------------------------------------------------------------------
    # 3. Parse JSON from AI response
    # ------------------------------------------------------------------
    parsed: dict[str, Any] | None = parse_json_response(response)

    if parsed is None:
        raise RuntimeError(
            "Gemini returned an empty or unparseable response when analyzing the sample form."
        )

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected a JSON object from AI, got {type(parsed).__name__}."
        )

    form_name: str = parsed.get("form_name", "Untitled Form")
    form_description: str = parsed.get("form_description", "")
    raw_fields: list[dict[str, Any]] = parsed.get("fields", [])

    if not raw_fields:
        raise ValueError(
            "AI analysis returned zero fields.  The image may be unreadable "
            "or not a recognizable form."
        )

    # ------------------------------------------------------------------
    # 4. Build FieldDefinition objects
    # ------------------------------------------------------------------
    field_definitions: list[FieldDefinition] = []
    seen_names: set[str] = set()

    for idx, raw in enumerate(raw_fields):
        field_def = _build_field_definition(raw, idx, seen_names)
        field_definitions.append(field_def)
        seen_names.add(field_def.name)

    # ------------------------------------------------------------------
    # 5. Assemble FormSchema
    # ------------------------------------------------------------------
    schema = FormSchema(
        name=form_name,
        description=form_description,
        fields=field_definitions,
        source_sample=path.name,
        metadata={
            "has_devanagari": parsed.get("has_devanagari", False),
            "analysis_notes": parsed.get("notes", ""),
        },
    )

    logger.info(
        "Schema generated: '%s' with %d fields from %s",
        schema.name,
        len(schema.fields),
        path.name,
    )
    return schema


def refine_schema(schema: FormSchema, user_edits: dict[str, Any]) -> FormSchema:
    """Apply user-supplied edits to an existing FormSchema.

    Users can rename the form, update its description, and add / remove /
    modify individual fields.

    Args:
        schema: The current ``FormSchema`` to refine.
        user_edits: A dict that may contain any of the following keys:

            * ``"name"`` — new form name
            * ``"description"`` — new form description
            * ``"fields"`` — full replacement list of field dicts (same
              format returned by ``analyze_sample``).  Each dict follows the
              ``FieldDefinition`` structure.

    Returns:
        A **new** ``FormSchema`` instance with the edits applied.
        The ``schema_id`` and ``created_at`` are preserved from the original.
    """
    logger.info("Refining schema '%s' with user edits", schema.name)

    # Start from a copy so we don't mutate the original
    updated = schema.model_copy(deep=True)

    # ── Top-level properties ──────────────────────────────────
    if "name" in user_edits:
        updated.name = str(user_edits["name"])
    if "description" in user_edits:
        updated.description = str(user_edits["description"])

    # ── Fields replacement ────────────────────────────────────
    if "fields" in user_edits:
        raw_fields: list[dict[str, Any]] = user_edits["fields"]
        new_definitions: list[FieldDefinition] = []
        seen_names: set[str] = set()

        for idx, raw in enumerate(raw_fields):
            field_def = _build_field_definition(raw, idx, seen_names)
            new_definitions.append(field_def)
            seen_names.add(field_def.name)

        updated.fields = new_definitions

        logger.info(
            "Schema fields replaced: %d fields in refined schema",
            len(new_definitions),
        )

    # ── Metadata merging ──────────────────────────────────────
    if "metadata" in user_edits and isinstance(user_edits["metadata"], dict):
        updated.metadata.update(user_edits["metadata"])

    return updated


# ── Private helpers ────────────────────────────────────────────

def _build_field_definition(
    raw: dict[str, Any],
    index: int,
    seen_names: set[str],
) -> FieldDefinition:
    """Convert a raw dict (from AI or user edits) into a ``FieldDefinition``.

    Handles missing keys gracefully and de-duplicates names by appending a
    numeric suffix when collisions are detected.

    Args:
        raw: Dictionary with field properties.
        index: Positional index (used to generate a fallback name).
        seen_names: Set of names already used — updated externally by caller.

    Returns:
        A validated ``FieldDefinition``.
    """
    # ── Name (must be unique) ─────────────────────────────────
    name: str = str(raw.get("name", f"field_{index + 1}")).strip()
    if not name:
        name = f"field_{index + 1}"

    # De-duplicate
    original_name = name
    counter = 2
    while name in seen_names:
        name = f"{original_name}_{counter}"
        counter += 1

    # ── Label ─────────────────────────────────────────────────
    label: str = str(raw.get("label", name)).strip()

    # ── Field type ────────────────────────────────────────────
    raw_type = str(raw.get("field_type", "other")).strip().lower()
    field_type: FieldType = _FIELD_TYPE_MAP.get(raw_type, FieldType.OTHER)

    # ── Required flag ─────────────────────────────────────────
    required: bool = bool(raw.get("required", True))

    # ── Description ───────────────────────────────────────────
    description: str = str(raw.get("description", "")).strip()
    # Append any per-field notes from the AI into the description
    field_notes: str = str(raw.get("notes", "")).strip()
    if field_notes:
        description = f"{description}  [Note: {field_notes}]" if description else f"[Note: {field_notes}]"

    # ── Numeric / Rating constraints ──────────────────────────
    min_value = _parse_optional_float(raw.get("min_value"))
    max_value = _parse_optional_float(raw.get("max_value"))
    scale_label: str | None = raw.get("scale_label")
    if scale_label is not None:
        scale_label = str(scale_label).strip() or None

    # ── Categorical constraints ───────────────────────────────
    allowed_values: list[str] | None = None
    raw_allowed = raw.get("allowed_values")
    if isinstance(raw_allowed, list) and raw_allowed:
        allowed_values = [str(v) for v in raw_allowed]

    # ── Date format ───────────────────────────────────────────
    date_format: str | None = raw.get("date_format")
    if date_format is not None:
        date_format = str(date_format).strip() or None

    # ── Group ─────────────────────────────────────────────────
    group: str | None = raw.get("group")
    if group is not None:
        group = str(group).strip() or None

    return FieldDefinition(
        name=name,
        label=label,
        field_type=field_type,
        required=required,
        description=description,
        min_value=min_value,
        max_value=max_value,
        scale_label=scale_label,
        allowed_values=allowed_values,
        date_format=date_format,
        group=group,
    )


def _parse_optional_float(value: Any) -> float | None:
    """Safely convert a value to float, returning ``None`` on failure.

    Handles strings, ints, floats, and ``None``.  Non-numeric strings
    (including empty strings) return ``None`` without raising.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None
