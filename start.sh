#!/usr/bin/env bash
# NIRMAN Data Automation — Mac/Linux Launcher
# Run with:  bash start.sh  (or make it executable: chmod +x start.sh && ./start.sh)

# Change to the directory where this script lives, regardless of where it was called from
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

echo "============================================================"
echo "  NIRMAN Data Automation -- Mac/Linux Launcher"
echo "  Directory: $SCRIPT_DIR"
echo "============================================================"
echo ""

# Find python3 or python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "ERROR: Python is not installed or not on your PATH."
    echo "Install Python 3.9+ from https://www.python.org/downloads/"
    read -rp "Press Enter to exit..."
    exit 1
fi

echo "Using Python: $($PYTHON --version)"
echo ""

# Run the universal launcher
"$PYTHON" start.py
