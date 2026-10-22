"""
Tests for the NIRMAN validation module.
"""

import pytest
from nirman.models import FieldDefinition, FieldType, FieldValue, ExtractionStatus
from nirman.validator import (
    validate_field,
    validate_record,
    convert_devanagari,
    DEVANAGARI_DIGIT_MAP,
)
from nirman.config import ILLEGIBLE, FIELD_MISSING, OUT_OF_RANGE, INVALID_VALUE


# ── Devanagari Conversion ────────────────────────────────────

class TestDevanagariConversion:
    def test_basic_conversion(self):
        assert convert_devanagari("१२३") == "123"

    def test_mixed_text(self):
        assert convert_devanagari("रेटिंग: ४") == "रेटिंग: 4"

    def test_no_devanagari(self):
        assert convert_devanagari("hello 123") == "hello 123"

    def test_empty_string(self):
        assert convert_devanagari("") == ""

    def test_all_digits(self):
        assert convert_devanagari("०१२३४५६७८९") == "0123456789"


# ── Rating / Numeric Field Validation ────────────────────────

class TestNumericValidation:
    @pytest.fixture
    def rating_field(self):
        return FieldDefinition(
            name="Q1", label="Content Quality",
            field_type=FieldType.RATING,
            min_value=1, max_value=5, scale_label="1-5"
        )

    @pytest.fixture
    def numeric_field(self):
        return FieldDefinition(
            name="score", label="Total Score",
            field_type=FieldType.NUMERIC,
            min_value=0, max_value=100
        )

    def test_valid_int(self, rating_field):
        fv = validate_field(rating_field, 4)
        assert fv.validated_value == 4
        assert fv.status == ExtractionStatus.VALID
        assert not fv.needs_review

    def test_valid_float_as_int(self, rating_field):
        fv = validate_field(rating_field, 3.0)
        assert fv.validated_value == 3.0
        assert fv.status == ExtractionStatus.VALID

    def test_string_number(self, rating_field):
        fv = validate_field(rating_field, "4")
        assert fv.validated_value == 4
        assert fv.status == ExtractionStatus.VALID

    def test_devanagari_number(self, rating_field):
        fv = validate_field(rating_field, "३")
        assert fv.validated_value == 3
        assert fv.status == ExtractionStatus.VALID

    def test_out_of_range_below(self, rating_field):
        fv = validate_field(rating_field, 0)
        assert fv.validated_value == OUT_OF_RANGE
        assert fv.status == ExtractionStatus.OUT_OF_RANGE
        assert fv.needs_review

    def test_out_of_range_above(self, rating_field):
        fv = validate_field(rating_field, 7)
        assert fv.validated_value == OUT_OF_RANGE
        assert fv.status == ExtractionStatus.OUT_OF_RANGE
        assert fv.needs_review

    def test_illegible_string(self, rating_field):
        fv = validate_field(rating_field, "ILLEGIBLE")
        assert fv.validated_value == ILLEGIBLE
        assert fv.status == ExtractionStatus.ILLEGIBLE
        assert fv.needs_review

    def test_illegible_case_insensitive(self, rating_field):
        fv = validate_field(rating_field, "illegible")
        assert fv.validated_value == ILLEGIBLE
        assert fv.needs_review

    def test_field_missing_string(self, rating_field):
        fv = validate_field(rating_field, "FIELD_MISSING")
        assert fv.validated_value == FIELD_MISSING
        assert fv.status == ExtractionStatus.MISSING
        assert fv.needs_review  # required field

    def test_none_value(self, rating_field):
        fv = validate_field(rating_field, None)
        assert fv.validated_value == FIELD_MISSING
        assert fv.status == ExtractionStatus.MISSING

    def test_non_numeric_string(self, rating_field):
        fv = validate_field(rating_field, "abc")
        assert fv.validated_value == INVALID_VALUE
        assert fv.status == ExtractionStatus.INVALID
        assert fv.needs_review

    def test_never_converts_sentinel_to_zero(self, rating_field):
        """Sentinels must NEVER be silently converted to zero."""
        for sentinel in [ILLEGIBLE, FIELD_MISSING, OUT_OF_RANGE]:
            fv = validate_field(rating_field, sentinel)
            assert fv.validated_value != 0, f"{sentinel} was converted to 0!"

    def test_numeric_100_scale(self, numeric_field):
        fv = validate_field(numeric_field, 85)
        assert fv.validated_value == 85
        assert fv.status == ExtractionStatus.VALID


# ── Text Field Validation ────────────────────────────────────

class TestTextValidation:
    @pytest.fixture
    def text_field_required(self):
        return FieldDefinition(
            name="name", label="Participant Name",
            field_type=FieldType.TEXT, required=True
        )

    @pytest.fixture
    def text_field_optional(self):
        return FieldDefinition(
            name="comments", label="Comments",
            field_type=FieldType.TEXT, required=False
        )

    def test_valid_text(self, text_field_required):
        fv = validate_field(text_field_required, "John Doe")
        assert fv.validated_value == "John Doe"
        assert fv.status == ExtractionStatus.VALID

    def test_empty_required(self, text_field_required):
        fv = validate_field(text_field_required, "")
        assert fv.status == ExtractionStatus.MISSING
        assert fv.needs_review

    def test_empty_optional(self, text_field_optional):
        fv = validate_field(text_field_optional, None)
        assert fv.status == ExtractionStatus.MISSING
        assert not fv.needs_review  # optional

    def test_illegible_text(self, text_field_required):
        fv = validate_field(text_field_required, "ILLEGIBLE")
        assert fv.validated_value == ILLEGIBLE
        assert fv.status == ExtractionStatus.ILLEGIBLE


# ── Categorical Field Validation ─────────────────────────────

class TestCategoricalValidation:
    @pytest.fixture
    def cat_field(self):
        return FieldDefinition(
            name="gender", label="Gender",
            field_type=FieldType.CATEGORICAL,
            allowed_values=["Male", "Female", "Other"]
        )

    def test_valid_value(self, cat_field):
        fv = validate_field(cat_field, "Male")
        assert fv.validated_value == "Male"
        assert fv.status == ExtractionStatus.VALID

    def test_case_insensitive_match(self, cat_field):
        fv = validate_field(cat_field, "female")
        assert fv.validated_value == "Female"  # canonical form
        assert fv.status == ExtractionStatus.VALID

    def test_invalid_category(self, cat_field):
        fv = validate_field(cat_field, "Unknown")
        assert fv.validated_value == INVALID_VALUE
        assert fv.status == ExtractionStatus.INVALID
        assert fv.needs_review

    def test_no_constraints(self):
        field = FieldDefinition(
            name="role", label="Role",
            field_type=FieldType.CATEGORICAL
        )
        fv = validate_field(field, "Manager")
        assert fv.validated_value == "Manager"
        assert fv.status == ExtractionStatus.VALID


# ── Checkbox Field Validation ────────────────────────────────

class TestCheckboxValidation:
    @pytest.fixture
    def checkbox_field(self):
        return FieldDefinition(
            name="attended", label="Attended Previous",
            field_type=FieldType.CHECKBOX
        )

    def test_true_boolean(self, checkbox_field):
        fv = validate_field(checkbox_field, True)
        assert fv.validated_value is True

    def test_false_boolean(self, checkbox_field):
        fv = validate_field(checkbox_field, False)
        assert fv.validated_value is False

    def test_yes_string(self, checkbox_field):
        fv = validate_field(checkbox_field, "yes")
        assert fv.validated_value is True

    def test_no_string(self, checkbox_field):
        fv = validate_field(checkbox_field, "no")
        assert fv.validated_value is False

    def test_checked(self, checkbox_field):
        fv = validate_field(checkbox_field, "checked")
        assert fv.validated_value is True

    def test_invalid_checkbox(self, checkbox_field):
        fv = validate_field(checkbox_field, "maybe")
        assert fv.validated_value == INVALID_VALUE
        assert fv.needs_review


# ── Date Field Validation ────────────────────────────────────

class TestDateValidation:
    @pytest.fixture
    def date_field(self):
        return FieldDefinition(
            name="training_date", label="Training Date",
            field_type=FieldType.DATE,
            date_format="DD/MM/YYYY"
        )

    def test_valid_date(self, date_field):
        fv = validate_field(date_field, "15/03/2024")
        assert fv.status == ExtractionStatus.VALID
        assert fv.validated_value == "15/03/2024"

    def test_invalid_date(self, date_field):
        fv = validate_field(date_field, "not-a-date")
        assert fv.status == ExtractionStatus.INVALID
        assert fv.needs_review

    def test_devanagari_date(self, date_field):
        fv = validate_field(date_field, "१५/०३/२०२४")
        assert fv.status == ExtractionStatus.VALID
