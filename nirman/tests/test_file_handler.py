"""
Tests for the NIRMAN file handler module.
"""

import pytest
from nirman.file_handler import validate_upload, safe_filename


class TestValidateUpload:
    def test_valid_jpg(self):
        ok, err = validate_upload("form.jpg", 1024 * 1024)
        assert ok is True
        assert err == ""

    def test_valid_png(self):
        ok, err = validate_upload("scan.png", 500_000)
        assert ok is True

    def test_unsupported_extension(self):
        ok, err = validate_upload("document.exe", 1024)
        assert ok is False
        assert "unsupported" in err.lower() or "not supported" in err.lower()

    def test_path_traversal(self):
        ok, err = validate_upload("../../etc/passwd.jpg", 1024)
        assert ok is False

    def test_empty_filename(self):
        ok, err = validate_upload("", 1024)
        assert ok is False


class TestSafeFilename:
    def test_no_collision(self):
        result = safe_filename("form.jpg", set())
        assert result == "form.jpg"

    def test_collision(self):
        result = safe_filename("form.jpg", {"form.jpg"})
        assert result == "form_1.jpg"

    def test_multiple_collisions(self):
        existing = {"form.jpg", "form_1.jpg", "form_2.jpg"}
        result = safe_filename("form.jpg", existing)
        assert result == "form_3.jpg"

    def test_preserves_extension(self):
        result = safe_filename("my scan.png", set())
        assert result.endswith(".png")

    def test_strips_path(self):
        result = safe_filename("C:\\Users\\test\\form.jpg", set())
        assert "\\" not in result
        assert "/" not in result
