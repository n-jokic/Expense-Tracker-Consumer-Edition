"""
ExpenseTracker.exe — desktop launcher for the Expense Tracker app.

Starts the Streamlit server (and the phone-sync API) from the project folder
this executable lives in, then opens your browser at http://localhost:8501.
If the server is already running it just opens the browser.
"""

import os
import filecmp
import subprocess
import sys
import time
import urllib.request
import webbrowser

APP_PORT = int(os.environ.get("STREAMLIT_SERVER_PORT", 8501))
APP_URL = f"http://localhost:{APP_PORT}"
API_PORT = 8502


def _saved_project() -> str | None:
    ini = _ini_path()
    try:
        with open(ini, "r", encoding="utf-8", errors="replace") as f:
            path = f.read().strip()
        if path and os.path.isfile(os.path.join(path, "app.py")):
            return path
    except Exception:
        # Corrupt INI / unreadable file must never crash the no-console exe.
        pass
    return None


def _save_project(path: str) -> None:
    ini = _ini_path()
    try:
        os.makedirs(os.path.dirname(ini), exist_ok=True)
        with open(ini, "w", encoding="utf-8") as f:
            f.write(path)
    except OSError:
        pass


def _project_dir() -> str:
    """The project folder: remembered location, then wherever app.py sits
    next to (or above) this exe/script, then the current directory."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    saved = _saved_project()
    if saved:
        return saved
    candidates = [getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)),
                  os.path.dirname(sys.executable)]
    if getattr(sys, "frozen", False):
        candidates.append(os.getcwd())
    # Also probe the script's own directory (covers `python launcher.py`,
    # where sys.executable is the interpreter, not the script folder).
    candidates.append(os.path.dirname(os.path.abspath(__file__)))
    for d in candidates:
        if os.path.isfile(os.path.join(d, "app.py")):
            return d
    for d in candidates:
        parent = os.path.dirname(d)
        if os.path.isfile(os.path.join(parent, "app.py")):
            return parent
    return candidates[0]


def _ini_path() -> str:
    """The launcher's settings file: next to the exe when writable, else in
    the user's roaming profile (e.g. the exe lives in Program Files)."""
    side_by_side = os.path.join(os.path.dirname(sys.executable), "ExpenseTracker.ini")
    if os.path.isdir(os.path.dirname(side_by_side)):
        try:
            probe = os.path.join(os.path.dirname(side_by_side), ".dshtest")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
            return side_by_side
        except OSError:
            pass
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "ExpenseTracker", "ExpenseTracker.ini")


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


def _state_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                        "ExpenseTracker")


def _copy_tree_verified(source: str, destination: str) -> None:
    for root, _, files in os.walk(source):
        relative = os.path.relpath(root, source)
        target = os.path.join(destination, relative)
        os.makedirs(target, exist_ok=True)
        for name in files:
            source_file, target_file = os.path.join(root, name), os.path.join(target, name)
            if not os.path.exists(target_file):
                import shutil
                shutil.copy2(source_file, target_file)
            if not filecmp.cmp(source_file, target_file, shallow=False):
                raise OSError(f"Could not verify migrated file: {name}")


def _prepare_state(project_dir: str) -> str:
    """Migrate a legacy bundled data directory only into an empty user store."""
    state = _state_dir()
    if os.path.isdir(state) and os.listdir(state):
        return state
    os.makedirs(state, exist_ok=True)
    candidates = []
    saved = _saved_project()
    if saved:
        candidates.append(os.path.join(saved, "data"))
    candidates.extend((os.path.join(os.path.dirname(sys.executable), "data"),
                       os.path.join(project_dir, "data")))
    for legacy in candidates:
        if os.path.isdir(legacy):
            _copy_tree_verified(legacy, state)
            break
    return state


def _mode_command(mode: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, os.path.abspath(__file__), mode]


def _run_streamlit() -> None:
    from streamlit.web.cli import main as streamlit_main
    sys.argv = [sys.argv[0], "run", os.path.join(_project_dir(), "app.py"),
                "--server.address", "0.0.0.0", "--server.port", str(APP_PORT),
                "--server.headless", "true"]
    streamlit_main()


def _run_api() -> None:
    import uvicorn
    from api import app
    kwargs = {"host": "0.0.0.0", "port": API_PORT}
    if os.environ.get("EXPENSE_TRACKER_TLS") == "1":
        from make_cert import ensure_cert
        cert, key = ensure_cert()
        kwargs.update(ssl_certfile=cert, ssl_keyfile=key)
    uvicorn.run(app, **kwargs)


def _smoke_check() -> None:
    import streamlit  # noqa: F401
    import llama_cpp  # noqa: F401
    import sqlcipher3  # noqa: F401
    if not os.path.isfile(os.path.join(_project_dir(), "app.py")):
        raise RuntimeError("Packaged app.py is missing")


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


def _api_healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",
                                    timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--streamlit":
        _run_streamlit()
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--api":
        _run_api()
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--smoke":
        _smoke_check()
        return
    project = _project_dir()
    if not os.path.isfile(os.path.join(project, "app.py")):
        chosen = _ask_for_project()
        if chosen and os.path.isfile(os.path.join(chosen, "app.py")):
            project = chosen
            _save_project(chosen)
        else:
            _fail("Could not find app.py.\n\nSelect the Expense Tracker "
                  "project folder (the one containing app.py) in the dialog.")

    state = _prepare_state(project)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    child_env = dict(os.environ, EXPENSE_TRACKER_DATA_DIR=state)

    # Start the two servers INDEPENDENTLY: the API must also come up when the
    # web app is already running (and vice versa).
    if not _server_healthy(APP_PORT):
        subprocess.Popen(
            _mode_command("--streamlit"), cwd=project, env=child_env,
            creationflags=creationflags,
        )
    # Phone-sync API (optional; used by the experimental phone pairing).
    # Only start it when nothing is already answering on port 8502 — a
    # second instance would crash on the taken port (invisible with the
    # no-console window).
    if os.path.isfile(os.path.join(project, "api.py")) and not _api_healthy(API_PORT):
        subprocess.Popen(
            _mode_command("--api"), cwd=project, env=child_env,
            creationflags=creationflags,
        )

    # Wait for the app to come up (max 90 s), then open the browser.
    for _ in range(180):
        if _server_healthy(APP_PORT):
            break
        time.sleep(0.5)
    else:
        _fail("The server did not start within 90 seconds.\n"
              "Run run_server.bat once to see the error messages.")

    webbrowser.open(APP_URL)


if __name__ == "__main__":
    main()
