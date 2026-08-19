"""Resumable download helper for the optional local Gemma model.

Usage:
    python tasks/download_model.py [URL] [DEST_PATH]

Downloads with HTTP Range resume and retries: safe to re-run after an
interrupted download, and it keeps going across flaky connections. The
default downloads the recommended Gemma 3 1B Q4_K_M GGUF into data\\models\\
(the folder the source app auto-detects).

Example:
    python tasks/download_model.py
    python tasks/download_model.py https://example.com/model.gguf C:\\models\\model.gguf
"""

import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_URL = ("https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/"
               "resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf")
DEFAULT_DEST = Path(__file__).resolve().parent.parent / "data" / "models" / \
    "google_gemma-3-1b-it-Q4_K_M.gguf"

CHUNK = 1 << 20  # 1 MiB
MAX_RETRIES = 100
RETRY_DELAY = 2  # seconds


def _expected_total(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return int(resp.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = _expected_total(url)
    print(f"Expected total: {total/1e6:.0f} MB" if total else "Total unknown")
    for attempt in range(1, MAX_RETRIES + 1):
        existing = dest.stat().st_size if dest.exists() else 0
        if total and existing >= total:
            print(f"Complete: {dest} ({existing/1e6:.1f} MB)")
            return
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status == 200 and existing:
                    existing = 0  # server ignored Range: restart from scratch
                done = existing
                with open(dest, "ab" if existing else "wb") as f:
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        f.write(block)
                        done += len(block)
                        if total:
                            pct = done * 100 // total
                            print(f"\r{done/1e6:.1f}/{total/1e6:.0f} MB ({pct}%)",
                                  end="", flush=True)
                        else:
                            print(f"\r{done/1e6:.1f} MB", end="", flush=True)
                print()
                if total and done < total:
                    raise ConnectionError(
                        f"stream ended early at {done/1e6:.1f}/{total/1e6:.0f} MB")
                print(f"Saved {dest} ({done/1e6:.1f} MB)")
                return
        except Exception as e:
            print(f"attempt {attempt}/{MAX_RETRIES}: {type(e).__name__}: {e}")
            time.sleep(RETRY_DELAY)
    raise SystemExit(f"gave up after {MAX_RETRIES} attempts: {url}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DEST
    download(url, dest)
