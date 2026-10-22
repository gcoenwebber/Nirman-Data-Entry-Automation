"""
Tests for the NIRMAN analytics module.
"""

import pytest
from nirman.models import (
    FieldDefinition, FieldType, FormSchema, FieldValue,
    ExtractionRecord, ExtractionStatus, RecordStatus,
)
from nirman.analytics import compute_analytics, field_summary_table, categorical_summary_table


@pytest.fixture
def rating_schema():
    return FormSchema(
        name="Test Form",
        fields=[
            FieldDefinition(name="Q1", label="Content", field_type=FieldType.RATING, min_value=1, max_value=5),
            FieldDefinition(name="Q2", label="Trainer", field_type=FieldType.RATING, min_value=1, max_value=5),
            FieldDefinition(name="gender", label="Gender", field_type=FieldType.CATEGORICAL,
                           allowed_values=["Male", "Female"]),
        ]
    )


def make_record(schema_id, filename, q1, q2, gender="Male", status=RecordStatus.SUCCESS):
    rec = ExtractionRecord(schema_id=schema_id, source_file=filename, status=status)
    if q1 is not None:
        rec.fields["Q1"] = FieldValue(raw_value=q1, validated_value=q1, status=ExtractionStatus.VALID)
    else:
        rec.fields["Q1"] = FieldValue(raw_value=None, validated_value="FIELD_MISSING",
                                       status=ExtractionStatus.MISSING, needs_review=True)
    if q2 is not None:
        rec.fields["Q2"] = FieldValue(raw_value=q2, validated_value=q2, status=ExtractionStatus.VALID)
    else:
        rec.fields["Q2"] = FieldValue(raw_value=None, validated_value="FIELD_MISSING",
                                       status=ExtractionStatus.MISSING, needs_review=True)
    if gender:
        rec.fields["gender"] = FieldValue(raw_value=gender, validated_value=gender, status=ExtractionStatus.VALID)
    else:
        rec.fields["gender"] = FieldValue(raw_value=None, validated_value="FIELD_MISSING",
                                           status=ExtractionStatus.MISSING)
    return rec


class TestComputeAnalytics:
    def test_basic_stats(self, rating_schema):
        records = [
            make_record("s", "f1.jpg", 4, 5),
            make_record("s", "f2.jpg", 3, 4),
            make_record("s", "f3.jpg", 5, 3),
        ]
        analytics = compute_analytics(records, rating_schema)

        assert analytics.total_records == 3
        assert analytics.valid_records == 3

        # Q1: values [4, 3, 5]
        q1_stats = analytics.numeric_stats[0]
        assert q1_stats.field_name == "Q1"
        assert q1_stats.valid_count == 3
        assert q1_stats.mean == 4.0
        assert q1_stats.min_value == 3
        assert q1_stats.max_value == 5

    def test_missing_values(self, rating_schema):
        records = [
            make_record("s", "f1.jpg", 4, 5),
            make_record("s", "f2.jpg", None, 4),
            make_record("s", "f3.jpg", 5, None),
        ]
        analytics = compute_analytics(records, rating_schema)

        q1_stats = analytics.numeric_stats[0]
        assert q1_stats.valid_count == 2
        assert q1_stats.missing_count == 1

    def test_categorical_stats(self, rating_schema):
        records = [
            make_record("s", "f1.jpg", 4, 5, gender="Male"),
            make_record("s", "f2.jpg", 3, 4, gender="Female"),
            make_record("s", "f3.jpg", 5, 3, gender="Male"),
        ]
        analytics = compute_analytics(records, rating_schema)

        cat = analytics.categorical_stats[0]
        assert cat.field_name == "gender"
        assert cat.frequency["Male"] == 2
        assert cat.frequency["Female"] == 1
        assert cat.mode == "Male"

    def test_empty_records(self, rating_schema):
        analytics = compute_analytics([], rating_schema)
        assert analytics.total_records == 0
        assert analytics.numeric_stats[0].valid_count == 0

    def test_single_record(self, rating_schema):
        records = [make_record("s", "f1.jpg", 4, 5)]
        analytics = compute_analytics(records, rating_schema)
        q1 = analytics.numeric_stats[0]
        assert q1.mean == 4.0
        assert q1.std_dev == 0.0  # single value

    def test_rating_distribution(self, rating_schema):
        records = [
            make_record("s", f"f{i}.jpg", v, v)
            for i, v in enumerate([1, 2, 3, 3, 4, 4, 4, 5, 5, 5])
        ]
        analytics = compute_analytics(records, rating_schema)
        q1 = analytics.numeric_stats[0]
        assert q1.distribution is not None
        assert q1.distribution.get("4.0") == 3 or q1.distribution.get("4") == 3

    def test_field_completeness(self, rating_schema):
        records = [
            make_record("s", "f1.jpg", 4, 5),
            make_record("s", "f2.jpg", None, 4),
        ]
        analytics = compute_analytics(records, rating_schema)
        assert analytics.field_completeness["Q1"] == 0.5
        assert analytics.field_completeness["Q2"] == 1.0


class TestSummaryTable:
    def test_field_summary_table(self, rating_schema):
        records = [make_record("s", "f1.jpg", 4, 5)]
        analytics = compute_analytics(records, rating_schema)
        table = field_summary_table(analytics.numeric_stats[0])
        assert "Field" in table
        assert "Mean" in table
        assert table["Valid Count"] == 1

    def test_categorical_summary_table(self, rating_schema):
        records = [make_record("s", "f1.jpg", 4, 5, gender="Male")]
        analytics = compute_analytics(records, rating_schema)
        table = categorical_summary_table(analytics.categorical_stats[0])
        assert table["Mode"] == "Male"
