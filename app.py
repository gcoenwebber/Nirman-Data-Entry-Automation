"""
NIRMAN Data Automation - Flask Web UI
Run:  python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import os
import sys
import json
import glob
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
FORMS_DIR    = os.path.join(BASE_DIR, "forms")
OUTPUT_XLSX  = os.path.join(BASE_DIR, "workshop_ratings_output.xlsx")
RESULTS_JSON = os.path.join(BASE_DIR, "results.json")   # Bug 5: persistence file
IMG_EXTS     = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"}

os.makedirs(FORMS_DIR, exist_ok=True)
sys.path.insert(0, BASE_DIR)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

# ── Shared extraction state ──────────────────────────────────
_lock  = threading.Lock()
_state = {
    "running": False,
    "done":    False,
    "error":   None,
    "current_file": "",
    "idx":   0,
    "total": 0,
    "log":     [],
    "results": {},
}

def _upd(**kw):
    with _lock:
        _state.update(kw)

def _log(msg):
    print(msg)
    with _lock:
        _state["log"].append(msg)

# ── Bug 5: session persistence helpers ──────────────────────
def _save_results():
    """Persist current results to disk (called after each form + after edits)."""
    with _lock:
        data = {k: v for k, v in _state["results"].items()
                if v != "__processing__"}   # never persist transient sentinel
    try:
        with open(RESULTS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[WARN] Could not save results.json:", e)

def _load_results():
    """Load previously saved results from disk on startup."""
    if not os.path.isfile(RESULTS_JSON):
        return
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        with _lock:
            _state["results"].update(data)
        print("[INFO] Loaded", len(data), "saved result(s) from results.json")
    except Exception as e:
        print("[WARN] Could not load results.json:", e)

_load_results()   # restore results immediately at import time

# ── Background extraction worker ─────────────────────────────
def _worker():
    from workshop_extractor import extract_ratings, export_to_excel
    try:
        paths = []
        for ext in IMG_EXTS:
            paths.extend(glob.glob(os.path.join(FORMS_DIR, "*" + ext)))
            paths.extend(glob.glob(os.path.join(FORMS_DIR, "**", "*" + ext), recursive=True))
        paths = sorted(set(paths))

        # ── Bug 1 fix: skip forms already extracted; preserve existing results ──
        with _lock:
            already_done = set(_state["results"].keys())
        pending = [p for p in paths if os.path.basename(p) not in already_done]

        _upd(running=True, done=False, error=None, idx=0,
             total=len(pending), log=[])   # do NOT clear results={}

        if not paths:
            msg = "No image files found in the forms folder."
            _log(msg)
            _upd(running=False, done=True, error=msg)
            return

        if not pending:
            _log("All " + str(len(paths)) + " form(s) already extracted -- nothing to do.")
            _upd(running=False, done=True)
            return

        _log("Starting extraction -- " + str(len(pending)) + " new form(s) to process "
             + "(" + str(len(already_done)) + " already done).")

        for i, path in enumerate(pending, 1):
            fname = os.path.basename(path)
            _upd(current_file=fname, idx=i)
            _log("[" + str(i) + "/" + str(len(pending)) + "] " + fname)
            # ── Bug 2 fix: mark as processing so UI shows amber badge ──
            with _lock:
                _state["results"][fname] = "__processing__"
            try:
                rec = extract_ratings(path)
            except Exception as e:
                _log("   ERROR extracting " + fname + ": " + str(e))
                # ── Bug 4 fix: store error record so form appears in Excel ──
                from workshop_extractor import _error_record, API_ERROR
                rec = _error_record(path, API_ERROR)
            with _lock:
                _state["results"][fname] = rec
            _save_results()   # Bug 5: persist after each form
            tag = "Needs review" if rec.get("Review Required") else "Clean"
            _log("   -> " + tag)

        # Collect all results (old + new) for Excel export
        with _lock:
            all_records = list(_state["results"].values())

        if not all_records:
            msg = "All forms failed extraction. Check your API key and image files."
            _log(msg)
            _upd(running=False, done=True, error=msg)
            return

        _log("Exporting to Excel...")
        export_to_excel(all_records, OUTPUT_XLSX)
        _log("Done! Excel file is ready.")
        _upd(running=False, done=True, current_file="")

    except Exception as exc:
        msg = "ERROR: " + str(exc)
        _log(msg)
        _upd(running=False, done=True, error=str(exc))

# ── Routes ────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/upload", methods=["POST"])
def upload():
    saved, errors = [], []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in IMG_EXTS:
            errors.append(f.filename + ": unsupported file type")
            continue
        dest = os.path.join(FORMS_DIR, f.filename)
        f.save(dest)
        saved.append(f.filename)
    return jsonify({"saved": saved, "errors": errors})

@app.route("/api/forms")
def list_forms():
    out = []
    for name in sorted(os.listdir(FORMS_DIR)):
        if Path(name).suffix.lower() not in IMG_EXTS:
            continue
        with _lock:
            result = _state["results"].get(name)
        out.append({
            "name":   name,
            "size":   os.path.getsize(os.path.join(FORMS_DIR, name)),
            "result": result,
        })
    return jsonify(out)

@app.route("/api/form-image/<path:name>")
def form_image(name):
    safe = os.path.basename(name)
    p = os.path.join(FORMS_DIR, safe)
    if os.path.isfile(p):
        return send_file(p)
    return "Not found", 404

@app.route("/api/delete/<path:name>", methods=["DELETE"])
def delete_form(name):
    safe = os.path.basename(name)
    p = os.path.join(FORMS_DIR, safe)
    if os.path.isfile(p):
        os.remove(p)
        with _lock:
            _state["results"].pop(safe, None)
        _save_results()   # Bug 5: persist after delete
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.route("/api/start", methods=["POST"])
def start():
    with _lock:
        if _state["running"]:
            return jsonify({"error": "Extraction already running"}), 400
    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/state")
def get_state():
    with _lock:
        snap = dict(_state)
        snap["log"] = snap["log"][-30:]
    return jsonify(snap)

@app.route("/api/save-edit", methods=["POST"])
def save_edit():
    data     = request.json or {}
    filename = data.get("filename", "")
    edits    = data.get("edits", {})

    with _lock:
        rec = _state["results"].get(filename)
        if rec is None:
            return jsonify({"error": "No result found for this form"}), 404
        rec.update(edits)
        from workshop_extractor import RATING_FIELDS, ILLEGIBLE, OUT_OF_RANGE, FIELD_MISSING, API_ERROR, PARSE_ERROR
        bad = {ILLEGIBLE, OUT_OF_RANGE, FIELD_MISSING, API_ERROR, PARSE_ERROR}
        flagged = [f for f in RATING_FIELDS if rec.get(f) in bad]
        rec["Review Required"] = bool(flagged)
        rec["Review Notes"]    = ("Flag: " + ", ".join(flagged)) if flagged else ""
        all_records = list(_state["results"].values())

    from workshop_extractor import export_to_excel
    export_to_excel(all_records, OUTPUT_XLSX)
    _save_results()   # Bug 5: persist after manual edit
    return jsonify({"ok": True, "record": rec})

@app.route("/api/download-excel")
def download_excel():
    if os.path.isfile(OUTPUT_XLSX):
        return send_file(OUTPUT_XLSX, as_attachment=True,
                         download_name="workshop_ratings_output.xlsx")
    return jsonify({"error": "Excel file not found. Run extraction first."}), 404

@app.route("/api/open-excel")
def open_excel():
    if os.path.isfile(OUTPUT_XLSX):
        if sys.platform == "win32":
            os.startfile(OUTPUT_XLSX)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", OUTPUT_XLSX])
        return jsonify({"ok": True})
    return jsonify({"error": "Excel file not found"}), 404

if __name__ == "__main__":
    import webbrowser
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print("NIRMAN Data Automation UI --> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
