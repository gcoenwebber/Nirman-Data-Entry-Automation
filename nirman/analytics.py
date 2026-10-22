"""
Statistical analysis of batch extraction data.

Computes descriptive statistics for numeric, rating, and categorical fields
across a batch of extraction records. No causal claims, no normalisation unless
explicitly requested.
"""

from __future__ import annotations

import logging
import statistics as stats_module
from collections import Counter
from typing import Any

from nirman.config import ILLEGIBLE, FIELD_MISSING, ALL_SENTINELS
from nirman.models import (
    BatchAnalytics,
    CategoricalStatistics,
    ExtractionRecord,
    ExtractionStatus,
    FieldDefinition,
    FieldStatistics,
    FieldType,
    FormSchema,
    RecordStatus,
)

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────


def compute_analytics(
    records: list[ExtractionRecord],
    schema: FormSchema,
) -> BatchAnalytics:
    """Compute descriptive statistics for every field in *schema* across *records*.

    Parameters
    ----------
    records:
        Validated :class:`ExtractionRecord` objects (one per form).
    schema:
        The :class:`FormSchema` describing the fields.

    Returns
    -------
    BatchAnalytics
        Populated analytics model with numeric, rating, and categorical stats.
    """
    total_records = len(records)
    valid_records = sum(
        1 for r in records if r.status in (RecordStatus.SUCCESS, RecordStatus.PARTIAL)
    )
    records_needing_review = sum(1 for r in records if r.review_required)

    numeric_stats: list[FieldStatistics] = []
    categorical_stats: list[CategoricalStatistics] = []
    field_completeness: dict[str, float] = {}

    for field_def in schema.fields:
        if field_def.field_type in (FieldType.NUMERIC, FieldType.RATING):
            fs = _compute_numeric_stats(records, field_def)
            numeric_stats.append(fs)
            field_completeness[field_def.name] = (
                fs.valid_count / fs.total_count if fs.total_count > 0 else 0.0
            )
        elif field_def.field_type == FieldType.CATEGORICAL:
            cs = _compute_categorical_stats(records, field_def)
            categorical_stats.append(cs)
            field_completeness[field_def.name] = (
                cs.valid_count / cs.total_count if cs.total_count > 0 else 0.0
            )
        else:
            # For text / checkbox / date / other — just compute completeness
            valid_count, total = _count_valid(records, field_def.name)
            field_completeness[field_def.name] = (
                valid_count / total if total > 0 else 0.0
            )

    # Overall response rate: average of per-field completeness
    overall_response_rate: float | None = None
    if field_completeness:
        overall_response_rate = round(
            sum(field_completeness.values()) / len(field_completeness), 4
        )

    return BatchAnalytics(
        batch_id="",
        schema_name=schema.name,
        total_records=total_records,
        valid_records=valid_records,
        records_needing_review=records_needing_review,
        overall_response_rate=overall_response_rate,
        numeric_stats=numeric_stats,
        categorical_stats=categorical_stats,
        field_completeness=field_completeness,
    )


def field_summary_table(field_stats: FieldStatistics) -> dict[str, Any]:
    """Return a flat dict for the Excel summary sheet (numeric/rating field).

    Keys are human-readable column headers; values are display-ready.
    """
    summary: dict[str, Any] = {
        "Field": field_stats.field_label,
        "Type": field_stats.field_type.value,
        "Valid Count": field_stats.valid_count,
        "Missing Count": field_stats.missing_count,
        "Illegible Count": field_stats.illegible_count,
        "Invalid Count": field_stats.invalid_count,
        "Total Count": field_stats.total_count,
        "Response Rate": _fmt_pct(field_stats.response_rate),
        "Mean": _fmt_num(field_stats.mean),
        "Median": _fmt_num(field_stats.median),
        "Std Dev": _fmt_num(field_stats.std_dev),
        "Min": _fmt_num(field_stats.min_value),
        "Max": _fmt_num(field_stats.max_value),
        "Mode": field_stats.mode,
    }
    if field_stats.distribution:
        summary["Distribution"] = ", ".join(
            f"{k}: {v}" for k, v in sorted(field_stats.distribution.items())
        )
    return summary


def categorical_summary_table(cat_stats: CategoricalStatistics) -> dict[str, Any]:
    """Return a flat dict for the Excel summary sheet (categorical field)."""
    summary: dict[str, Any] = {
        "Field": cat_stats.field_label,
        "Type": "categorical",
        "Valid Count": cat_stats.valid_count,
        "Missing Count": cat_stats.missing_count,
        "Total Count": cat_stats.total_count,
        "Response Rate": _fmt_pct(cat_stats.response_rate),
        "Mode": cat_stats.mode,
        "Frequency": ", ".join(
            f"{k}: {v}" for k, v in sorted(cat_stats.frequency.items())
        ),
        "Proportions": ", ".join(
            f"{k}: {v:.2%}" for k, v in sorted(cat_stats.proportions.items())
        ),
    }
    return summary


# ── Internal helpers ──────────────────────────────────────────────────────────


def _classify_field_value(
    record: ExtractionRecord, field_name: str
) -> tuple[ExtractionStatus, Any]:
    """Return the (status, validated_value) for a field in a record."""
    fv = record.fields.get(field_name)
    if fv is None:
        return ExtractionStatus.MISSING, None
    return fv.status, fv.validated_value


def _count_valid(
    records: list[ExtractionRecord], field_name: str
) -> tuple[int, int]:
    """Return (valid_count, total_count) for a field across records."""
    total = len(records)
    valid = 0
    for r in records:
        status, val = _classify_field_value(r, field_name)
        if status == ExtractionStatus.VALID and val is not None and val not in ALL_SENTINELS:
            valid += 1
    return valid, total


def _compute_numeric_stats(
    records: list[ExtractionRecord],
    field_def: FieldDefinition,
) -> FieldStatistics:
    """Compute descriptive stats for a numeric or rating field."""
    valid_values: list[float] = []
    valid_count = 0
    missing_count = 0
    illegible_count = 0
    invalid_count = 0
    total_count = len(records)

    for rec in records:
        status, val = _classify_field_value(rec, field_def.name)

        if status == ExtractionStatus.MISSING:
            missing_count += 1
        elif status == ExtractionStatus.ILLEGIBLE:
            illegible_count += 1
        elif status in (
            ExtractionStatus.INVALID,
            ExtractionStatus.OUT_OF_RANGE,
            ExtractionStatus.API_ERROR,
            ExtractionStatus.PARSE_ERROR,
        ):
            invalid_count += 1
        elif status == ExtractionStatus.VALID:
            # Only include genuinely numeric values
            numeric_val = _to_float(val)
            if numeric_val is not None:
                valid_values.append(numeric_val)
                valid_count += 1
            else:
                # Value present but not convertible — treat as invalid
                invalid_count += 1

    # Compute statistics using stdlib statistics module
    mean: float | None = None
    median: float | None = None
    std_dev: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    mode: Any = None
    distribution: dict[str, int] | None = None

    if valid_values:
        mean = round(stats_module.mean(valid_values), 4)
        median = round(stats_module.median(valid_values), 4)
        min_value = min(valid_values)
        max_value = max(valid_values)

        if len(valid_values) >= 2:
            std_dev = round(stats_module.stdev(valid_values), 4)
        else:
            std_dev = 0.0

        try:
            mode = stats_module.mode(valid_values)
        except stats_module.StatisticsError:
            # No unique mode — pick lowest among the most common
            mode = Counter(valid_values).most_common(1)[0][0]

    # Rating distribution
    if field_def.field_type == FieldType.RATING and valid_values:
        dist_counter = Counter(valid_values)
        # Build distribution with string keys for JSON compatibility
        distribution = {str(k): v for k, v in sorted(dist_counter.items())}

    response_rate = round(valid_count / total_count, 4) if total_count > 0 else 0.0

    return FieldStatistics(
        field_name=field_def.name,
        field_label=field_def.label,
        field_type=field_def.field_type,
        valid_count=valid_count,
        missing_count=missing_count,
        illegible_count=illegible_count,
        invalid_count=invalid_count,
        total_count=total_count,
        mean=mean,
        median=median,
        std_dev=std_dev,
        min_value=min_value,
        max_value=max_value,
        mode=mode,
        distribution=distribution,
        response_rate=response_rate,
    )


def _compute_categorical_stats(
    records: list[ExtractionRecord],
    field_def: FieldDefinition,
) -> CategoricalStatistics:
    """Compute frequency distribution for a categorical field."""
    freq: Counter[str] = Counter()
    valid_count = 0
    missing_count = 0
    total_count = len(records)

    for rec in records:
        status, val = _classify_field_value(rec, field_def.name)

        if status == ExtractionStatus.VALID and val is not None and val not in ALL_SENTINELS:
            str_val = str(val).strip()
            if str_val:
                freq[str_val] += 1
                valid_count += 1
            else:
                missing_count += 1
        elif status == ExtractionStatus.MISSING:
            missing_count += 1
        else:
            missing_count += 1  # Illegible / error → counted as missing for cat

    frequency = dict(freq)
    proportions: dict[str, float] = {}
    if valid_count > 0:
        proportions = {k: round(v / valid_count, 4) for k, v in frequency.items()}

    mode: str | None = None
    if freq:
        mode = freq.most_common(1)[0][0]

    response_rate = round(valid_count / total_count, 4) if total_count > 0 else 0.0

    return CategoricalStatistics(
        field_name=field_def.name,
        field_label=field_def.label,
        valid_count=valid_count,
        missing_count=missing_count,
        total_count=total_count,
        frequency=frequency,
        proportions=proportions,
        mode=mode,
        response_rate=response_rate,
    )


def _to_float(value: Any) -> float | None:
    """Attempt to convert *value* to float; return ``None`` on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in ALL_SENTINELS or not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _fmt_num(value: float | None) -> str:
    """Format a numeric value for display, or return '—' for None."""
    if value is None:
        return "—"
    # Show integers cleanly
    if value == int(value):
        return str(int(value))
    return f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    """Format a proportion as percentage string."""
    if value is None:
        return "—"
    return f"{value:.1%}"
