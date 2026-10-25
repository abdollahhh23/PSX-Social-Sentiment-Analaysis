"""
Dashboard entry point — thin wrapper around the Streamlit CLI:

    python run_app.py          # equivalent to: streamlit run app.py
"""

from __future__ import annotations

import os
import sys

import streamlit.web.cli as stcli


def main() -> None:
    sys.argv = [
        "streamlit", "run", os.path.join(os.path.dirname(__file__), "app.py"),
        "--server.port", os.getenv("RADAR_UI_PORT", "8501"),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    print("[radar-ui] dashboard on http://localhost:8501 — keep run_worker.py running")
    main()
