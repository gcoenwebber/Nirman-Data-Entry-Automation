"""
Pydantic data models for the entire platform.
Defines form schemas, extraction records, batch results, and validation structures.
No hard-coded field names, scales, or form layouts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Field Type Enum ────────────────────────────────────────────

class FieldType(str, Enum):
    """Supported field types for form extraction."""
    NUMERIC = "numeric"
    RATING = "rating"
    TEXT = "text"
    CATEGORICAL = "categorical"
    CHECKBOX = "checkbox"
    DATE = "date"
    OTHER = "other"


# ── Extraction Status Enum ─────────────────────────────────────

class ExtractionStatus(str, Enum):
    """Status of a single field extraction."""
    VALID = "valid"
    ILLEGIBLE = "illegible"
    MISSING = "missing"
    OUT_OF_RANGE = "out_of_range"
    INVALID = "invalid"
    API_ERROR = "api_error"
    PARSE_ERROR = "parse_error"


class RecordStatus(str, Enum):
    """Overall status of a record extraction."""
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    PENDING = "pending"
    PROCESSING = "processing"


# ── Field Definition (part of a schema) ────────────────────────

class FieldDefinition(BaseModel):
    """Defines a single field on a form — its type, constraints, and metadata."""
    name: str = Field(..., description="Machine-readable field key, e.g. 'Q1', 'trainer_rating'")
    label: str = Field(..., description="Human-readable label, e.g. 'Content Quality'")
    field_type: FieldType = Field(..., description="Type of data in this field")
    required: bool = Field(default=True, description="Whether this field should be present on every form")
    description: str = Field(default="", description="Description of what this field captures")

    # Numeric / Rating constraints
    min_value: Optional[float] = Field(default=None, description="Minimum valid value (numeric/rating)")
    max_value: Optional[float] = Field(default=None, description="Maximum valid value (numeric/rating)")
    scale_label: Optional[str] = Field(default=None, description="Scale description, e.g. '1-5', '1-10'")

    # Categorical constraints
    allowed_values: Optional[list[str]] = Field(default=None, description="List of valid categorical values")

    # Date format
    date_format: Optional[str] = Field(default=None, description="Expected date format, e.g. 'DD/MM/YYYY'")

    # Group/section for organizing fields in output
    group: Optional[str] = Field(default=None, description="Logical group, e.g. 'Training Quality', 'Logistics'")


# ── Form Schema ────────────────────────────────────────────────

class FormSchema(BaseModel):
    """Complete schema for a form type — defines all fields to extract."""
    schema_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Form type name, e.g. 'Workshop Feedback Form'")
    description: str = Field(default="", description="Description of this form type")
    version: str = Field(default="1.0", description="Schema version")
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    source_sample: Optional[str] = Field(default=None, description="Filename of the sample used to generate this schema")
    fields: list[FieldDefinition] = Field(default_factory=list, description="List of field definitions")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional schema metadata")

    def field_names(self) -> list[str]:
        """Return list of all field names."""
        return [f.name for f in self.fields]

    def get_field(self, name: str) -> Optional[FieldDefinition]:
        """Get a field definition by name."""
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def field_groups(self) -> dict[str, list[FieldDefinition]]:
        """Group fields by their group attribute."""
        groups: dict[str, list[FieldDefinition]] = {}
        for f in self.fields:
            key = f.group or "Ungrouped"
            groups.setdefault(key, []).append(f)
        return groups

    def numeric_fields(self) -> list[FieldDefinition]:
        """Return fields that are numeric or rating type."""
        return [f for f in self.fields if f.field_type in (FieldType.NUMERIC, FieldType.RATING)]

    def categorical_fields(self) -> list[FieldDefinition]:
        """Return fields that are categorical type."""
        return [f for f in self.fields if f.field_type == FieldType.CATEGORICAL]


# ── Field Value (extracted from a single form) ─────────────────

class FieldValue(BaseModel):
    """Value extracted for a single field, with raw/validated distinction."""
    raw_value: Any = Field(default=None, description="Raw value from AI extraction")
    validated_value: Any = Field(default=None, description="Value after validation")
    status: ExtractionStatus = Field(default=ExtractionStatus.VALID, description="Extraction/validation status")
    confidence: Optional[float] = Field(default=None, description="AI confidence 0.0-1.0 if available")
    needs_review: bool = Field(default=False, description="Whether this field needs manual review")
    review_note: str = Field(default="", description="Explanation of why review is needed")


# ── Extraction Record (one form) ──────────────────────────────

class ExtractionRecord(BaseModel):
    """Complete extraction result for a single form image."""
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schema_id: str = Field(..., description="ID of the schema used for extraction")
    source_file: str = Field(..., description="Original filename")
    safe_filename: str = Field(default="", description="De-duplicated safe filename on disk")
    page_identifier: str = Field(default="1", description="Page number or identifier")
    processed_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    status: RecordStatus = Field(default=RecordStatus.PENDING, description="Overall extraction status")
    review_required: bool = Field(default=False, description="Whether any field needs review")
    review_notes: str = Field(default="", description="Summary of review issues")
    fields: dict[str, FieldValue] = Field(default_factory=dict, description="Extracted field values")
    error_message: str = Field(default="", description="Error message if extraction failed")

    def get_value(self, field_name: str) -> Any:
        """Get validated value for a field, or None."""
        fv = self.fields.get(field_name)
        return fv.validated_value if fv else None

    def get_raw_value(self, field_name: str) -> Any:
        """Get raw extracted value for a field, or None."""
        fv = self.fields.get(field_name)
        return fv.raw_value if fv else None

    def flagged_fields(self) -> list[str]:
        """Return names of fields that need review."""
        return [name for name, fv in self.fields.items() if fv.needs_review]

    def to_flat_dict(self, schema: FormSchema) -> dict[str, Any]:
        """Convert to flat dict for Excel export — uses validated values."""
        row: dict[str, Any] = {
            "File Name": self.source_file,
            "Processed At": self.processed_at,
            "Status": self.status.value,
        }
        for field_def in schema.fields:
            fv = self.fields.get(field_def.name)
            if fv:
                row[field_def.label] = fv.validated_value
                row[f"{field_def.label} [raw]"] = fv.raw_value
                row[f"{field_def.label} [status]"] = fv.status.value
            else:
                row[field_def.label] = None
                row[f"{field_def.label} [raw]"] = None
                row[f"{field_def.label} [status]"] = ExtractionStatus.MISSING.value
        row["Review Required"] = self.review_required
        row["Review Notes"] = self.review_notes
        return row


# ── Batch Result ──────────────────────────────────────────────

class BatchResult(BaseModel):
    """Result of processing a batch of forms."""
    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schema_id: str = Field(..., description="Schema used for this batch")
    schema_name: str = Field(default="", description="Schema name for display")
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    records: dict[str, ExtractionRecord] = Field(default_factory=dict, description="Records keyed by safe_filename")
    total_forms: int = Field(default=0)
    processed_forms: int = Field(default=0)
    successful_forms: int = Field(default=0)
    failed_forms: int = Field(default=0)
    review_required_forms: int = Field(default=0)

    def update_summary(self) -> None:
        """Recalculate summary counts from records."""
        self.total_forms = len(self.records)
        self.processed_forms = sum(
            1 for r in self.records.values()
            if r.status not in (RecordStatus.PENDING, RecordStatus.PROCESSING)
        )
        self.successful_forms = sum(
            1 for r in self.records.values() if r.status == RecordStatus.SUCCESS
        )
        self.failed_forms = sum(
            1 for r in self.records.values() if r.status == RecordStatus.FAILED
        )
        self.review_required_forms = sum(
            1 for r in self.records.values() if r.review_required
        )


# ── Analytics Models ──────────────────────────────────────────

class FieldStatistics(BaseModel):
    """Descriptive statistics for a single numeric/rating field."""
    field_name: str
    field_label: str
    field_type: FieldType
    valid_count: int = 0
    missing_count: int = 0
    illegible_count: int = 0
    invalid_count: int = 0
    total_count: int = 0
    mean: Optional[float] = None
    median: Optional[float] = None
    std_dev: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mode: Optional[Any] = None
    # For rating fields
    distribution: Optional[dict[str, int]] = None
    response_rate: Optional[float] = None


class CategoricalStatistics(BaseModel):
    """Frequency distribution for a categorical field."""
    field_name: str
    field_label: str
    valid_count: int = 0
    missing_count: int = 0
    total_count: int = 0
    frequency: dict[str, int] = Field(default_factory=dict)
    proportions: dict[str, float] = Field(default_factory=dict)
    mode: Optional[str] = None
    response_rate: Optional[float] = None


class BatchAnalytics(BaseModel):
    """Complete analytics for a processed batch."""
    batch_id: str
    schema_name: str
    total_records: int = 0
    valid_records: int = 0
    records_needing_review: int = 0
    overall_response_rate: Optional[float] = None
    numeric_stats: list[FieldStatistics] = Field(default_factory=list)
    categorical_stats: list[CategoricalStatistics] = Field(default_factory=list)
    field_completeness: dict[str, float] = Field(default_factory=dict)
