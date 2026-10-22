"""
Centralised audit-logging for the NIRMAN Data Automation Platform.

Sets up a single UTF-8 file handler writing to ``config.LOG_FILE`` and exposes
convenience helpers for structured, privacy-aware log entries.

**Design decisions**:

* **No StreamHandler** — emoji characters and extended Unicode in log messages
  crash the default Windows ``cp1252`` console.
* **Never logs API keys**, full form content, or unnecessarily sensitive data.
* Raw values longer than ``_MAX_RAW_DISPLAY`` characters are redacted in logs.
"""

from __future__ import annotations

import logging
from typing import Optional

from nirman import config

# ── Constants ──────────────────────────────────────────────────────────

_MAX_RAW_DISPLAY: int = 120  # Truncate raw values beyond this length

_LOG_FORMAT = "%(asctime)s  [%(levelname)s]  %(message)s"

# ── Root logger setup (runs once at import time) ──────────────────────

_configured: bool = False


def _configure_root_logger() -> None:
    """Attach a UTF-8 file handler to the root logger if not already done.

    Calling this more than once is safe — subsequent calls are no-ops.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Prevent duplicate handlers if the module is somehow re-imported
    for handler in root.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            if handler.baseFilename == str(config.LOG_FILE.resolve()):
                _configured = True
                return

    file_handler = logging.FileHandler(
        str(config.LOG_FILE),
        encoding="utf-8",
        mode="a",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)
    _configured = True


# Auto-configure on first import
_configure_root_logger()


# ── Public helpers ─────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """Return a named logger under the existing root configuration.

    Parameters
    ----------
    name : str
        Logical name for the logger (typically ``__name__`` of the caller).
    """
    # Ensure root configuration is applied (idempotent)
    _configure_root_logger()
    return logging.getLogger(name)


# ── Structured log helpers ─────────────────────────────────────────────

_audit = get_logger("nirman.audit")


def log_extraction_start(filename: str) -> None:
    """Log the beginning of extraction for a file.

    Parameters
    ----------
    filename : str
        The source filename being processed.
    """
    _audit.info("EXTRACTION_START | file=%s", filename)


def log_extraction_complete(
    filename: str,
    status: str,
    review_required: bool,
) -> None:
    """Log the completion of extraction for a file.

    Parameters
    ----------
    filename : str
        The source filename that was processed.
    status : str
        Final status (e.g. ``"success"``, ``"partial"``, ``"failed"``).
    review_required : bool
        Whether manual review has been flagged.
    """
    _audit.info(
        "EXTRACTION_COMPLETE | file=%s | status=%s | review_required=%s",
        filename,
        status,
        review_required,
    )


def log_api_call(
    model: str,
    tokens_in: Optional[int],
    tokens_out: Optional[int],
) -> None:
    """Log an API call's token usage **without** exposing the API key.

    Parameters
    ----------
    model : str
        Name of the Gemini model used.
    tokens_in : int or None
        Prompt / input token count.
    tokens_out : int or None
        Candidate / output token count.
    """
    _audit.info(
        "API_CALL | model=%s | tokens_in=%s | tokens_out=%s",
        model,
        tokens_in,
        tokens_out,
    )


def log_validation_issue(
    filename: str,
    field_name: str,
    issue: str,
    raw_value: object,
) -> None:
    """Log a validation problem for a specific field.

    Long ``raw_value`` strings are truncated to prevent log bloat and to
    avoid accidentally persisting sensitive content.

    Parameters
    ----------
    filename : str
        Source filename.
    field_name : str
        Machine-readable field key.
    issue : str
        Short description of the validation issue.
    raw_value : object
        The original extracted value (will be redacted if too long).
    """
    safe_raw = _redact(raw_value)
    _audit.warning(
        "VALIDATION_ISSUE | file=%s | field=%s | issue=%s | raw_value=%s",
        filename,
        field_name,
        issue,
        safe_raw,
    )


def log_batch_summary(
    total: int,
    success: int,
    failed: int,
    review: int,
) -> None:
    """Log aggregate results for a batch run.

    Parameters
    ----------
    total : int
        Total number of forms in the batch.
    success : int
        Successfully extracted (no review flags).
    failed : int
        Extraction failures.
    review : int
        Forms requiring manual review.
    """
    _audit.info(
        "BATCH_SUMMARY | total=%d | success=%d | failed=%d | review=%d",
        total,
        success,
        failed,
        review,
    )


# ── Internal utilities ─────────────────────────────────────────────────

def _redact(value: object) -> str:
    """Return a display-safe string representation of *value*.

    Strings longer than ``_MAX_RAW_DISPLAY`` are truncated with an
    ellipsis and a length note.
    """
    text = str(value)
    if len(text) <= _MAX_RAW_DISPLAY:
        return text
    return text[:_MAX_RAW_DISPLAY] + f"... [truncated, total {len(text)} chars]"
