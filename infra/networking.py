"""infra/networking.py — LAN URL helpers."""

from __future__ import annotations
import os, socket
import streamlit as st

APP_PORT = 8501
TLS_ENABLED = os.environ.get("EXPENSE_TRACKER_TLS") == "1"

def get_server_port() -> int:
    try:
        return int(st.get_option("server.port"))
    except Exception:
        pass
    try:
        return int(os.environ.get("STREAMLIT_SERVER_PORT", APP_PORT))
    except Exception:
        return APP_PORT

@st.cache_data(ttl=60, show_spinner=False)
def get_lan_urls(port: int):
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    hostname = None
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    urls = []
    scheme = "https" if TLS_ENABLED else "http"
    for ip in sorted(ips):
        if ip.startswith(("127.", "169.254.")):
            continue
        urls.append(f"{scheme}://{ip}:{port}")
    return urls, hostname

def qr_png(url: str) -> bytes:
    import io, qrcode
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
