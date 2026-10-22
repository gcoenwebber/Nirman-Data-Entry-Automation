"""
Centralized configuration — loads from environment variables and .env files.
Never hard-codes API keys, field names, scales, or batch sizes.
"""

import os
from pathlib import Path


def _load_env_file(path: str = ".env") -> None:
    """Load simple KEY=VALUE entries from a local .env file if present."""
    env_path = Path(path)
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_load_env_file(str(_PROJECT_ROOT / ".env"))


# ── Deployment detection ────────────────────────────────────────
IS_VERCEL = bool(os.environ.get("VERCEL"))

# ── Paths ───────────────────────────────────────────────────────
BASE_DIR = _PROJECT_ROOT

if IS_VERCEL:
    # Vercel's filesystem is read-only except /tmp (ephemeral per invocation).
    _TMP = Path("/tmp/nirman")
    SCHEMAS_DIR = _TMP / "schemas"
    UPLOADS_DIR = _TMP / "uploads"
    OUTPUT_DIR = _TMP / "output"
    FORMS_DIR = _TMP / "forms"
    LOG_FILE = _TMP / "extraction_log.txt"
else:
    SCHEMAS_DIR = BASE_DIR / "schemas"
    UPLOADS_DIR = BASE_DIR / "uploads"
    OUTPUT_DIR = BASE_DIR / "output"
    FORMS_DIR = BASE_DIR / "forms"      # Legacy compat — also checked for uploads
    LOG_FILE = BASE_DIR / "extraction_log.txt"

# Ensure directories exist
for d in (SCHEMAS_DIR, UPLOADS_DIR, OUTPUT_DIR, FORMS_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ── API Configuration ──────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()


# ── Processing Limits ──────────────────────────────────────────
MAX_RETRIES = int(os.getenv("NIRMAN_MAX_RETRIES", "4"))
BASE_RETRY_DELAY = int(os.getenv("NIRMAN_BASE_RETRY_DELAY", "5"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("NIRMAN_MAX_UPLOAD_MB", "100"))
RATE_LIMIT_RPM = int(os.getenv("NIRMAN_RATE_LIMIT_RPM", "15"))  # requests per minute


# ── File Types ─────────────────────────────────────────────────
SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}
SUPPORTED_PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = SUPPORTED_IMAGE_EXTS | SUPPORTED_PDF_EXTS


# ── Sentinel Values ───────────────────────────────────────────
# These are used across the system to mark extraction/validation issues.
# Never convert these silently to zero or empty string.
ILLEGIBLE = "ILLEGIBLE"
OUT_OF_RANGE = "OUT_OF_RANGE"
FIELD_MISSING = "FIELD_MISSING"
API_ERROR = "API_ERROR"
PARSE_ERROR = "PARSE_ERROR"
INVALID_VALUE = "INVALID_VALUE"

ALL_SENTINELS = {ILLEGIBLE, OUT_OF_RANGE, FIELD_MISSING, API_ERROR, PARSE_ERROR, INVALID_VALUE}
REVIEW_SENTINELS = {ILLEGIBLE, OUT_OF_RANGE, FIELD_MISSING, INVALID_VALUE}
FATAL_SENTINELS = {API_ERROR, PARSE_ERROR}
