"""
Tests for the NIRMAN report generator module.
Tests the prompt building (no API calls needed).
"""

import pytest
from nirman.models import (
    FieldDefinition, FieldType, FormSchema,
    BatchAnalytics, FieldStatistics, CategoricalStatistics,
)
from nirman.report_generator import build_report_prompt


@pytest.fixture
def sample_analytics():
    return BatchAnalytics(
        batch_id="test-batch",
        schema_name="Test Form",
        total_records=10,
        valid_records=8,
        records_needing_review=2,
        overall_response_rate=0.85,
        numeric_stats=[
            FieldStatistics(
                field_name="Q1", field_label="Content Quality",
                field_type=FieldType.RATING,
                valid_count=9, missing_count=1, illegible_count=0,
                invalid_count=0, total_count=10,
                mean=4.2, median=4.0, std_dev=0.78,
                min_value=2.0, max_value=5.0, mode=4,
                distribution={"1": 0, "2": 1, "3": 1, "4": 4, "5": 3},
                response_rate=0.9,
            ),
        ],
        categorical_stats=[
            CategoricalStatistics(
                field_name="gender", field_label="Gender",
                valid_count=8, missing_count=2, total_count=10,
                frequency={"Male": 5, "Female": 3},
                proportions={"Male": 0.625, "Female": 0.375},
                mode="Male",
                response_rate=0.8,
            ),
        ],
        field_completeness={"Q1": 0.9, "gender": 0.8},
    )


@pytest.fixture
def sample_schema():
    return FormSchema(
        name="Test Form",
        fields=[
            FieldDefinition(name="Q1", label="Content Quality",
                           field_type=FieldType.RATING, min_value=1, max_value=5),
            FieldDefinition(name="gender", label="Gender",
                           field_type=FieldType.CATEGORICAL,
                           allowed_values=["Male", "Female"]),
        ]
    )


class TestReportGenerator:
    def test_prompt_contains_stats(self, sample_analytics, sample_schema):
        prompt = build_report_prompt(sample_analytics, sample_schema)
        assert "4.2" in prompt  # mean
        assert "Content Quality" in prompt
        assert "Male" in prompt
        assert "N = 10" in prompt or "10" in prompt

    def test_prompt_has_no_fabrication_instruction(self, sample_analytics, sample_schema):
        prompt = build_report_prompt(sample_analytics, sample_schema)
        assert "Do NOT invent" in prompt or "NOT invent" in prompt or "NOT fabricate" in prompt

    def test_prompt_has_causation_warning(self, sample_analytics, sample_schema):
        prompt = build_report_prompt(sample_analytics, sample_schema)
        assert "causal" in prompt.lower()

    def test_prompt_includes_sample_size(self, sample_analytics, sample_schema):
        prompt = build_report_prompt(sample_analytics, sample_schema)
        assert "10" in prompt  # total records

    def test_prompt_includes_methodology_section(self, sample_analytics, sample_schema):
        prompt = build_report_prompt(sample_analytics, sample_schema)
        assert "Methodology" in prompt or "Limitations" in prompt
