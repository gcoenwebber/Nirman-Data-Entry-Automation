"""
Tests for the NIRMAN models module.
"""

import pytest
from nirman.models import (
    FieldDefinition, FieldType, FormSchema, FieldValue,
    ExtractionRecord, ExtractionStatus, RecordStatus,
    BatchResult, BatchAnalytics, FieldStatistics, CategoricalStatistics,
)


class TestFieldDefinition:
    def test_rating_field(self):
        f = FieldDefinition(
            name="Q1", label="Content Quality",
            field_type=FieldType.RATING,
            min_value=1, max_value=5, scale_label="1-5"
        )
        assert f.name == "Q1"
        assert f.field_type == FieldType.RATING
        assert f.min_value == 1
        assert f.max_value == 5

    def test_text_field(self):
        f = FieldDefinition(
            name="name", label="Name",
            field_type=FieldType.TEXT
        )
        assert f.field_type == FieldType.TEXT
        assert f.required is True

    def test_categorical_field(self):
        f = FieldDefinition(
            name="gender", label="Gender",
            field_type=FieldType.CATEGORICAL,
            allowed_values=["Male", "Female", "Other"]
        )
        assert f.allowed_values == ["Male", "Female", "Other"]


class TestFormSchema:
    @pytest.fixture
    def sample_schema(self):
        return FormSchema(
            name="Test Form",
            fields=[
                FieldDefinition(name="Q1", label="Q1", field_type=FieldType.RATING, min_value=1, max_value=5),
                FieldDefinition(name="Q2", label="Q2", field_type=FieldType.RATING, min_value=1, max_value=5),
                FieldDefinition(name="name", label="Name", field_type=FieldType.TEXT),
                FieldDefinition(name="gender", label="Gender", field_type=FieldType.CATEGORICAL, allowed_values=["M", "F"]),
            ]
        )

    def test_field_names(self, sample_schema):
        assert sample_schema.field_names() == ["Q1", "Q2", "name", "gender"]

    def test_get_field(self, sample_schema):
        f = sample_schema.get_field("Q1")
        assert f is not None
        assert f.field_type == FieldType.RATING

    def test_get_field_missing(self, sample_schema):
        assert sample_schema.get_field("nonexistent") is None

    def test_numeric_fields(self, sample_schema):
        nums = sample_schema.numeric_fields()
        assert len(nums) == 2
        assert all(f.field_type == FieldType.RATING for f in nums)

    def test_categorical_fields(self, sample_schema):
        cats = sample_schema.categorical_fields()
        assert len(cats) == 1
        assert cats[0].name == "gender"

    def test_schema_serialization(self, sample_schema):
        data = sample_schema.model_dump(mode="json")
        restored = FormSchema.model_validate(data)
        assert restored.name == sample_schema.name
        assert len(restored.fields) == len(sample_schema.fields)

    def test_schema_id_generated(self):
        s1 = FormSchema(name="A")
        s2 = FormSchema(name="B")
        assert s1.schema_id != s2.schema_id


class TestExtractionRecord:
    def test_empty_record(self):
        rec = ExtractionRecord(schema_id="test", source_file="form.jpg")
        assert rec.status == RecordStatus.PENDING
        assert not rec.review_required
        assert rec.fields == {}

    def test_flagged_fields(self):
        rec = ExtractionRecord(schema_id="test", source_file="form.jpg")
        rec.fields["Q1"] = FieldValue(raw_value=4, validated_value=4, status=ExtractionStatus.VALID)
        rec.fields["Q2"] = FieldValue(raw_value="ILLEGIBLE", validated_value="ILLEGIBLE",
                                       status=ExtractionStatus.ILLEGIBLE, needs_review=True)
        assert rec.flagged_fields() == ["Q2"]

    def test_get_value(self):
        rec = ExtractionRecord(schema_id="test", source_file="form.jpg")
        rec.fields["Q1"] = FieldValue(raw_value=4, validated_value=4, status=ExtractionStatus.VALID)
        assert rec.get_value("Q1") == 4
        assert rec.get_value("missing") is None


class TestBatchResult:
    def test_update_summary(self):
        batch = BatchResult(schema_id="test")
        batch.records["f1"] = ExtractionRecord(
            schema_id="test", source_file="f1.jpg", status=RecordStatus.SUCCESS
        )
        batch.records["f2"] = ExtractionRecord(
            schema_id="test", source_file="f2.jpg", status=RecordStatus.FAILED, review_required=True
        )
        batch.records["f3"] = ExtractionRecord(
            schema_id="test", source_file="f3.jpg", status=RecordStatus.PARTIAL, review_required=True
        )
        batch.update_summary()
        assert batch.total_forms == 3
        assert batch.processed_forms == 3
        assert batch.successful_forms == 1
        assert batch.failed_forms == 1
        assert batch.review_required_forms == 2
