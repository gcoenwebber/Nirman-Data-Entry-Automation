"""
Vercel Serverless Entry Point
==============================
This module adapts the Flask app for Vercel's serverless Python runtime.
Vercel automatically wraps the WSGI `app` object.
"""

import os
import sys

# Ensure the project root is on the path so `nirman` package resolves.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import the Flask app — Vercel looks for an `app` variable.
from app import app

# Vercel's Python runtime uses this as the WSGI handler.
