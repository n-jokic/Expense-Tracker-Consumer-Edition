"""
ExpenseTracker.exe — desktop launcher for the Expense Tracker app.

Starts the Streamlit server (and the phone-sync API) from the project folder
this executable lives in, then opens your browser at http://localhost:8501.
If the server is already running it just opens the browser.
"""

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

APP_PORT = int(os.environ.get("STREAMLIT_SERVER_PORT", 8501))
APP_URL = f"http://localhost:{APP_PORT}"
API_PORT = 8502


def _saved_project() -> str | None:
    ini = os.path.join(os.path.dirname(sys.executable), "ExpenseTracker.ini")
    try:
        with open(ini, "r", encoding="utf-8") as f:
            path = f.read().strip()
        if path and os.path.isfile(os.path.join(path, "app.py")):
            return path
    except OSError:
        pass
    return None


def _save_project(path: str) -> None:
    ini = os.path.join(os.path.dirname(sys.executable), "ExpenseTracker.ini")
    try:
        with open(ini, "w", encoding="utf-8") as f:
            f.write(path)
    except OSError:
        pass


def _project_dir() -> str:
    """The project folder: remembered location, then wherever app.py sits
    next to (or above) this exe, then the current directory."""
    saved = _saved_project()
    if saved:
        return saved
    candidates = [os.path.dirname(sys.executable)]
    if getattr(sys, "frozen", False):
        candidates.append(os.getcwd())
    for d in candidates:
        if os.path.isfile(os.path.join(d, "app.py")):
            return d
    for d in candidates:
        parent = os.path.dirname(d)
        if os.path.isfile(os.path.join(parent, "app.py")):
            return parent
    return candidates[0]


def _ask_for_project() -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(
            title="Select the Expense Tracker project folder (the one containing app.py)")
        root.destroy()
        return path or None
    except Exception:
        return None


def _python(project_dir: str) -> str:
    venv = os.path.join(project_dir, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv):
        return venv
    return shutil.which("python") or "python"


def _fail(message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Expense Tracker", 0x10)
    except Exception:
        print(message)
    sys.exit(1)


def _server_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health",
                                    timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    project = _project_dir()
    if not os.path.isfile(os.path.join(project, "app.py")):
        chosen = _ask_for_project()
        if chosen and os.path.isfile(os.path.join(chosen, "app.py")):
            project = chosen
            _save_project(chosen)
        else:
            _fail("Could not find app.py.\n\nSelect the Expense Tracker "
                  "project folder (the one containing app.py) in the dialog.")

    python = _python(project)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    if not _server_healthy(APP_PORT):
        subprocess.Popen(
            [python, "-m", "streamlit", "run", "app.py",
             "--server.address", "0.0.0.0", "--server.headless", "true"],
            cwd=project, creationflags=creationflags,
        )
        # Phone-sync API (optional; used by the experimental phone pairing).
        if os.path.isfile(os.path.join(project, "api.py")):
            subprocess.Popen(
                [python, "api.py"], cwd=project, creationflags=creationflags,
            )

        # Wait for the app to come up (max 90 s), then open the browser.
        for _ in range(180):
            if _server_healthy(APP_PORT):
                break
            time.sleep(0.5)
        else:
            _fail("The server did not start within 90 seconds.\n"
                  "Run run_server.bat once to see the error messages.")
    else:
        # Server already running — just open the browser.
        pass

    webbrowser.open(APP_URL)


if __name__ == "__main__":
    main()
