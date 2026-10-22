"""
Tests for the NIRMAN schema manager and sample analyzer modules.
"""

import os
import json
import pytest
from nirman.models import FieldDefinition, FieldType, FormSchema
from nirman.schema_manager import save_schema, load_schema, list_schemas, delete_schema, schema_to_summary


@pytest.fixture
def sample_schema():
    return FormSchema(
        name="Workshop Feedback",
        description="A test feedback form",
        fields=[
            FieldDefinition(name="Q1", label="Content Quality", field_type=FieldType.RATING,
                           min_value=1, max_value=5, scale_label="1-5", group="Training"),
            FieldDefinition(name="Q2", label="Trainer Performance", field_type=FieldType.RATING,
                           min_value=1, max_value=5, scale_label="1-5", group="Training"),
            FieldDefinition(name="name", label="Participant Name", field_type=FieldType.TEXT,
                           required=False),
        ]
    )


class TestSchemaManager:
    def test_save_and_load(self, sample_schema, tmp_path, monkeypatch):
        import nirman.config
        monkeypatch.setattr(nirman.config, "SCHEMAS_DIR", tmp_path)

        path = save_schema(sample_schema)
        assert os.path.isfile(str(path))

        loaded = load_schema(sample_schema.schema_id)
        assert loaded is not None
        assert loaded.name == sample_schema.name
        assert len(loaded.fields) == 3

    def test_load_nonexistent(self, tmp_path, monkeypatch):
        import nirman.config
        monkeypatch.setattr(nirman.config, "SCHEMAS_DIR", tmp_path)

        result = load_schema("nonexistent-id")
        assert result is None

    def test_list_schemas(self, sample_schema, tmp_path, monkeypatch):
        import nirman.config
        monkeypatch.setattr(nirman.config, "SCHEMAS_DIR", tmp_path)

        save_schema(sample_schema)
        schemas = list_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "Workshop Feedback"
        assert schemas[0]["field_count"] == 3

    def test_delete_schema(self, sample_schema, tmp_path, monkeypatch):
        import nirman.config
        monkeypatch.setattr(nirman.config, "SCHEMAS_DIR", tmp_path)

        save_schema(sample_schema)
        assert delete_schema(sample_schema.schema_id) is True
        assert load_schema(sample_schema.schema_id) is None

    def test_delete_nonexistent(self, tmp_path, monkeypatch):
        import nirman.config
        monkeypatch.setattr(nirman.config, "SCHEMAS_DIR", tmp_path)

        assert delete_schema("nonexistent") is False

    def test_schema_summary(self, sample_schema):
        summary = schema_to_summary(sample_schema)
        assert summary["name"] == "Workshop Feedback"
        assert summary["field_count"] == 3
        assert "Training" in summary["groups"]


class TestSampleAnalyzerRefinement:
    def test_refine_schema(self, sample_schema):
        from nirman.sample_analyzer import refine_schema

        edits = {
            "name": "Updated Form",
            "fields": [
                {"name": "Q1", "label": "Updated Label", "field_type": "rating",
                 "min_value": 1, "max_value": 10, "required": True},
            ]
        }
        updated = refine_schema(sample_schema, edits)
        assert updated.name == "Updated Form"
        assert len(updated.fields) == 1
        assert updated.fields[0].label == "Updated Label"
        assert updated.fields[0].max_value == 10
        # Schema ID preserved
        assert updated.schema_id == sample_schema.schema_id

    def test_refine_preserves_id(self, sample_schema):
        from nirman.sample_analyzer import refine_schema

        edits = {"name": "New Name"}
        updated = refine_schema(sample_schema, edits)
        assert updated.schema_id == sample_schema.schema_id
        assert updated.name == "New Name"
        assert len(updated.fields) == 3  # fields not changed
