"""
NIRMAN Data Automation — Universal Launcher
============================================
Run this file with:  python start.py
Or double-click 'Start NIRMAN.bat' (Windows) / run 'start.sh' (Mac/Linux).

This launcher will:
  1. Make sure the working directory is always the folder that holds these files.
  2. Check that Python 3.9+ is being used.
  3. Auto-install any missing packages from requirements.txt.
  4. Start the Flask server and open the browser automatically.
"""

import sys
import os
import subprocess

# ── 1. Always run from the directory that contains this script ────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# ── 2. Python version check ───────────────────────────────────────────────────
if sys.version_info < (3, 9):
    print("=" * 60)
    print("ERROR: Python 3.9 or higher is required.")
    print("Your version:", sys.version)
    print("Download Python at https://www.python.org/downloads/")
    print("=" * 60)
    input("Press Enter to exit...")
    sys.exit(1)

print("=" * 60)
print("  NIRMAN Data Automation Launcher")
print("  Python:", sys.version.split()[0], " | CWD:", HERE)
print("=" * 60)

# ── 3. Auto-install missing packages ─────────────────────────────────────────
REQ_FILE = os.path.join(HERE, "requirements.txt")
if os.path.isfile(REQ_FILE):
    print("\nChecking / installing requirements...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", REQ_FILE, "--quiet"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("WARNING: Some packages may not have installed correctly.")
        print(result.stderr[-800:] if result.stderr else "")
    else:
        print("All requirements satisfied.\n")
else:
    print("WARNING: requirements.txt not found — skipping dependency check.\n")

# ── 4. Launch app.py ──────────────────────────────────────────────────────────
APP = os.path.join(HERE, "app.py")
if not os.path.isfile(APP):
    print("ERROR: app.py not found in", HERE)
    input("Press Enter to exit...")
    sys.exit(1)

print("Starting NIRMAN Data Automation server...")
print("The browser should open automatically at http://127.0.0.1:5000")
print("Press Ctrl+C to stop the server.\n")

try:
    subprocess.run([sys.executable, APP], cwd=HERE)
except KeyboardInterrupt:
    print("\nServer stopped.")
