"""
AI-powered analytical report generator for batch extraction results.

Builds structured prompts from validated analytics data and uses Gemini
to produce plain-language reports suitable for non-technical stakeholders.

Key invariant: all statistics in the report come from the BatchAnalytics
object — the SAME validated data that feeds Excel output.  The AI is
instructed to explain and contextualize these numbers, never to fabricate
its own.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from nirman.ai_provider import call_gemini, parse_json_response  # noqa: F401 – parse kept for future structured use
from nirman.config import OUTPUT_DIR
from nirman.models import (
    BatchAnalytics,
    CategoricalStatistics,
    ExtractionRecord,
    FieldStatistics,
    FieldType,
    FormSchema,
)

logger = logging.getLogger(__name__)

# ── Internal helpers ──────────────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    """Format a float for display, returning '—' for None."""
    if value is None:
        return "—"
    return f"{value:.{decimals}f}"


def _pct(value: Optional[float]) -> str:
    """Format an optional 0–1 float as a percentage string."""
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _build_numeric_stats_table(stats: list[FieldStatistics]) -> str:
    """Build a Markdown table of numeric/rating field statistics.

    Returns an empty string when there are no numeric stats.
    """
    if not stats:
        return ""

    header = (
        "| Field | Type | N (valid) | Missing | Mean | Median "
        "| Std Dev | Min | Max | Mode | Response Rate |\n"
        "|-------|------|-----------|---------|------|--------"
        "|---------|-----|-----|------|---------------|\n"
    )
    rows: list[str] = []
    for s in stats:
        row = (
            f"| {s.field_label} | {s.field_type.value} | {s.valid_count} "
            f"| {s.missing_count} | {_fmt(s.mean)} | {_fmt(s.median)} "
            f"| {_fmt(s.std_dev)} | {_fmt(s.min_value)} | {_fmt(s.max_value)} "
            f"| {s.mode if s.mode is not None else '—'} "
            f"| {_pct(s.response_rate)} |"
        )
        rows.append(row)

    return (
        "### Numeric & Rating Field Statistics\n\n"
        + header
        + "\n".join(rows)
    )


def _build_rating_distributions(stats: list[FieldStatistics]) -> str:
    """Build Markdown sub-sections for rating distribution breakdowns."""
    rating_fields = [
        s for s in stats
        if s.field_type == FieldType.RATING and s.distribution
    ]
    if not rating_fields:
        return ""

    sections: list[str] = []
    for s in rating_fields:
        lines = [f"#### {s.field_label} — Rating Distribution"]
        lines.append(f"(N = {s.valid_count} valid responses)\n")
        lines.append("| Rating | Count |")
        lines.append("|--------|-------|")
        for rating_value, count in sorted(s.distribution.items(), key=lambda kv: kv[0]):
            lines.append(f"| {rating_value} | {count} |")
        sections.append("\n".join(lines))

    return "### Rating Distributions\n\n" + "\n\n".join(sections)


def _build_categorical_stats_table(stats: list[CategoricalStatistics]) -> str:
    """Build Markdown sections for each categorical field."""
    if not stats:
        return ""

    sections: list[str] = []
    for s in stats:
        lines = [f"#### {s.field_label}"]
        lines.append(
            f"Valid: {s.valid_count} | Missing: {s.missing_count} "
            f"| Response Rate: {_pct(s.response_rate)}\n"
        )
        lines.append("| Value | Count | Proportion |")
        lines.append("|-------|-------|------------|")
        for val, count in sorted(s.frequency.items(), key=lambda kv: -kv[1]):
            prop = s.proportions.get(val)
            lines.append(f"| {val} | {count} | {_pct(prop)} |")
        if s.mode is not None:
            lines.append(f"\n**Mode:** {s.mode}")
        sections.append("\n".join(lines))

    return "### Categorical Field Distributions\n\n" + "\n\n".join(sections)


def _build_field_completeness_section(completeness: dict[str, float]) -> str:
    """Build a Markdown table of per-field completeness rates."""
    if not completeness:
        return ""

    lines = [
        "### Field Completeness (% of records with valid data)\n",
        "| Field | Completeness |",
        "|-------|-------------|",
    ]
    for field, rate in sorted(completeness.items(), key=lambda kv: kv[1]):
        lines.append(f"| {field} | {_pct(rate)} |")

    return "\n".join(lines)


def _build_field_definitions_section(schema: FormSchema) -> str:
    """Summarize every field in the schema for context."""
    lines = [
        "### Field Definitions (from schema)\n",
        "| Field | Label | Type | Scale / Values | Required | Group |",
        "|-------|-------|------|----------------|----------|-------|",
    ]
    for fd in schema.fields:
        scale = fd.scale_label or ""
        if fd.allowed_values:
            scale = ", ".join(fd.allowed_values[:10])
            if len(fd.allowed_values) > 10:
                scale += ", …"
        lines.append(
            f"| {fd.name} | {fd.label} | {fd.field_type.value} "
            f"| {scale} | {'Yes' if fd.required else 'No'} "
            f"| {fd.group or '—'} |"
        )
    return "\n".join(lines)


def _build_data_quality_section(analytics: BatchAnalytics) -> str:
    """Summarize high-level data quality metrics."""
    lines = [
        "### Data Quality Overview\n",
        f"- **Total records processed:** {analytics.total_records}",
        f"- **Records with fully valid extractions:** {analytics.valid_records}",
        f"- **Records flagged for review:** {analytics.records_needing_review}",
        f"- **Overall response rate:** {_pct(analytics.overall_response_rate)}",
    ]
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────


def build_report_prompt(analytics: BatchAnalytics, schema: FormSchema) -> str:
    """Build the full prompt sent to Gemini for report generation.

    The prompt embeds every available statistic as Markdown tables so the
    AI has no reason — and explicit instructions not — to invent numbers.

    Args:
        analytics: Validated batch analytics (same data as the Excel).
        schema:    The form schema with field definitions.

    Returns:
        A string prompt ready to pass to ``call_gemini``.
    """
    # ── assemble data sections ────────────────────────────────────
    data_sections: list[str] = [
        f"## Source: {schema.name} (schema v{schema.version})",
        f"## Batch ID: {analytics.batch_id}",
        "",
        _build_data_quality_section(analytics),
        "",
        _build_numeric_stats_table(analytics.numeric_stats),
        "",
        _build_rating_distributions(analytics.numeric_stats),
        "",
        _build_categorical_stats_table(analytics.categorical_stats),
        "",
        _build_field_completeness_section(analytics.field_completeness),
        "",
        _build_field_definitions_section(schema),
    ]

    data_block = "\n".join(s for s in data_sections if s)  # drop empties

    prompt = f"""\
You are an expert data analyst.  Below is a complete set of validated
statistics extracted from handwritten feedback forms.  Your task is to
write a professional, plain-language analytical report based EXCLUSIVELY
on the data provided.

=== BEGIN DATA ===
{data_block}
=== END DATA ===

Write a Markdown report with the following sections IN ORDER:

1. **Executive Summary**
   - 2-3 sentence overview of the dataset and key takeaways.
   - Include the total sample size (N = {analytics.total_records}).

2. **Key Findings — Numeric & Rating Fields**
   - For each numeric / rating field, state the mean, median, and
     response rate.  Always cite the sample size (N = valid_count for
     that field).
   - Highlight the TOP 3 highest-rated areas (strengths) and the
     BOTTOM 3 lowest-rated areas (areas for improvement).  Use the
     exact numbers from the table.
   - Describe the rating distributions where available, noting any
     skew or concentration.

3. **Key Findings — Categorical Fields**
   - Report frequency distributions and proportions.
   - Identify the most common response (mode) for each field.

4. **Patterns & Observations**
   - Identify notable patterns, clusters, or outliers ONLY where the
     data supports them (e.g., bimodal distributions, very low
     response rates on particular fields).
   - Do NOT speculate about causes.

5. **Methodology & Limitations**
   - State the sample size and overall response rate.
   - List any fields with low completeness (<70%) or high missing counts.
   - Note that data was extracted from handwritten forms via AI (OCR +
     LLM), which may introduce extraction errors.
   - Clearly state: "This report is descriptive. Correlations should
     not be interpreted as causal relationships."
   - If any normalization of ratings was applied, document the method
     and justification.

STRICT RULES — you MUST follow these:
- Use ONLY the numbers provided in the data above.  Do NOT invent,
  round differently, or extrapolate any statistics.
- Every statistic you cite MUST include the sample size
  (e.g., "mean = 4.12, N = 38").
- Do NOT claim causation.
- Do NOT fabricate quotes, comments, or qualitative data.
- Do NOT normalize or rescale ratings unless explicitly justified
  with a documented method in the Limitations section.
- Write for a non-technical audience.  Avoid jargon.
- Keep the report concise: aim for 500-800 words.

Return ONLY the Markdown report text, with no additional commentary.
"""
    return prompt


def generate_report_text(
    analytics: BatchAnalytics,
    schema: FormSchema,
    records: list[ExtractionRecord],
) -> str:
    """Generate an analytical report and return it as a string.

    This is the main entry-point used by the API / frontend.  The
    returned text is Markdown-formatted and suitable for direct display.

    Args:
        analytics: Validated batch analytics.
        schema:    Form schema with field definitions.
        records:   The extraction records (used for record count
                   verification, not re-analysed).

    Returns:
        The report text as a Markdown string.

    Raises:
        RuntimeError: If the AI call fails after retries.
    """
    logger.info(
        "Generating report text for batch %s (%d records, schema '%s')",
        analytics.batch_id,
        len(records),
        schema.name,
    )

    # Sanity-check: analytics and records should agree on counts
    if analytics.total_records != len(records):
        logger.warning(
            "Record count mismatch: analytics says %d, got %d records. "
            "Report will use analytics figures (same as Excel).",
            analytics.total_records,
            len(records),
        )

    prompt = build_report_prompt(analytics, schema)
    logger.debug("Report prompt length: %d chars", len(prompt))

    try:
        response = call_gemini(
            contents=[prompt],
            response_mime_type="text/plain",
        )
        report_text = response.text.strip() if response.text else ""
    except RuntimeError:
        raise
    except Exception:
        logger.exception("Gemini call failed during report generation")
        raise RuntimeError(
            "Failed to generate the analytical report. "
            "Please check API connectivity and try again."
        ) from None

    if not report_text:
        raise RuntimeError(
            "Gemini returned an empty response for the report."
        )

    # ── Append metadata footer ────────────────────────────────────
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    footer = (
        "\n\n---\n"
        f"*Report generated on {timestamp} by NIRMAN Data Automation Platform.*  \n"
        f"*Schema: {schema.name} v{schema.version} | "
        f"Batch: {analytics.batch_id} | "
        f"Records: {analytics.total_records}*\n"
    )
    report_text = report_text.strip() + footer

    logger.info("Report generated successfully (%d chars)", len(report_text))
    return report_text


def generate_report(
    analytics: BatchAnalytics,
    schema: FormSchema,
    records: list[ExtractionRecord],
    output_path: str,
) -> str:
    """Generate an analytical report and save it as a Markdown file.

    Delegates to :func:`generate_report_text` for content, then writes
    the result to disk.

    Args:
        analytics:   Validated batch analytics (same data as Excel).
        schema:      Form schema with field definitions.
        records:     The extraction records for the batch.
        output_path: Destination path for the ``.md`` file.  Parent
                     directories are created automatically.

    Returns:
        The absolute path to the saved report file.

    Raises:
        RuntimeError: If the AI call or file write fails.
    """
    report_text = generate_report_text(analytics, schema, records)

    # Ensure output directory exists
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        out.write_text(report_text, encoding="utf-8")
    except OSError:
        logger.exception("Failed to write report to %s", output_path)
        raise RuntimeError(
            f"Could not save the report to '{output_path}'. "
            "Check file permissions and disk space."
        ) from None

    logger.info("Report saved to %s", out.resolve())
    return str(out.resolve())
