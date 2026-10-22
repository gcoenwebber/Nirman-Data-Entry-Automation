"""
Professional Excel workbook generation for NIRMAN extraction results.

Creates a multi-sheet workbook with colour-coded data records, field
definitions, data quality metrics, and summary statistics.
"""

from __future__ import annotations

import logging
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from nirman.analytics import (
    categorical_summary_table,
    field_summary_table,
)
from nirman.config import (
    ALL_SENTINELS,
    API_ERROR,
    FIELD_MISSING,
    ILLEGIBLE,
    OUT_OF_RANGE,
    PARSE_ERROR,
    REVIEW_SENTINELS,
    FATAL_SENTINELS,
)
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

# ── Colour palette ────────────────────────────────────────────────────────────

_NAVY_FILL = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
_WHITE_FILL = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
_AMBER_FILL = PatternFill(start_color="FFD966", end_color="FFD966", fill_type="solid")
_RED_FILL = PatternFill(start_color="FF7575", end_color="FF7575", fill_type="solid")
_GOLD_FILL = PatternFill(start_color="FFC000", end_color="FFC000", fill_type="solid")
_LIGHT_BLUE_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
_LIGHT_GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
_LIGHT_RED_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
_LIGHT_AMBER_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_FONT_DARK = Font(bold=True, color="1F3864", size=11)
_NORMAL_FONT = Font(size=10)
_BOLD_FONT = Font(bold=True, size=10)
_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
_WRAP_ALIGN = Alignment(wrap_text=True, vertical="top")
_CENTER_ALIGN = Alignment(horizontal="center", vertical="center")

_MAX_COL_WIDTH = 40


# ── Public API ────────────────────────────────────────────────────────────────


def export_workbook(
    records: list[ExtractionRecord],
    schema: FormSchema,
    analytics: BatchAnalytics,
    output_path: str,
) -> str:
    """Create a multi-sheet Excel workbook and save it to *output_path*.

    Sheets:
        1. **Data Records** — one row per form with colour-coded values.
        2. **Field Definitions** — schema field catalogue.
        3. **Data Quality** — per-field quality metrics.
        4. **Summary Statistics** — descriptive stats & frequency tables.

    Returns
    -------
    str
        The *output_path* the workbook was written to.
    """
    wb = Workbook()

    # Sheet 1 — Data Records (the default sheet)
    ws_data = wb.active
    ws_data.title = "Data Records"  # type: ignore[union-attr]
    _write_data_records(ws_data, records, schema)  # type: ignore[arg-type]

    # Sheet 2 — Field Definitions
    ws_fields = wb.create_sheet("Field Definitions")
    _write_field_definitions(ws_fields, schema)

    # Sheet 3 — Data Quality
    ws_quality = wb.create_sheet("Data Quality")
    _write_data_quality(ws_quality, analytics)

    # Sheet 4 — Summary Statistics
    ws_stats = wb.create_sheet("Summary Statistics")
    _write_summary_statistics(ws_stats, analytics)

    try:
        wb.save(output_path)
        logger.info("Workbook saved to %s", output_path)
    except Exception:
        logger.exception("Failed to save workbook to %s", output_path)
        raise

    return output_path


def export_raw_data_sheet(
    ws: Worksheet,
    records: list[ExtractionRecord],
    schema: FormSchema,
) -> None:
    """Write a raw-data sheet with *both* raw and validated values.

    This helper can be called independently to add a raw-data tab to an
    existing workbook.
    """
    # Build header
    headers: list[str] = ["File Name", "Processed At", "Status"]
    for field_def in schema.fields:
        headers.append(f"{field_def.label} [raw]")
        headers.append(f"{field_def.label} [validated]")
        headers.append(f"{field_def.label} [status]")
    headers += ["Review Required", "Review Notes"]

    # Write header row
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _NAVY_FILL
        cell.font = _HEADER_FONT
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER_ALIGN

    # Write data rows
    for row_idx, record in enumerate(records, start=2):
        col = 1
        ws.cell(row=row_idx, column=col, value=record.source_file).border = _THIN_BORDER
        col += 1
        ws.cell(row=row_idx, column=col, value=record.processed_at).border = _THIN_BORDER
        col += 1
        ws.cell(row=row_idx, column=col, value=record.status.value).border = _THIN_BORDER
        col += 1

        for field_def in schema.fields:
            fv = record.fields.get(field_def.name)
            raw_val = fv.raw_value if fv else None
            val_val = fv.validated_value if fv else None
            status_val = fv.status.value if fv else ExtractionStatus.MISSING.value

            ws.cell(row=row_idx, column=col, value=_display_value(raw_val)).border = _THIN_BORDER
            col += 1
            ws.cell(row=row_idx, column=col, value=_display_value(val_val)).border = _THIN_BORDER
            col += 1
            ws.cell(row=row_idx, column=col, value=status_val).border = _THIN_BORDER
            col += 1

        ws.cell(row=row_idx, column=col, value=record.review_required).border = _THIN_BORDER
        col += 1
        ws.cell(row=row_idx, column=col, value=record.review_notes).border = _THIN_BORDER

    _freeze_and_autowidth(ws)


# ── Sheet writers (private) ───────────────────────────────────────────────────


def _write_data_records(
    ws: Worksheet,
    records: list[ExtractionRecord],
    schema: FormSchema,
) -> None:
    """Write Sheet 1 — colour-coded data records."""
    # Build header list
    headers: list[str] = ["File Name", "Processed At", "Status"]
    for field_def in schema.fields:
        headers.append(field_def.label)
    headers += ["Review Required", "Review Notes"]

    # Write header row
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _NAVY_FILL
        cell.font = _HEADER_FONT
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER_ALIGN

    # Map field column offsets for colouring
    field_col_start = 4  # columns 1-3 are File Name, Processed At, Status

    for row_idx, record in enumerate(records, start=2):
        # Fixed columns
        ws.cell(row=row_idx, column=1, value=record.source_file).border = _THIN_BORDER
        ws.cell(row=row_idx, column=2, value=record.processed_at).border = _THIN_BORDER
        ws.cell(row=row_idx, column=3, value=record.status.value).border = _THIN_BORDER

        # Field value columns
        for fi, field_def in enumerate(schema.fields):
            col = field_col_start + fi
            fv = record.fields.get(field_def.name)

            if fv is None:
                cell = ws.cell(row=row_idx, column=col, value=None)
                cell.fill = _AMBER_FILL
            else:
                cell = ws.cell(
                    row=row_idx,
                    column=col,
                    value=_display_value(fv.validated_value),
                )
                cell.fill = _cell_fill_for_status(fv.status, fv.validated_value)

            cell.border = _THIN_BORDER
            cell.font = _NORMAL_FONT

        # Review Required
        review_col = field_col_start + len(schema.fields)
        review_cell = ws.cell(
            row=row_idx, column=review_col, value=record.review_required
        )
        review_cell.border = _THIN_BORDER
        if record.review_required:
            review_cell.fill = _GOLD_FILL
            review_cell.font = _BOLD_FONT

        # Review Notes
        notes_col = review_col + 1
        notes_cell = ws.cell(
            row=row_idx, column=notes_col, value=record.review_notes
        )
        notes_cell.border = _THIN_BORDER
        notes_cell.alignment = _WRAP_ALIGN

    _freeze_and_autowidth(ws)


def _write_field_definitions(ws: Worksheet, schema: FormSchema) -> None:
    """Write Sheet 2 — field catalogue."""
    headers = [
        "Field Name",
        "Label",
        "Type",
        "Required",
        "Min",
        "Max",
        "Scale",
        "Allowed Values",
        "Group",
        "Description",
    ]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _LIGHT_BLUE_FILL
        cell.font = _HEADER_FONT_DARK
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER_ALIGN

    for row_idx, field_def in enumerate(schema.fields, start=2):
        values = [
            field_def.name,
            field_def.label,
            field_def.field_type.value,
            field_def.required,
            field_def.min_value,
            field_def.max_value,
            field_def.scale_label,
            ", ".join(field_def.allowed_values) if field_def.allowed_values else None,
            field_def.group,
            field_def.description,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = _THIN_BORDER
            cell.font = _NORMAL_FONT
            cell.alignment = _WRAP_ALIGN

    _freeze_and_autowidth(ws)


def _write_data_quality(ws: Worksheet, analytics: BatchAnalytics) -> None:
    """Write Sheet 3 — per-field data quality metrics."""
    headers = [
        "Field",
        "Valid Count",
        "Missing Count",
        "Illegible Count",
        "Invalid Count",
        "Total Count",
        "Response Rate",
    ]

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _NAVY_FILL
        cell.font = _HEADER_FONT
        cell.border = _THIN_BORDER
        cell.alignment = _CENTER_ALIGN

    row = 2
    totals = {
        "valid": 0,
        "missing": 0,
        "illegible": 0,
        "invalid": 0,
        "total": 0,
    }

    # Numeric / rating fields
    for fs in analytics.numeric_stats:
        _write_quality_row(ws, row, fs.field_label, fs.valid_count, fs.missing_count,
                           fs.illegible_count, fs.invalid_count, fs.total_count,
                           fs.response_rate)
        totals["valid"] += fs.valid_count
        totals["missing"] += fs.missing_count
        totals["illegible"] += fs.illegible_count
        totals["invalid"] += fs.invalid_count
        totals["total"] += fs.total_count
        row += 1

    # Categorical fields
    for cs in analytics.categorical_stats:
        illegible = 0  # categorical stats don't track illegible separately
        invalid = cs.missing_count  # non-valid are bucketed as missing
        _write_quality_row(ws, row, cs.field_label, cs.valid_count, cs.missing_count,
                           illegible, 0, cs.total_count, cs.response_rate)
        totals["valid"] += cs.valid_count
        totals["missing"] += cs.missing_count
        totals["illegible"] += illegible
        totals["total"] += cs.total_count
        row += 1

    # Summary row
    overall_rr = (
        round(totals["valid"] / totals["total"], 4) if totals["total"] > 0 else 0.0
    )
    row += 1  # blank separator
    summary_values = [
        "TOTAL",
        totals["valid"],
        totals["missing"],
        totals["illegible"],
        totals["invalid"],
        totals["total"],
        f"{overall_rr:.1%}",
    ]
    for col_idx, val in enumerate(summary_values, start=1):
        cell = ws.cell(row=row, column=col_idx, value=val)
        cell.fill = _LIGHT_BLUE_FILL
        cell.font = _BOLD_FONT
        cell.border = _THIN_BORDER

    _freeze_and_autowidth(ws)


def _write_quality_row(
    ws: Worksheet,
    row: int,
    label: str,
    valid: int,
    missing: int,
    illegible: int,
    invalid: int,
    total: int,
    response_rate: float | None,
) -> None:
    """Write a single quality-metrics row with conditional colouring."""
    values: list[Any] = [
        label, valid, missing, illegible, invalid, total,
        f"{response_rate:.1%}" if response_rate is not None else "—",
    ]

    for col_idx, val in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col_idx, value=val)
        cell.border = _THIN_BORDER
        cell.font = _NORMAL_FONT

    # Highlight problematic fields
    rr = response_rate if response_rate is not None else 1.0
    if rr < 0.5:
        ws.cell(row=row, column=1).fill = _LIGHT_RED_FILL
    elif rr < 0.8:
        ws.cell(row=row, column=1).fill = _LIGHT_AMBER_FILL
    else:
        ws.cell(row=row, column=1).fill = _LIGHT_GREEN_FILL


def _write_summary_statistics(ws: Worksheet, analytics: BatchAnalytics) -> None:
    """Write Sheet 4 — descriptive stats and frequency distributions."""
    row = 1

    # ── Section: Numeric / Rating fields ──────────────────────────
    if analytics.numeric_stats:
        # Section header
        cell = ws.cell(row=row, column=1, value="Numeric & Rating Field Statistics")
        cell.font = Font(bold=True, size=13, color="1F3864")
        row += 1

        stat_headers = [
            "Field", "Type", "N (valid)", "Response Rate",
            "Mean", "Median", "Std Dev", "Min", "Max", "Mode",
        ]
        for col_idx, h in enumerate(stat_headers, start=1):
            cell = ws.cell(row=row, column=col_idx, value=h)
            cell.fill = _NAVY_FILL
            cell.font = _HEADER_FONT
            cell.border = _THIN_BORDER
            cell.alignment = _CENTER_ALIGN
        row += 1

        for fs in analytics.numeric_stats:
            summary = field_summary_table(fs)
            values = [
                summary["Field"],
                summary["Type"],
                summary["Valid Count"],
                summary["Response Rate"],
                summary["Mean"],
                summary["Median"],
                summary["Std Dev"],
                summary["Min"],
                summary["Max"],
                summary["Mode"],
            ]
            for col_idx, val in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col_idx, value=val)
                cell.border = _THIN_BORDER
                cell.font = _NORMAL_FONT
            row += 1

        # Rating distributions
        rating_stats = [
            fs for fs in analytics.numeric_stats
            if fs.field_type == FieldType.RATING and fs.distribution
        ]
        if rating_stats:
            row += 1
            cell = ws.cell(row=row, column=1, value="Rating Distributions (count per value)")
            cell.font = Font(bold=True, size=12, color="1F3864")
            row += 1

            for fs in rating_stats:
                cell = ws.cell(row=row, column=1, value=fs.field_label)
                cell.font = _BOLD_FONT
                cell.border = _THIN_BORDER

                if fs.distribution:
                    col = 2
                    # Header sub-row
                    for key in sorted(fs.distribution.keys()):
                        header_cell = ws.cell(row=row, column=col, value=f"Rating {key}")
                        header_cell.fill = _LIGHT_BLUE_FILL
                        header_cell.font = _HEADER_FONT_DARK
                        header_cell.border = _THIN_BORDER
                        header_cell.alignment = _CENTER_ALIGN
                        col += 1

                    row += 1
                    ws.cell(row=row, column=1, value="Count").font = _BOLD_FONT
                    ws.cell(row=row, column=1).border = _THIN_BORDER
                    col = 2
                    for key in sorted(fs.distribution.keys()):
                        cell = ws.cell(row=row, column=col, value=fs.distribution[key])
                        cell.border = _THIN_BORDER
                        cell.alignment = _CENTER_ALIGN
                        col += 1
                row += 1

    # ── Section: Categorical fields ───────────────────────────────
    if analytics.categorical_stats:
        row += 1
        cell = ws.cell(row=row, column=1, value="Categorical Field Statistics")
        cell.font = Font(bold=True, size=13, color="1F3864")
        row += 1

        cat_headers = [
            "Field", "N (valid)", "Missing", "Total",
            "Response Rate", "Mode", "Frequency Distribution",
        ]
        for col_idx, h in enumerate(cat_headers, start=1):
            cell = ws.cell(row=row, column=col_idx, value=h)
            cell.fill = _NAVY_FILL
            cell.font = _HEADER_FONT
            cell.border = _THIN_BORDER
            cell.alignment = _CENTER_ALIGN
        row += 1

        for cs in analytics.categorical_stats:
            summary = categorical_summary_table(cs)
            values = [
                summary["Field"],
                summary["Valid Count"],
                summary["Missing Count"],
                summary["Total Count"],
                summary["Response Rate"],
                summary["Mode"],
                summary["Frequency"],
            ]
            for col_idx, val in enumerate(values, start=1):
                cell = ws.cell(row=row, column=col_idx, value=val)
                cell.border = _THIN_BORDER
                cell.font = _NORMAL_FONT
                if col_idx == 7:
                    cell.alignment = _WRAP_ALIGN
            row += 1

    # ── Footer: sample size note ──────────────────────────────────
    row += 2
    ws.cell(
        row=row, column=1,
        value=f"Total records: {analytics.total_records}  |  "
              f"Valid records: {analytics.valid_records}  |  "
              f"Records needing review: {analytics.records_needing_review}",
    ).font = Font(italic=True, size=10, color="666666")

    _freeze_and_autowidth(ws)


# ── Utility helpers ───────────────────────────────────────────────────────────


def _cell_fill_for_status(
    status: ExtractionStatus, value: Any
) -> PatternFill:
    """Return the background fill for a data cell based on its status/value."""
    # Check sentinel values first
    if isinstance(value, str) and value in ALL_SENTINELS:
        if value in FATAL_SENTINELS:
            return _RED_FILL
        return _AMBER_FILL

    if status in (ExtractionStatus.API_ERROR, ExtractionStatus.PARSE_ERROR):
        return _RED_FILL
    if status in (
        ExtractionStatus.ILLEGIBLE,
        ExtractionStatus.MISSING,
        ExtractionStatus.OUT_OF_RANGE,
    ):
        return _AMBER_FILL
    if status == ExtractionStatus.INVALID:
        return _AMBER_FILL

    return _WHITE_FILL


def _display_value(value: Any) -> Any:
    """Convert a value for display in Excel (keep sentinels as strings)."""
    if value is None:
        return None
    if isinstance(value, str) and value in ALL_SENTINELS:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, dict)):
        return str(value)
    return value


def _freeze_and_autowidth(ws: Worksheet) -> None:
    """Freeze panes at A2 and auto-size columns (capped at ``_MAX_COL_WIDTH``)."""
    ws.freeze_panes = "A2"

    for col_cells in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col_cells[0].column)  # type: ignore[union-attr]
        for cell in col_cells:
            try:
                cell_len = len(str(cell.value)) if cell.value is not None else 0
                max_length = max(max_length, cell_len)
            except Exception:  # noqa: BLE001
                pass
        adjusted = min(max_length + 3, _MAX_COL_WIDTH)
        ws.column_dimensions[col_letter].width = max(adjusted, 8)
