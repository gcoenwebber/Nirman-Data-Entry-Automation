"""
=============================================================================
  NIRMAN WORKSHOP FEEDBACK FORM - AUTOMATED RATING EXTRACTOR
  Uses Google Gemini 2.5 Flash (Free Vision AI) to read handwritten forms
  and exports structured data to Excel with error flagging.
=============================================================================
"""

import os
import json
import re
import glob
import time
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image
from google import genai
from google.genai import types


def _load_env_file(path=".env"):
    """Load simple KEY=VALUE entries from a local .env file if present."""
    if not os.path.exists(path):
        return

    with open(path, encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_env_file()

# ─────────────────────────────────────────────
#  CONFIGURATION  -- Edit these values as needed
# ─────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Folder that contains your scanned / photographed form images
IMAGES_FOLDER = "./forms"

# Accepted image extensions
IMAGE_EXTENSIONS = ["*.jpg", "*.jpeg", "*.png", "*.bmp", "*.tiff", "*.webp"]

# Output Excel filename
OUTPUT_EXCEL = "workshop_ratings_output.xlsx"

# Log file for audit trail
LOG_FILE = "extraction_log.txt"

# ─────────────────────────────────────────────
#  RATING FIELDS -- Q1 to Q30 (all rated 1-5)
# ─────────────────────────────────────────────

RATING_FIELDS = {f"Q{i}": {"min": 1, "max": 5, "scale_label": "1-5"} for i in range(1, 31)}

# ─────────────────────────────────────────────
#  SENTINEL VALUES
# ─────────────────────────────────────────────
ILLEGIBLE     = "ILLEGIBLE"
OUT_OF_RANGE  = "OUT_OF_RANGE"
FIELD_MISSING = "FIELD_MISSING"
API_ERROR     = "API_ERROR"
PARSE_ERROR   = "PARSE_ERROR"

# ─────────────────────────────────────────────
#  LOGGING SETUP
#  StreamHandler is intentionally omitted:
#  emoji in log messages crash the Windows cp1252
#  console encoder, killing the extraction thread.
#  All logs go to the UTF-8 file only.
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  GEMINI CLIENT INIT
# ─────────────────────────────────────────────
client = genai.Client(api_key=GEMINI_API_KEY)


# ─────────────────────────────────────────────
#  GEMINI CALL WITH AUTOMATIC RETRY
#  Retries up to 4 times on 503 UNAVAILABLE
#  with exponential backoff (5s, 10s, 20s, 40s)
# ─────────────────────────────────────────────
def _call_gemini(contents, max_retries=4, base_delay=5):
    """
    Call Gemini and automatically retry on:
      - 503 UNAVAILABLE        : exponential backoff (5, 10, 20, 40 s)
      - 429 RESOURCE_EXHAUSTED : waits the retryDelay the API specifies (+1 s buffer)
    Raises the last exception if all retries are exhausted.
    """
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            meta = getattr(response, "usage_metadata", None)
            if meta:
                log.info(
                    "  Tokens -- input: " + str(getattr(meta, "prompt_token_count", "?")) +
                    ", output: " + str(getattr(meta, "candidates_token_count", "?")) +
                    ", total: " + str(getattr(meta, "total_token_count", "?"))
                )
            else:
                log.warning("  Tokens -- usage_metadata not returned by API for this call.")
            return response
        except Exception as exc:
            last_exc = exc
            err = str(exc)
            if "503" in err or "UNAVAILABLE" in err or "high demand" in err.lower():
                wait = base_delay * (2 ** (attempt - 1))   # 5, 10, 20, 40 s
                log.warning(
                    "  Gemini 503 on attempt " + str(attempt) + "/" + str(max_retries) +
                    " -- retrying in " + str(wait) + "s"
                )
                print("  Gemini busy (503) -- waiting " + str(wait) + "s before retry " +
                      str(attempt) + "/" + str(max_retries))
                time.sleep(wait)
            elif "429" in err or "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
                # Parse the exact delay the API tells us to wait, e.g. retryDelay: '27s'
                m = re.search(r"retryDelay['\"\s:]+(\d+)", err)
                wait = int(m.group(1)) + 1 if m else 60   # +1 s buffer; default 60 s
                log.warning(
                    "  Gemini 429 (quota) on attempt " + str(attempt) + "/" + str(max_retries) +
                    " -- retrying in " + str(wait) + "s (API-requested delay)"
                )
                print("  Gemini quota limit (429) -- waiting " + str(wait) +
                      "s before retry " + str(attempt) + "/" + str(max_retries))
                time.sleep(wait)
            else:
                raise   # any other error: fail immediately
    raise last_exc if last_exc is not None else RuntimeError("Gemini call failed: all retries exhausted with no captured exception.")


# =============================================================================
#  PASS 1 -- DETECT MESSY / CORRECTED FIELDS
# =============================================================================

def detect_messy_fields(image_path: str) -> set:
    """
    Ask Gemini to inspect the image and return the set of question numbers
    whose answer boxes contain ANY crossing-out, scribbling, overwriting,
    correction marks, or otherwise ambiguous handwriting.

    Opens its OWN fresh PIL Image so the file pointer is not shared with
    the extraction call that follows.
    """
    scan_prompt = """
You are a quality-control inspector for handwritten data-entry forms.

SCRIPT NOTE
===========
Answers may be written in Devanagari numerals (e.g. १, २, ३, ४, ५)
or standard Arabic numerals (1, 2, 3, 4, 5). Treat both as equivalent digits
when assessing clarity -- focus only on whether the writing is ambiguous,
crossed out, or corrected, regardless of which script is used.

TASK
====
Look at EVERY answer box in this form (Q1 through Q30).
For each question, check whether the handwritten answer shows ANY of:
  - crossing out / strike-through marks
  - scribbles or scratch marks over the original writing
  - overwriting (a digit written on top of another)
  - correction marks (e.g. eraser smudges, arrows, or other edits)
  - multiple digits written in the same box making the intended value unclear

OUTPUT
======
Return ONLY a valid JSON object with a single key "messy_fields" whose value
is a list of question labels (e.g. ["Q5", "Q26"]) that have ANY of the above
issues.  If no fields are messy, return {"messy_fields": []}.
No markdown fences, no extra text -- pure JSON only.
"""
    try:
        img_scan = Image.open(image_path)
        response = _call_gemini([img_scan, scan_prompt])
        raw  = response.text.strip()
        data = _safe_json_parse(raw, image_path)
        if data and isinstance(data.get("messy_fields"), list):
            messy = {f.strip().upper() for f in data["messy_fields"]}
            if messy:
                log.info("  Pre-scan flagged messy fields: " + str(sorted(messy)))
            return messy
    except Exception as exc:
        log.warning("  Pre-scan failed for " + image_path + ": " + str(exc) +
                    " -- skipping messy-field override")
    return set()


# =============================================================================
#  PASS 2 -- EXTRACT VALUES
# =============================================================================

def extract_ratings(image_path: str) -> dict:
    """
    Two-pass extraction for one form image:
      Pass 1 -- detect fields with crossed-out / corrected handwriting.
      Pass 2 -- extract numeric ratings; override any Pass-1 field to ILLEGIBLE.
    Returns a dict where every value is either a clean number or a sentinel string.
    """
    log.info("Processing: " + image_path)

    # -- 1. Verify image is readable ------------------------------------
    try:
        img = Image.open(image_path)
        img.verify()
    except Exception as exc:
        log.error("Cannot open image " + image_path + ": " + str(exc))
        return _error_record(image_path, API_ERROR)

    # -- 2. Pass 1: detect messy fields (fresh PIL image internally) ----
    messy_fields = detect_messy_fields(image_path)

    # -- 2b. Reopen image for Pass 2 (verify() closes the file) ---------
    try:
        img = Image.open(image_path)
    except Exception as exc:
        log.error("Cannot reopen image " + image_path + ": " + str(exc))
        return _error_record(image_path, API_ERROR)

    # -- 3. Build extraction prompt ------------------------------------
    field_descriptions = "\n".join(
        ['   - "' + name + '" (scale ' + meta["scale_label"] +
         ', valid range ' + str(meta["min"]) + '-' + str(meta["max"]) + ')'
         for name, meta in RATING_FIELDS.items()]
    )

    prompt = f"""
You are an expert data-entry assistant specialising in handwritten workshop feedback forms.

TASK
====
Carefully examine the entire form image -- read every part of the page for context
(instructions, headers, scale labels, example rows) before extracting values.

FIELDS TO EXTRACT
=================
{field_descriptions}

RULES
=====
1. Each rating must be a NUMBER within the stated valid range for that field.
2. Only return a number if you are 95% or more confident in the value.
3. If the handwriting is crossed out, overwritten, corrected, scribbled, or
   otherwise ambiguous or unclear in any way, return the exact string
   "ILLEGIBLE" for that field -- do NOT guess.
4. If a field is clearly blank / not filled in, return "FIELD_MISSING".
5. Do NOT invent values. Do NOT hallucinate.
6. Return ONLY valid JSON -- no markdown fences, no extra text.
7. DEVANAGARI SCRIPT: Answers may be written in Devanagari numerals
   (०=0, १=1, २=2, ३=3, ४=4, ५=5, ६=6, ७=7, ८=8, ९=9).
   Convert any Devanagari digit to its Arabic equivalent before returning
   the value in JSON. For example, if you see ३ written in a box, return 3.

OUTPUT FORMAT (strict JSON)
===========================
{{
  "Q1":  <number or "ILLEGIBLE" or "FIELD_MISSING">,
  "Q2":  <number or "ILLEGIBLE" or "FIELD_MISSING">,
  "Q3":  <number or "ILLEGIBLE" or "FIELD_MISSING">,
  ...
  "Q30": <number or "ILLEGIBLE" or "FIELD_MISSING">
}}

Return ALL 30 keys (Q1 through Q30) every time, even if the value is "FIELD_MISSING".
"""

    # -- 4. Pass 2: call Gemini (with automatic retry on 503) --------
    try:
        response = _call_gemini([img, prompt])
        raw_text = response.text.strip()
        log.debug("Raw Gemini response for " + image_path + ":\n" + raw_text)
    except Exception as exc:
        log.error("Gemini API error for " + image_path + ": " + str(exc))
        return _error_record(image_path, API_ERROR)

    # -- 5. Parse JSON -------------------------------------------------
    parsed = _safe_json_parse(raw_text, image_path)
    if parsed is None:
        return _error_record(image_path, PARSE_ERROR)

    # -- 6. Validate ranges & apply messy-field override ---------------
    record = {
        "File Name":       os.path.basename(image_path),
        "Processed At":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Review Required": False,
    }

    for field, meta in RATING_FIELDS.items():
        if field in messy_fields:
            log.warning("  [OVERRIDE] " + field +
                        ": forced to ILLEGIBLE (messy/corrected handwriting in pre-scan)")
            record[field] = ILLEGIBLE
            record["Review Required"] = True
            continue

        raw_val = parsed.get(field, FIELD_MISSING)
        validated_val, needs_review = _validate_field(field, raw_val, meta)
        record[field] = validated_val
        if needs_review:
            record["Review Required"] = True

    # -- 7. Review notes -----------------------------------------------
    flagged = [
        f for f in RATING_FIELDS
        if record.get(f) in (ILLEGIBLE, OUT_OF_RANGE, FIELD_MISSING, API_ERROR, PARSE_ERROR)
    ]
    record["Review Notes"] = ("Flag: " + ", ".join(flagged)) if flagged else ""

    log.info("  [DONE] Review Required: " + str(record["Review Required"]))
    return record


# =============================================================================
#  HELPER FUNCTIONS
# =============================================================================

def _validate_field(field_name: str, raw_val, meta: dict):
    """Validate one extracted field. Returns (final_value, needs_review: bool)."""
    if isinstance(raw_val, str):
        upper = raw_val.strip().upper()
        if upper in ("ILLEGIBLE", "FIELD_MISSING"):
            sentinel = ILLEGIBLE if upper == "ILLEGIBLE" else FIELD_MISSING
            log.warning("  [WARN] " + field_name + ": " + sentinel)
            return sentinel, True
        try:
            raw_val = float(raw_val)
        except ValueError:
            log.warning("  [WARN] " + field_name +
                        ": unrecognised value '" + str(raw_val) + "' -> " + ILLEGIBLE)
            return ILLEGIBLE, True

    if isinstance(raw_val, (int, float)):
        numeric = int(raw_val) if raw_val == int(raw_val) else raw_val
        if meta["min"] <= numeric <= meta["max"]:
            return numeric, False
        else:
            log.warning("  [WARN] " + field_name + ": " + str(numeric) +
                        " is outside valid range " +
                        str(meta["min"]) + "-" + str(meta["max"]) +
                        " -> " + OUT_OF_RANGE)
            return OUT_OF_RANGE, True

    log.warning("  [WARN] " + field_name +
                ": unexpected type " + str(type(raw_val)) + " -> " + ILLEGIBLE)
    return ILLEGIBLE, True


def _safe_json_parse(text: str, image_path: str):
    """Try to parse JSON; strip markdown fences if present."""
    clean = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        log.error("JSON parse failed for " + image_path + ": " + str(exc) +
                  "\nRaw text: " + text[:300])
        return None


def _error_record(image_path: str, sentinel: str) -> dict:
    """Return a fully-populated error record when something goes fatally wrong."""
    record = {
        "File Name":       os.path.basename(image_path),
        "Processed At":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Review Required": True,
        "Review Notes":    "Fatal: " + sentinel + " -- manual entry needed",
    }
    for field in RATING_FIELDS:
        record[field] = sentinel
    return record


def collect_image_paths(folder: str) -> list:
    """Recursively collect all image files from a folder."""
    paths = []
    for ext in IMAGE_EXTENSIONS:
        paths.extend(glob.glob(os.path.join(folder, "**", ext), recursive=True))
    paths.sort()
    return paths


# =============================================================================
#  EXCEL EXPORT  (colour-coded, formatted)
# =============================================================================

def export_to_excel(all_records: list, output_path: str):
    """Export results to a formatted, colour-coded Excel file."""
    if not all_records:
        log.warning("No records to export.")
        return

    fixed_cols  = ["File Name", "Processed At"]
    rating_cols = list(RATING_FIELDS.keys())
    meta_cols   = ["Review Required", "Review Notes"]
    all_cols    = fixed_cols + rating_cols + meta_cols

    df = pd.DataFrame(all_records, columns=all_cols)

    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Workshop Ratings")

        wb = writer.book
        ws = writer.sheets["Workshop Ratings"]

        header_fill  = PatternFill("solid", fgColor="1F3864")
        ok_fill      = PatternFill("solid", fgColor="FFFFFF")
        warn_fill    = PatternFill("solid", fgColor="FFD966")
        error_fill   = PatternFill("solid", fgColor="FF7575")
        review_fill  = PatternFill("solid", fgColor="FFC000")

        header_font  = Font(bold=True, color="FFFFFF", size=11)
        body_font    = Font(size=10)
        center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

        thin_border  = Border(
            left   = Side(style="thin", color="D0D0D0"),
            right  = Side(style="thin", color="D0D0D0"),
            top    = Side(style="thin", color="D0D0D0"),
            bottom = Side(style="thin", color="D0D0D0"),
        )

        SENTINEL_SET = {ILLEGIBLE, FIELD_MISSING, OUT_OF_RANGE}
        FATAL_SET    = {API_ERROR, PARSE_ERROR}

        for cell in ws[1]:
            cell.fill      = header_fill
            cell.font      = header_font
            cell.alignment = center_align
            cell.border    = thin_border

        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            for cell in row:
                cell.font      = body_font
                cell.alignment = center_align
                cell.border    = thin_border

                val = str(cell.value) if cell.value is not None else ""

                if val.upper() in FATAL_SET:
                    cell.fill = error_fill
                elif val.upper() in SENTINEL_SET:
                    cell.fill = warn_fill
                elif ws.cell(row=1, column=cell.column).value == "Review Required" and val == "True":
                    cell.fill = review_fill
                    cell.font = Font(bold=True, size=10, color="7B3F00")
                else:
                    cell.fill = ok_fill

        for col_idx, col_cells in enumerate(ws.columns, start=1):
            max_len = max(
                (len(str(c.value)) if c.value is not None else 0) for c in col_cells
            )
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 40)

        ws.freeze_panes = "A2"

    log.info("=" * 55)
    log.info("  Excel saved: " + os.path.abspath(output_path))
    log.info("  Total forms processed: " + str(len(all_records)))
    flagged = sum(1 for r in all_records if r.get("Review Required"))
    log.info("  Forms needing manual review: " + str(flagged))
    log.info("=" * 55)


# =============================================================================
#  MAIN ENTRY POINT  (for running as standalone script)
# =============================================================================

def main():
    log.info("=" * 55)
    log.info("  NIRMAN Workshop Rating Extractor -- Starting")
    log.info("  Images folder : " + os.path.abspath(IMAGES_FOLDER))
    log.info("  Output Excel  : " + OUTPUT_EXCEL)
    log.info("=" * 55)

    if not os.path.isdir(IMAGES_FOLDER):
        log.error("Images folder not found: " + IMAGES_FOLDER)
        return

    image_paths = collect_image_paths(IMAGES_FOLDER)
    if not image_paths:
        log.warning("No image files found in " + IMAGES_FOLDER + ". Exiting.")
        return

    log.info("Found " + str(len(image_paths)) + " image(s) to process.")

    all_records = []
    for idx, path in enumerate(image_paths, start=1):
        log.info("[" + str(idx) + "/" + str(len(image_paths)) + "] " + os.path.basename(path))
        record = extract_ratings(path)
        all_records.append(record)

    export_to_excel(all_records, OUTPUT_EXCEL)

    log.info("Done. Open the Excel file to review results.")
    log.info("Full audit log saved to: " + os.path.abspath(LOG_FILE))


if __name__ == "__main__":
    main()
