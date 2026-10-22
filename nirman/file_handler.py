"""
File upload handling — validation, sanitization, duplicate-name resolution,
image collection, PDF-to-image conversion, and MIME-type detection.

All thresholds and extension sets are read from `nirman.config` so nothing is
hard-coded here.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from nirman import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Upload Validation
# ---------------------------------------------------------------------------

def validate_upload(filename: str, file_size_bytes: int) -> tuple[bool, str]:
    """Validate an uploaded file's name and size.

    Checks:
    - Extension is in ``config.SUPPORTED_EXTS``
    - File size does not exceed ``config.MAX_UPLOAD_SIZE_MB``
    - Filename contains no path-traversal sequences or dangerous characters

    Args:
        filename: The original filename supplied by the client.
        file_size_bytes: Size of the uploaded file in bytes.

    Returns:
        A ``(is_valid, error_message)`` tuple.  ``error_message`` is the empty
        string when the file is valid.
    """
    if not filename or not filename.strip():
        return False, "Filename is empty."

    # Reject path-traversal attempts in the raw filename BEFORE stripping.
    if ".." in filename:
        return False, "Filename contains path-traversal characters."

    # Strip any directory components the client may have sent.
    basename = Path(filename).name

    # Reject remaining path-traversal after stripping.
    if ".." in basename or "/" in basename or "\\" in basename:
        return False, "Filename contains path-traversal characters."

    # Reject control characters and a handful of risky shell metacharacters.
    if re.search(r'[<>:"|?*\x00-\x1f]', basename):
        return False, (
            "Filename contains invalid characters "
            "(angle brackets, quotes, pipe, question mark, asterisk, or control characters)."
        )

    # Extension check.
    ext = Path(basename).suffix.lower()
    if ext not in config.SUPPORTED_EXTS:
        supported = ", ".join(sorted(config.SUPPORTED_EXTS))
        return False, (
            f"Unsupported file extension '{ext}'. "
            f"Supported extensions: {supported}"
        )

    # Size check.
    max_bytes = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if file_size_bytes > max_bytes:
        size_mb = file_size_bytes / (1024 * 1024)
        return False, (
            f"File size ({size_mb:.1f} MB) exceeds the "
            f"{config.MAX_UPLOAD_SIZE_MB} MB limit."
        )

    if file_size_bytes <= 0:
        return False, "File is empty (0 bytes)."

    return True, ""


# ---------------------------------------------------------------------------
# 2. Safe / Unique Filename
# ---------------------------------------------------------------------------

def safe_filename(original_name: str, existing_names: set[str] | None = None) -> str:
    """Return a sanitized, unique filename.

    * Strips directory components.
    * Normalizes Unicode and collapses whitespace to underscores.
    * Removes characters that are unsafe on common filesystems.
    * Appends ``_1``, ``_2``, … when a collision is found in
      *existing_names*.

    Args:
        original_name: The raw filename (may include path components).
        existing_names: Set of filenames already present in the target
            directory.  ``None`` is treated as an empty set.

    Returns:
        A filesystem-safe, deduplicated filename preserving the original
        extension.
    """
    if existing_names is None:
        existing_names = set()

    # Take only the basename.
    basename = Path(original_name).name

    # Unicode normalization → ASCII-safe approximation where possible.
    basename = unicodedata.normalize("NFKD", basename)

    # Split stem / extension before sanitizing so we can preserve the ext.
    stem = Path(basename).stem
    ext = Path(basename).suffix.lower()

    # Replace whitespace runs with a single underscore.
    stem = re.sub(r"\s+", "_", stem)

    # Remove anything that isn't alphanumeric, underscore, hyphen, or dot.
    stem = re.sub(r"[^\w\-.]", "", stem, flags=re.ASCII)

    # Collapse consecutive underscores / hyphens.
    stem = re.sub(r"[_\-]{2,}", "_", stem)

    # Strip leading/trailing separators.
    stem = stem.strip("_-.")

    # Fallback if everything was stripped.
    if not stem:
        stem = "upload"

    candidate = f"{stem}{ext}"
    if candidate not in existing_names:
        return candidate

    # Deduplicate with a numeric suffix.
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{ext}"
        if candidate not in existing_names:
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# 3. Save Upload
# ---------------------------------------------------------------------------

def save_upload(
    file_storage: Any,
    destination_dir: str | Path,
    existing_names: set[str] | None = None,
) -> tuple[str, Optional[str]]:
    """Validate, sanitize, and persist an uploaded file.

    ``file_storage`` is expected to be a *file-like* object that supports
    ``.filename`` (or a ``filename`` attribute/key) and ``.save(path)``
    (Flask/Werkzeug ``FileStorage``) **or** ``.read()`` for generic streams.

    Args:
        file_storage: Uploaded file object.
        destination_dir: Directory to save the file into.
        existing_names: Already-used filenames (for deduplication).

    Returns:
        ``(safe_name, error)`` — on success *error* is ``None``.
    """
    if existing_names is None:
        existing_names = set()

    dest = Path(destination_dir)
    dest.mkdir(parents=True, exist_ok=True)

    # Resolve the original filename.
    original_name: str = getattr(file_storage, "filename", None) or ""
    if not original_name:
        return "", "No filename found on uploaded file object."

    # Determine file size — peek at the stream if needed.
    try:
        if hasattr(file_storage, "content_length") and file_storage.content_length:
            file_size = int(file_storage.content_length)
        elif hasattr(file_storage, "seek") and hasattr(file_storage, "tell"):
            pos = file_storage.tell()
            file_storage.seek(0, os.SEEK_END)
            file_size = file_storage.tell()
            file_storage.seek(pos)
        else:
            # Fallback: read content to get size (will re-read for save).
            content = file_storage.read()
            file_size = len(content)
            # Reset stream position if possible.
            if hasattr(file_storage, "seek"):
                file_storage.seek(0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not determine file size: %s", exc)
        file_size = 1  # Allow through — downstream will catch real problems.

    # Validate.
    is_valid, err_msg = validate_upload(original_name, file_size)
    if not is_valid:
        return "", err_msg

    # Build safe name.
    name = safe_filename(original_name, existing_names)
    target_path = dest / name

    # Persist.
    try:
        if hasattr(file_storage, "save"):
            file_storage.save(str(target_path))
        else:
            data = file_storage.read() if hasattr(file_storage, "read") else b""
            if hasattr(file_storage, "seek"):
                file_storage.seek(0)
            target_path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to save uploaded file '%s': %s", name, exc)
        return "", f"Failed to save file: {exc}"

    logger.info("Saved upload as '%s' → %s", name, target_path)
    return name, None


# ---------------------------------------------------------------------------
# 4. Collect Image Paths
# ---------------------------------------------------------------------------

def collect_image_paths(folder: str | Path) -> list[Path]:
    """Recursively collect all supported image files in *folder*.

    Only files whose extension is in ``config.SUPPORTED_IMAGE_EXTS`` are
    included.  Results are sorted by full path for deterministic ordering.

    Args:
        folder: Root directory to scan.

    Returns:
        Sorted list of ``Path`` objects pointing to image files.
    """
    root = Path(folder)
    if not root.is_dir():
        logger.warning("collect_image_paths: '%s' is not a directory.", root)
        return []

    images: list[Path] = []
    for entry in root.rglob("*"):
        if entry.is_file() and entry.suffix.lower() in config.SUPPORTED_IMAGE_EXTS:
            images.append(entry)

    images.sort()
    logger.info("Found %d image(s) in '%s'.", len(images), root)
    return images


# ---------------------------------------------------------------------------
# 5. PDF → Images
# ---------------------------------------------------------------------------

def pdf_to_images(
    pdf_path: str | Path,
    output_dir: str | Path,
) -> tuple[list[Path], Optional[str]]:
    """Convert each page of a PDF to a separate image file.

    Uses ``pdf2image`` (which wraps poppler) if available.  Falls back to a
    clear error message when the dependency is missing.

    Args:
        pdf_path: Path to the source PDF.
        output_dir: Directory to write the page images into.

    Returns:
        ``(image_paths, error)`` — on success *error* is ``None``.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)

    if not pdf_path.is_file():
        return [], f"PDF file not found: {pdf_path}"

    if pdf_path.suffix.lower() not in config.SUPPORTED_PDF_EXTS:
        return [], f"Not a PDF file: {pdf_path.name}"

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from pdf2image import convert_from_path  # type: ignore[import-untyped]
    except ImportError:
        msg = (
            "pdf2image is not installed.  Install it with:\n"
            "  pip install pdf2image\n"
            "You also need poppler installed on your system.\n"
            "  Windows: download from https://github.com/oschwartz10612/poppler-windows/releases\n"
            "  Linux:   sudo apt-get install poppler-utils\n"
            "  macOS:   brew install poppler"
        )
        logger.error(msg)
        return [], msg

    try:
        pages = convert_from_path(str(pdf_path))
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF conversion failed for '%s': %s", pdf_path.name, exc)
        return [], f"PDF conversion failed: {exc}"

    stem = pdf_path.stem
    image_paths: list[Path] = []

    for idx, page_img in enumerate(pages, start=1):
        img_name = f"{stem}_page_{idx}.png"
        img_path = output_dir / img_name
        try:
            page_img.save(str(img_path), "PNG")
            image_paths.append(img_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save page %d of '%s': %s", idx, pdf_path.name, exc)
            return image_paths, f"Failed to save page {idx}: {exc}"

    logger.info("Converted '%s' → %d page image(s).", pdf_path.name, len(image_paths))
    return image_paths, None


# ---------------------------------------------------------------------------
# 6. MIME Type Detection
# ---------------------------------------------------------------------------

def get_mime_type(filepath: str | Path) -> str:
    """Return the MIME type for *filepath* based on its extension.

    Falls back to ``"application/octet-stream"`` when the type cannot be
    determined.

    Args:
        filepath: Path (or filename) to inspect.

    Returns:
        A MIME type string, e.g. ``"image/jpeg"`` or ``"application/pdf"``.
    """
    mime, _ = mimetypes.guess_type(str(filepath))
    return mime or "application/octet-stream"
