# NIRMAN Data Automation Platform

A **dynamic, form-aware batch data extraction and analysis platform** that uses Google Gemini Vision AI to extract structured data from handwritten and printed forms, with schema-driven validation and professional reporting.

> **Current Version: 2.0.0** · [Changelog](#-changelog)

---

## 📁 Project Structure

```
Nirman Data Entry Automation/
│
├── nirman/                        ← Core package (v2.0)
│   ├── __init__.py                ← Package init, version constant
│   ├── config.py                  ← Centralized configuration (env vars, paths, sentinels)
│   ├── models.py                  ← Pydantic data models (schemas, records, analytics)
│   ├── ai_provider.py             ← Gemini API wrapper with retry & rate limiting
│   ├── sample_analyzer.py         ← AI-powered sample form → schema generation
│   ├── schema_manager.py          ← Schema CRUD (save / load / list / delete)
│   ├── extractor.py               ← Two-pass, schema-driven extraction pipeline
│   ├── validator.py               ← Type-aware field validation (6 field types)
│   ├── excel_export.py            ← 4-sheet professional Excel workbook generation
│   ├── analytics.py               ← Descriptive statistics & frequency distributions
│   ├── report_generator.py        ← AI-generated plain-language analytical reports
│   ├── file_handler.py            ← Upload validation, safe filenames, PDF support
│   ├── audit.py                   ← Privacy-aware audit logging
│   └── tests/                     ← 85 automated tests
│       ├── test_validator.py      ← 26 tests — all field types, Devanagari, sentinels
│       ├── test_models.py         ← 14 tests — schemas, records, serialization
│       ├── test_analytics.py      ← 11 tests — stats, distributions, edge cases
│       ├── test_excel_export.py   ←  4 tests — workbook generation, sentinel colouring
│       ├── test_schema.py         ←  8 tests — CRUD, refinement, ID preservation
│       ├── test_report_generator.py ← 5 tests — prompt content, anti-fabrication
│       └── test_file_handler.py   ←  8 tests — upload validation, path traversal
│
├── app.py                         ← Flask web application (18 API routes)
├── templates/
│   └── index.html                 ← 5-step wizard UI
│
├── schemas/                       ← Saved form schemas (JSON)
├── uploads/                       ← Uploaded form images (batch)
├── output/                        ← Generated Excel workbooks & reports
├── forms/                         ← Form images (legacy-compatible directory)
├── _legacy/                       ← Archived v1.x source files
│
├── requirements.txt               ← Python dependencies
├── start.py                       ← Universal launcher (auto-installs deps)
├── Start NIRMAN.bat               ← Windows double-click launcher
├── start.sh                       ← Mac / Linux launcher
└── .env                           ← API key (not committed to git)
```

---

## ⚙️ Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get a free Gemini API Key
- Visit: https://aistudio.google.com/apikey
- Create a free API key (no credit card required)

### 3. Configure the API key
Create a `.env` file in the project root:
```
GEMINI_API_KEY=your_api_key_here
```

### 4. Run the application
```bash
python start.py
```
Or double-click **Start NIRMAN.bat** on Windows, or `bash start.sh` on Mac/Linux.

The browser opens automatically at **http://127.0.0.1:5000**

---

## 🚀 User Workflow (v2.0)

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  1. Analyze  │───▶│  2. Review   │───▶│  3. Upload   │───▶│ 4. Process   │───▶│  5. Report   │
│    Sample    │    │    Schema    │    │    Batch     │    │  & Review    │    │  (Optional)  │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
 Upload 1 sample     Edit fields,       Drag & drop all     AI extracts,        AI writes a
 form image.         types, ranges.     form images.        you review &        plain-language
 AI detects all      Add/remove         Duplicates are      correct flagged     analytical
 fields & types.     fields. Confirm.   handled safely.     values in-browser.  report.
```

### Step 1 — Analyze Sample Form
Upload **one sample form image**. The AI identifies all fields, their types (rating, text, categorical, checkbox, date, numeric), and constraints (valid ranges, allowed values, date formats). A form schema is generated automatically.

### Step 2 — Review & Edit Schema
Review the AI-generated schema in a table editor. Rename fields, change types, adjust min/max ranges, toggle required flags, add or remove fields. Click **Confirm Schema** when satisfied.

### Step 3 — Upload Batch
Upload all form images for processing. Drag & drop or browse — supports **JPG, PNG, TIFF, BMP, WEBP**. Duplicate filenames are automatically renamed (`form.jpg` → `form_1.jpg`).

### Step 4 — Process & Review
Click **Start Extraction**. Watch live progress with per-form status badges. After processing, review flagged forms (amber/red badges), manually correct values in the side-by-side editor, and save edits — each save regenerates the Excel workbook.

### Step 5 — Generate Report (Optional)
Generate an AI-powered analytical report explaining findings in plain language, highlighting strengths and weak areas, reporting distributions, and stating limitations. The report uses the **same validated data** as the Excel output and never fabricates statistics.

---

## 📊 Output Excel Workbook

The generated `nirman_output.xlsx` contains **four sheets**:

| Sheet | Contents |
|-------|----------|
| **Data Records** | One row per form, colour-coded extracted values, review flags |
| **Field Definitions** | Complete schema catalogue — field name, label, type, range, group |
| **Data Quality** | Per-field valid / missing / illegible / invalid counts, response rates |
| **Summary Statistics** | Mean, median, std dev, min, max, mode, rating distributions, categorical frequencies |

**Colour coding:**

| Colour | Meaning |
|--------|---------|
| 🟦 Navy header | Column labels |
| ⬜ White | Clean, valid value |
| 🟧 Amber | `ILLEGIBLE` / `FIELD_MISSING` / `OUT_OF_RANGE` / `INVALID_VALUE` |
| 🟥 Red | `API_ERROR` / `PARSE_ERROR` (fatal extraction failure) |
| 🟡 Gold | Row has `Review Required = True` |

---

## ⚠️ Error Sentinels

| Sentinel | Meaning | Review? |
|----------|---------|---------|
| `ILLEGIBLE` | AI could not confidently read the handwriting (< 95 % confidence) | Yes |
| `FIELD_MISSING` | Field was blank / not filled in on the form | Yes (if required) |
| `OUT_OF_RANGE` | AI read a number but it falls outside the schema's valid range | Yes |
| `INVALID_VALUE` | Value doesn't match expected type or categorical constraints | Yes |
| `API_ERROR` | Gemini API call failed (check API key / internet / quota) | Yes |
| `PARSE_ERROR` | AI returned malformed or unparseable JSON (rare) | Yes |

> **Sentinel integrity guarantee:** These values are **never** silently converted to zero, empty string, or any other default. Raw and validated values are stored separately in every extraction record.

---

## 🔧 Supported Field Types

| Type | Validation Rules | Example |
|------|-----------------|---------|
| `rating` | Integer within min–max range; scale label displayed | Q1: scale 1–5 |
| `numeric` | Number within min–max range; float or int | Score: 0–100 |
| `text` | Any string; flagged if empty and required | Participant name |
| `categorical` | Case-insensitive membership in allowed values list | Gender: Male / Female / Other |
| `checkbox` | Normalized to `True`/`False`; accepts yes/no/1/0/checked/unchecked | Attended previous workshop |
| `date` | Parsed against schema format (DD/MM/YYYY, etc.); Devanagari-aware | Training date |
| `other` | Accepted as-is, no constraints | Free-form data |

---

## 🧪 Running Tests

```bash
python -m pytest nirman/tests/ -v
```

**85 tests** covering:
- Field validation for all 6 types + Devanagari numeral conversion
- Sentinel integrity (never converts ILLEGIBLE/FIELD_MISSING to zero)
- Schema creation, serialization, CRUD, and refinement
- Analytics computation — means, distributions, completeness, edge cases
- Excel workbook generation — 4 sheets, sentinel colouring, empty batches
- Report prompt construction — anti-fabrication rules, sample-size citations
- File handling — upload validation, safe filenames, path traversal rejection

---

## 🔒 Security

| Concern | Mitigation |
|---------|-----------|
| API key exposure | Stored in `.env` / environment variables only; never logged or returned in API responses |
| Upload attacks | Extension whitelist, file-size limit, path-traversal rejection, filename sanitization |
| Duplicate filenames | Automatic renaming with numeric suffix (`_1`, `_2`, …) |
| Log privacy | Audit logger truncates long values, never records full form content or API keys |
| Dangerous characters | Filenames stripped of `< > : " \| ? *` and control characters |

---

## 📝 Configuration

All settings via environment variables or `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model name |
| `NIRMAN_MAX_RETRIES` | `4` | Max API retry attempts per call |
| `NIRMAN_BASE_RETRY_DELAY` | `5` | Base delay (seconds) for exponential backoff |
| `NIRMAN_MAX_UPLOAD_MB` | `100` | Max upload file size in MB |
| `NIRMAN_RATE_LIMIT_RPM` | `15` | Global rate limit (requests per minute) |

---

## 💡 Tips for Best Results

- Use **good lighting** when photographing forms — avoid shadows and glare
- Scan at **300 DPI or higher** for best OCR accuracy
- Ensure the **full form** is visible and not cropped
- The AI reads the **entire page for context** (headers, instructions, scale labels) before extracting values
- **Devanagari numerals** (१, २, ३, ४, ५) are automatically converted to Arabic digits
- Edit extracted values **directly in the browser** before generating reports
- Re-use saved schemas for recurring form types — no need to re-analyze

---

## 📋 Changelog

### v2.0.0 — Dynamic Form Platform (October 2026)

**This is a complete architectural redesign.** The single-purpose workshop rating extractor has been restructured into a modular, schema-driven platform that supports arbitrary form types.

#### 🏗️ Architecture

| Area | v1.x | v2.0 |
|------|------|------|
| Structure | 2 files (`workshop_extractor.py`, `app.py`) | 12-module `nirman/` package + thin `app.py` controller |
| Data models | Plain dicts | Pydantic models with type safety & serialization |
| Field schema | Hard-coded `Q1`–`Q30` (all rating 1–5) | Dynamic schema from AI sample analysis — any fields, any types |
| Field types | Numeric ratings only | Rating, numeric, text, categorical, checkbox, date, other |
| Value tracking | Single value per field | Raw value + validated value + status + confidence + review flag |
| Excel output | 1 sheet (data) | 4 sheets (data, field definitions, data quality, summary statistics) |
| Analytics | None | Descriptive statistics, distributions, response rates, completeness |
| Reports | None | AI-generated plain-language analytical reports |
| Tests | None | 85 automated tests across 7 test modules |
| UI | Single-page upload + extract | 5-step wizard (sample → schema → batch → process → report) |

#### ✨ New Features

- **Schema-first workflow** — upload one sample form, AI detects all fields and generates an editable schema; no code changes needed for new form types
- **6 field types** — `rating`, `numeric`, `text`, `categorical`, `checkbox`, `date` with type-specific validation
- **Raw / validated value separation** — every field stores both the raw AI output and the validated result with a status enum
- **Confidence tracking** — per-field AI confidence scores when available
- **4-sheet Excel workbook** — Data Records, Field Definitions, Data Quality, Summary Statistics
- **Descriptive analytics** — mean, median, std dev, min/max, mode, rating distributions, categorical frequencies, response rates, field completeness
- **AI-generated reports** — plain-language analysis using the same validated data as Excel; anti-fabrication guardrails; sample-size citations; causation disclaimers
- **Schema persistence** — save, load, list, delete schemas as JSON files; reuse across sessions
- **Token-bucket rate limiter** — configurable RPM to stay within Gemini API quotas
- **Duplicate filename handling** — `form.jpg` → `form_1.jpg` → `form_2.jpg`
- **Path traversal protection** — rejects `../../` and dangerous characters in uploads
- **Privacy-aware audit logging** — truncates long values, never logs API keys or full form content
- **Devanagari date support** — date fields with Devanagari digits are parsed correctly
- **Checkbox normalization** — accepts `yes`/`no`/`1`/`0`/`true`/`false`/`checked`/`unchecked`/`✓`/`☑`
- **`INVALID_VALUE` sentinel** — new sentinel for type-mismatch and constraint violations (distinct from `ILLEGIBLE` and `OUT_OF_RANGE`)
- **85 automated tests** — validation, models, analytics, Excel export, schema CRUD, report prompts, file handling

#### ✅ Preserved from v1.x

- Two-pass extraction (Pass 1: messy-field detection → Pass 2: value extraction)
- Five original sentinel values: `ILLEGIBLE`, `FIELD_MISSING`, `OUT_OF_RANGE`, `API_ERROR`, `PARSE_ERROR`
- Exponential backoff retry for 503 / UNAVAILABLE errors
- Rate-limit-aware retry for 429 / RESOURCE_EXHAUSTED (parses `retryDelay` from API response)
- Colour-coded Excel cells (navy header, white valid, amber warning, red fatal, gold review)
- Freeze panes, auto-width columns, thin borders in Excel
- Live progress polling with per-form status badges in the UI
- Manual edit → re-export workflow (edit a field, click save, Excel regenerates)
- Session persistence via `results.json`
- `.env` file loading for API key configuration
- Windows `.bat` / Mac-Linux `.sh` / universal `start.py` launchers
- Devanagari numeral conversion (०–९ → 0–9) in extraction prompts

#### ⚠️ Breaking Changes

| What Changed | v1.x | v2.0 | Migration |
|-------------|------|------|-----------|
| Source files | `workshop_extractor.py` + `app.py` | `nirman/` package + new `app.py` | Old files archived to `_legacy/` |
| Field definitions | Hard-coded `Q1`–`Q30` in Python | Dynamic schema from sample analysis | Run Step 1 (analyze sample) before processing |
| `results.json` format | `{ filename: { Q1: 4, ... } }` | `{ filename: ExtractionRecord }` with nested `FieldValue` objects | Delete old `results.json` or let it be overwritten |
| Excel output path | `./workshop_ratings_output.xlsx` | `./output/nirman_output.xlsx` | Output now in `output/` directory |
| API routes | `/api/start`, `/api/state`, … | Same names + new schema/report routes | Frontend fully replaced; API is not backward-compatible |
| Dependencies | 5 packages | 7 packages (added `pydantic`, `pytest`) | Run `pip install -r requirements.txt` |

---

### v1.x — Workshop Rating Extractor (September 2026)

Initial release. Single-purpose extractor for NIRMAN workshop feedback forms with hard-coded Q1–Q30 rating fields (scale 1–5).

- Gemini 2.5 Flash Vision for handwritten form OCR
- Two-pass extraction with messy-field detection
- Colour-coded single-sheet Excel output
- Flask web UI with drag-and-drop upload
- Session persistence and manual edit workflow
- `.env`-based API key configuration
- Devanagari numeral support
- Exponential backoff retry (503) and quota-aware retry (429)

---

## 📜 License

Internal project — NIRMAN Data Automation.
