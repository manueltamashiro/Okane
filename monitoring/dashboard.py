"""Streamlit dashboard for the Okane Trading Bot.

This file is a thin shim that delegates to the dashboard package.
Run with:
    streamlit run monitoring/dashboard.py
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from monitoring.dashboard.app import main

main()
