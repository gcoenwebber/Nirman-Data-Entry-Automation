"""
NIRMAN Data Automation — Flask Web Application
================================================
Thin controller layer providing API routes for:
    1. Sample form analysis → schema generation
    2. Schema review / editing
    3. Batch form upload & processing
    4. Excel download
    5. Optional report generation

Run:  python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import json
import os
import sys
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file

# ── Bootstrap the nirman package ─────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from nirman import config
from nirman.audit import get_logger, log_batch_summary
from nirman.models import (
    BatchResult,
    ExtractionRecord,
    FormSchema,
    RecordStatus,
)

logger = get_logger(__name__)

# ── Flask app ────────────────────────────────────────────────
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_SIZE_MB * 1024 * 1024

# ── Shared application state ────────────────────────────────
_lock = threading.Lock()
_state = {
    # Schema state
    "current_schema": None,          # FormSchema or None
    "schema_confirmed": False,       # Has user confirmed the schema?

    # Batch processing state
    "batch": None,                   # BatchResult or None
    "running": False,
    "done": False,
    "error": None,
    "current_file": "",
    "idx": 0,
    "total": 0,
    "log": [],

    # Output paths
    "excel_path": None,
    "report_path": None,
    "report_text": None,
}

RESULTS_JSON = os.path.join(BASE_DIR, "results.json")
UPLOADS = config.UPLOADS_DIR
FORMS = config.FORMS_DIR


def _upd(**kw):
    with _lock:
        _state.update(kw)


def _log(msg):
    print(msg)
    with _lock:
        _state["log"].append(msg)


# ── Persistence helpers ──────────────────────────────────────
def _save_results():
    """Persist batch results to disk."""
    with _lock:
        batch = _state.get("batch")
    if batch is None:
        return
    try:
        data = {}
        for fname, rec in batch.records.items():
            if rec.status == RecordStatus.PROCESSING:
                continue
            data[fname] = rec.model_dump(mode="json")
        with open(RESULTS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Could not save results.json: %s", e)


def _load_results():
    """Load previously saved results from disk."""
    if not os.path.isfile(RESULTS_JSON):
        return {}
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            raw = json.load(f)
        records = {}
        for fname, data in raw.items():
            records[fname] = ExtractionRecord.model_validate(data)
        logger.info("Loaded %d saved result(s) from results.json", len(records))
        return records
    except Exception as e:
        logger.warning("Could not load results.json: %s", e)
        return {}


# ── Background extraction worker ─────────────────────────────
def _worker():
    """Process all unprocessed forms in the uploads directory."""
    from nirman.extractor import extract_form, create_error_record
    from nirman.analytics import compute_analytics
    from nirman.excel_export import export_workbook
    from nirman.file_handler import collect_image_paths

    try:
        with _lock:
            schema = _state["current_schema"]
            batch = _state["batch"]

        if schema is None:
            _log("ERROR: No schema defined. Analyze a sample form first.")
            _upd(running=False, done=True, error="No schema defined")
            return

        if batch is None:
            batch = BatchResult(schema_id=schema.schema_id, schema_name=schema.name)
            _upd(batch=batch)

        # Collect all image files
        paths = []
        for folder in (UPLOADS, FORMS):
            found = collect_image_paths(str(folder))
            paths.extend(found)
        paths = sorted(set(str(p) for p in paths))

        # Skip already-processed
        with _lock:
            already_done = {
                fname for fname, rec in batch.records.items()
                if rec.status not in (RecordStatus.PENDING, RecordStatus.PROCESSING)
            }
        pending = [p for p in paths if os.path.basename(p) not in already_done]

        _upd(running=True, done=False, error=None, idx=0, total=len(pending), log=[])

        if not paths:
            msg = "No image files found in the uploads folder."
            _log(msg)
            _upd(running=False, done=True, error=msg)
            return

        if not pending:
            _log(f"All {len(paths)} form(s) already extracted — nothing to do.")
            _upd(running=False, done=True)
            # Still regenerate Excel with existing data
            _generate_excel_output(schema, batch)
            return

        _log(f"Starting extraction — {len(pending)} new form(s) to process "
             f"({len(already_done)} already done).")

        for i, path in enumerate(pending, 1):
            fname = os.path.basename(path)
            _upd(current_file=fname, idx=i)
            _log(f"[{i}/{len(pending)}] {fname}")

            # Mark as processing
            with _lock:
                processing_rec = ExtractionRecord(
                    schema_id=schema.schema_id,
                    source_file=fname,
                    safe_filename=fname,
                    status=RecordStatus.PROCESSING,
                )
                batch.records[fname] = processing_rec

            try:
                rec = extract_form(path, schema)
            except Exception as e:
                _log(f"   ERROR extracting {fname}: {e}")
                rec = create_error_record(path, schema, config.API_ERROR, str(e))

            with _lock:
                batch.records[fname] = rec

            _save_results()
            tag = "Needs review" if rec.review_required else "Clean"
            _log(f"   -> {tag} ({rec.status.value})")

        # Update batch summary
        batch.update_summary()

        # Generate Excel
        _generate_excel_output(schema, batch)

        log_batch_summary(
            batch.total_forms, batch.successful_forms,
            batch.failed_forms, batch.review_required_forms
        )
        _log("Done! Excel file is ready.")
        _upd(running=False, done=True, current_file="")

    except Exception as exc:
        msg = f"ERROR: {exc}"
        _log(msg)
        _upd(running=False, done=True, error=str(exc))


def _generate_excel_output(schema, batch):
    """Generate the Excel workbook from current batch data."""
    from nirman.analytics import compute_analytics
    from nirman.excel_export import export_workbook

    records_list = [
        rec for rec in batch.records.values()
        if rec.status not in (RecordStatus.PENDING, RecordStatus.PROCESSING)
    ]
    if not records_list:
        return

    analytics = compute_analytics(records_list, schema)
    analytics.batch_id = batch.batch_id

    output_path = str(config.OUTPUT_DIR / "nirman_output.xlsx")
    export_workbook(records_list, schema, analytics, output_path)
    _upd(excel_path=output_path)
    _log(f"Excel saved: {output_path}")


# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ── Schema Routes ────────────────────────────────────────────

@app.route("/api/analyze-sample", methods=["POST"])
def analyze_sample():
    """Upload a sample form image and generate a schema."""
    from nirman.sample_analyzer import analyze_sample as do_analyze
    from nirman.schema_manager import save_schema

    f = request.files.get("sample")
    if not f or not f.filename:
        return jsonify({"error": "No sample file provided"}), 400

    # Save the sample temporarily
    ext = Path(f.filename).suffix.lower()
    if ext not in config.SUPPORTED_IMAGE_EXTS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    sample_path = str(config.UPLOADS_DIR / f"_sample{ext}")
    f.save(sample_path)

    try:
        schema = do_analyze(sample_path)
        save_schema(schema)
        _upd(current_schema=schema, schema_confirmed=False)
        return jsonify({
            "ok": True,
            "schema": schema.model_dump(mode="json"),
        })
    except Exception as e:
        logger.exception("Sample analysis failed")
        return jsonify({"error": f"Analysis failed: {e}"}), 500
    finally:
        # Clean up temp sample
        try:
            os.remove(sample_path)
        except OSError:
            pass


@app.route("/api/schema", methods=["GET"])
def get_schema():
    """Return the current schema."""
    with _lock:
        schema = _state["current_schema"]
        confirmed = _state["schema_confirmed"]
    if schema is None:
        return jsonify({"schema": None, "confirmed": False})
    return jsonify({
        "schema": schema.model_dump(mode="json"),
        "confirmed": confirmed,
    })


@app.route("/api/schema", methods=["PUT"])
def update_schema():
    """Update the current schema with user edits."""
    from nirman.sample_analyzer import refine_schema
    from nirman.schema_manager import save_schema

    data = request.json or {}
    with _lock:
        schema = _state["current_schema"]
    if schema is None:
        return jsonify({"error": "No schema to update"}), 400

    try:
        updated = refine_schema(schema, data)
        save_schema(updated)
        _upd(current_schema=updated)
        return jsonify({"ok": True, "schema": updated.model_dump(mode="json")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/schema/confirm", methods=["POST"])
def confirm_schema():
    """Confirm the schema — enables batch processing."""
    with _lock:
        if _state["current_schema"] is None:
            return jsonify({"error": "No schema to confirm"}), 400
        _state["schema_confirmed"] = True
    return jsonify({"ok": True})


@app.route("/api/schemas", methods=["GET"])
def list_schemas():
    """List all saved schemas."""
    from nirman.schema_manager import list_schemas
    return jsonify(list_schemas())


@app.route("/api/schema/load/<schema_id>", methods=["POST"])
def load_schema(schema_id):
    """Load a previously saved schema."""
    from nirman.schema_manager import load_schema as do_load
    schema = do_load(schema_id)
    if schema is None:
        return jsonify({"error": "Schema not found"}), 404
    _upd(current_schema=schema, schema_confirmed=False)
    return jsonify({"ok": True, "schema": schema.model_dump(mode="json")})


# ── Upload Routes ────────────────────────────────────────────

@app.route("/api/upload", methods=["POST"])
def upload():
    """Upload batch form images."""
    from nirman.file_handler import validate_upload, safe_filename

    saved, errors = [], []
    existing = set(os.listdir(str(UPLOADS))) | set(os.listdir(str(FORMS)))

    for f in request.files.getlist("files"):
        if not f.filename:
            continue

        is_valid, err = validate_upload(f.filename, f.content_length or 0)
        if not is_valid:
            errors.append(f"{f.filename}: {err}")
            continue

        safe = safe_filename(f.filename, existing)
        dest = str(UPLOADS / safe)
        f.save(dest)
        existing.add(safe)
        saved.append(safe)

    return jsonify({"saved": saved, "errors": errors})


@app.route("/api/forms")
def list_forms():
    """List all uploaded form images with their processing status."""
    out = []
    for folder in (UPLOADS, FORMS):
        folder_path = Path(folder)
        if not folder_path.exists():
            continue
        for name in sorted(os.listdir(str(folder_path))):
            if Path(name).suffix.lower() not in config.SUPPORTED_IMAGE_EXTS:
                continue
            if name.startswith("_sample"):
                continue

            with _lock:
                batch = _state.get("batch")
                rec = batch.records.get(name) if batch else None

            result = None
            if rec:
                if rec.status == RecordStatus.PROCESSING:
                    result = "__processing__"
                else:
                    result = rec.model_dump(mode="json")

            file_path = folder_path / name
            out.append({
                "name": name,
                "size": file_path.stat().st_size if file_path.exists() else 0,
                "result": result,
                "folder": str(folder_path.name),
            })

    # Deduplicate by name
    seen = set()
    deduped = []
    for item in out:
        if item["name"] not in seen:
            seen.add(item["name"])
            deduped.append(item)

    return jsonify(deduped)


@app.route("/api/form-image/<path:name>")
def form_image(name):
    safe = os.path.basename(name)
    for folder in (UPLOADS, FORMS):
        p = os.path.join(str(folder), safe)
        if os.path.isfile(p):
            return send_file(p)
    return "Not found", 404


@app.route("/api/delete/<path:name>", methods=["DELETE"])
def delete_form(name):
    safe = os.path.basename(name)
    deleted = False
    for folder in (UPLOADS, FORMS):
        p = os.path.join(str(folder), safe)
        if os.path.isfile(p):
            os.remove(p)
            deleted = True

    if deleted:
        with _lock:
            batch = _state.get("batch")
            if batch and safe in batch.records:
                del batch.records[safe]
        _save_results()
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


# ── Processing Routes ────────────────────────────────────────

@app.route("/api/start", methods=["POST"])
def start():
    with _lock:
        if _state["running"]:
            return jsonify({"error": "Extraction already running"}), 400
        if not _state["schema_confirmed"]:
            return jsonify({"error": "Schema not confirmed. Please confirm the schema first."}), 400
    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/state")
def get_state():
    with _lock:
        snap = {
            "running": _state["running"],
            "done": _state["done"],
            "error": _state["error"],
            "current_file": _state["current_file"],
            "idx": _state["idx"],
            "total": _state["total"],
            "log": _state["log"][-30:],
            "schema_confirmed": _state["schema_confirmed"],
            "has_schema": _state["current_schema"] is not None,
            "excel_ready": _state["excel_path"] is not None and os.path.isfile(_state["excel_path"] or ""),
            "report_ready": _state["report_text"] is not None,
        }
        batch = _state.get("batch")
        if batch:
            snap["results"] = {}
            for fname, rec in batch.records.items():
                if rec.status == RecordStatus.PROCESSING:
                    snap["results"][fname] = "__processing__"
                else:
                    snap["results"][fname] = rec.model_dump(mode="json")
    return jsonify(snap)


# ── Edit Routes ──────────────────────────────────────────────

@app.route("/api/save-edit", methods=["POST"])
def save_edit():
    """Save manual edits to a form's extracted data."""
    from nirman.validator import validate_field
    from nirman.models import FieldValue, ExtractionStatus

    data = request.json or {}
    filename = data.get("filename", "")
    edits = data.get("edits", {})

    with _lock:
        batch = _state.get("batch")
        schema = _state.get("current_schema")

    if batch is None or schema is None:
        return jsonify({"error": "No batch or schema available"}), 404

    rec = batch.records.get(filename)
    if rec is None:
        return jsonify({"error": "No result found for this form"}), 404

    # Apply edits
    for field_name, new_value in edits.items():
        field_def = schema.get_field(field_name)
        if field_def is None:
            continue
        fv = validate_field(field_def, new_value)
        rec.fields[field_name] = fv

    # Recalculate review status
    flagged = [name for name, fv in rec.fields.items() if fv.needs_review]
    rec.review_required = len(flagged) > 0
    rec.review_notes = (
        "Flag: " + ", ".join(flagged)
    ) if flagged else ""

    # Regenerate Excel
    _generate_excel_output(schema, batch)
    _save_results()

    return jsonify({"ok": True, "record": rec.model_dump(mode="json")})


# ── Download Routes ──────────────────────────────────────────

@app.route("/api/download-excel")
def download_excel():
    with _lock:
        path = _state.get("excel_path")
    if path and os.path.isfile(path):
        return send_file(path, as_attachment=True,
                         download_name="nirman_output.xlsx")
    return jsonify({"error": "Excel file not found. Run extraction first."}), 404


@app.route("/api/open-excel")
def open_excel():
    with _lock:
        path = _state.get("excel_path")
    if path and os.path.isfile(path):
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return jsonify({"ok": True})
    return jsonify({"error": "Excel file not found"}), 404


# ── Report Routes ────────────────────────────────────────────

@app.route("/api/generate-report", methods=["POST"])
def generate_report():
    """Generate an analytical report from the batch results."""
    from nirman.analytics import compute_analytics
    from nirman.report_generator import generate_report_text, generate_report

    with _lock:
        schema = _state.get("current_schema")
        batch = _state.get("batch")

    if schema is None or batch is None:
        return jsonify({"error": "No schema or batch data available"}), 400

    records_list = [
        rec for rec in batch.records.values()
        if rec.status not in (RecordStatus.PENDING, RecordStatus.PROCESSING)
    ]
    if not records_list:
        return jsonify({"error": "No processed records to analyze"}), 400

    try:
        analytics = compute_analytics(records_list, schema)
        analytics.batch_id = batch.batch_id

        report_text = generate_report_text(analytics, schema, records_list)

        # Also save to file
        report_path = str(config.OUTPUT_DIR / "nirman_report.md")
        generate_report(analytics, schema, records_list, report_path)

        _upd(report_text=report_text, report_path=report_path)

        return jsonify({"ok": True, "report": report_text})
    except Exception as e:
        logger.exception("Report generation failed")
        return jsonify({"error": f"Report generation failed: {e}"}), 500


@app.route("/api/download-report")
def download_report():
    with _lock:
        path = _state.get("report_path")
    if path and os.path.isfile(path):
        return send_file(path, as_attachment=True,
                         download_name="nirman_report.md")
    return jsonify({"error": "Report not found. Generate it first."}), 404


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import webbrowser
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print("NIRMAN Data Automation Platform --> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
