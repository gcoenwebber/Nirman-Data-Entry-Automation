"""
CRUD operations for form schemas persisted as JSON files.

Schemas are stored in ``config.SCHEMAS_DIR`` with the naming convention
``{schema_id}.json``.  All serialization goes through Pydantic's
``model_dump_json`` / ``model_validate_json`` for type safety.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from nirman import config
from nirman.models import FieldType, FormSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _schema_path(schema_id: str) -> Path:
    """Return the canonical file path for a schema ID."""
    return config.SCHEMAS_DIR / f"{schema_id}.json"


# ---------------------------------------------------------------------------
# 1. Save Schema
# ---------------------------------------------------------------------------

def save_schema(schema: FormSchema) -> Path:
    """Persist a :class:`FormSchema` to disk as JSON.

    Creates ``config.SCHEMAS_DIR`` if it does not already exist.

    Args:
        schema: The schema to save.

    Returns:
        The ``Path`` the schema was written to.
    """
    config.SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    path = _schema_path(schema.schema_id)
    path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Saved schema '%s' (%s) → %s", schema.name, schema.schema_id, path)
    return path


# ---------------------------------------------------------------------------
# 2. Load Schema
# ---------------------------------------------------------------------------

def load_schema(schema_id: str) -> Optional[FormSchema]:
    """Load a :class:`FormSchema` from disk.

    Args:
        schema_id: Unique identifier of the schema.

    Returns:
        The loaded ``FormSchema``, or ``None`` if the file does not exist
        or cannot be parsed.
    """
    path = _schema_path(schema_id)
    if not path.is_file():
        logger.warning("Schema file not found: %s", path)
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        schema = FormSchema.model_validate_json(raw)
        return schema
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load schema '%s': %s", schema_id, exc)
        return None


# ---------------------------------------------------------------------------
# 3. List Schemas
# ---------------------------------------------------------------------------

def list_schemas() -> list[dict[str, Any]]:
    """Return summary information for every schema on disk.

    Scans ``config.SCHEMAS_DIR`` for ``*.json`` files and extracts lightweight
    metadata without loading the full field definitions into memory beyond
    what is needed for the count.

    Returns:
        A list of dicts, each containing:
        ``schema_id``, ``name``, ``created_at``, ``field_count``.
    """
    schemas_dir = config.SCHEMAS_DIR
    if not schemas_dir.is_dir():
        return []

    summaries: list[dict[str, Any]] = []
    for json_file in sorted(schemas_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            summaries.append({
                "schema_id": data.get("schema_id", json_file.stem),
                "name": data.get("name", "Untitled"),
                "created_at": data.get("created_at", ""),
                "field_count": len(data.get("fields", [])),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping unreadable schema file '%s': %s", json_file.name, exc)

    return summaries


# ---------------------------------------------------------------------------
# 4. Delete Schema
# ---------------------------------------------------------------------------

def delete_schema(schema_id: str) -> bool:
    """Delete a schema file from disk.

    Args:
        schema_id: Unique identifier of the schema to delete.

    Returns:
        ``True`` if the file was found and removed, ``False`` otherwise.
    """
    path = _schema_path(schema_id)
    if not path.is_file():
        logger.warning("Cannot delete — schema not found: %s", path)
        return False

    try:
        path.unlink()
        logger.info("Deleted schema '%s' → %s", schema_id, path)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to delete schema '%s': %s", schema_id, exc)
        return False


# ---------------------------------------------------------------------------
# 5. Update Schema
# ---------------------------------------------------------------------------

def update_schema(schema: FormSchema) -> Path:
    """Overwrite an existing schema file, preserving the ``schema_id``.

    This is functionally identical to :func:`save_schema` but communicates
    intent — the caller expects the file to already exist.

    Args:
        schema: The updated schema (``schema_id`` must match an existing file).

    Returns:
        The ``Path`` the schema was written to.

    Raises:
        FileNotFoundError: If no existing schema with that ID is found.
    """
    path = _schema_path(schema.schema_id)
    if not path.is_file():
        raise FileNotFoundError(
            f"Cannot update — schema '{schema.schema_id}' does not exist on disk."
        )
    path.write_text(schema.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Updated schema '%s' (%s).", schema.name, schema.schema_id)
    return path


# ---------------------------------------------------------------------------
# 6. Default Schema
# ---------------------------------------------------------------------------

def get_default_schema() -> None:
    """Return the default form schema.

    There is no built-in default — a sample form must be analyzed first to
    generate a schema.  This function always returns ``None``.
    """
    return None


# ---------------------------------------------------------------------------
# 7. Schema Summary
# ---------------------------------------------------------------------------

def schema_to_summary(schema: FormSchema) -> dict[str, Any]:
    """Build a human-readable summary dict for a :class:`FormSchema`.

    Useful for display in a UI or logging without exposing the full schema
    internals.

    Args:
        schema: The schema to summarise.

    Returns:
        A dict with keys: ``schema_id``, ``name``, ``description``,
        ``version``, ``created_at``, ``source_sample``, ``field_count``,
        ``required_field_count``, ``groups``, and ``fields`` (a list of
        per-field mini-summaries).
    """
    field_summaries: list[dict[str, Any]] = []
    for f in schema.fields:
        entry: dict[str, Any] = {
            "name": f.name,
            "label": f.label,
            "type": f.field_type.value,
            "required": f.required,
        }
        if f.field_type in (FieldType.NUMERIC, FieldType.RATING):
            if f.min_value is not None or f.max_value is not None:
                entry["range"] = f"{f.min_value} – {f.max_value}"
            if f.scale_label:
                entry["scale"] = f.scale_label
        if f.field_type == FieldType.CATEGORICAL and f.allowed_values:
            entry["allowed_values"] = f.allowed_values
        if f.field_type == FieldType.DATE and f.date_format:
            entry["date_format"] = f.date_format
        if f.group:
            entry["group"] = f.group
        field_summaries.append(entry)

    groups = sorted({f.group for f in schema.fields if f.group})

    return {
        "schema_id": schema.schema_id,
        "name": schema.name,
        "description": schema.description,
        "version": schema.version,
        "created_at": schema.created_at,
        "source_sample": schema.source_sample,
        "field_count": len(schema.fields),
        "required_field_count": sum(1 for f in schema.fields if f.required),
        "groups": groups,
        "fields": field_summaries,
    }
