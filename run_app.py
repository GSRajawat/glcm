"""
run_app.py — Entry point for the packaged .exe. PyInstaller can't freeze
`streamlit run app.py` directly (it's a CLI wrapper), so this launches the
Streamlit server programmatically and opens the browser to it, exactly as
`streamlit run` would.

Build with (see README.md for full steps):
    pyinstaller --onefile --add-data "app.py;." --add-data "auth.py;." ... run_app.py
"""

import os
import sys
import threading
import webbrowser

from streamlit.web import cli as stcli


def _resource_path(filename: str) -> str:
    """Works both running from source and from a PyInstaller bundle."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, filename)


def _open_browser_delayed():
    import time
    time.sleep(2)
    webbrowser.open("http://localhost:8501")


def main():
    threading.Thread(target=_open_browser_delayed, daemon=True).start()

    sys.argv = [
        "streamlit", "run", _resource_path("app.py"),
        "--server.headless=false",
        "--global.developmentMode=false",
    ]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
