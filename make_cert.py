"""
make_cert.py — Generate the self-signed TLS certificate used for LAN HTTPS.

Run:  python make_cert.py

Creates data/certs/cert.pem and data/certs/key.pem (RSA 2048, SHA-256,
valid for 825 days). Safe to run on every server start: it no-ops when both
files already exist and are non-empty, so the certificate is generated once
and reused until you delete the files.

The certificate is self-signed with CN = this machine's hostname and a
SubjectAlternativeName covering "localhost", the hostname, and every
non-loopback IPv4 of this machine — the browser/phone trust check finds a
matching SAN regardless of which address is used to connect. Because it is
self-signed, the phone shows a one-time certificate warning that must be
accepted.

Standalone on purpose: it imports neither streamlit nor utils, so it can run
before the app's dependencies are fully checked out. The `cryptography`
import is deferred into the generation function and only needed when a new
certificate is actually created (the system Python 3.12.10 ships it, and the
.venv uses system-site-packages).
"""

import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone

CERT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "certs")
CERT_FILE = os.path.join(CERT_DIR, "cert.pem")
KEY_FILE  = os.path.join(CERT_DIR, "key.pem")


def _host_ips():
    """Every non-loopback IPv4 of this machine, best effort."""
    ips = set()
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
    try:
        # Best-effort hint of the primary outbound interface.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    return sorted(ips)


def ensure_cert():
    """Create the self-signed cert/key pair if missing; return their paths.

    No-op when both files already exist and are non-empty, so this can be
    called on every server start.
    """
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE) \
            and os.path.getsize(CERT_FILE) > 0 and os.path.getsize(KEY_FILE) > 0:
        return CERT_FILE, KEY_FILE

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise SystemExit(
            "make_cert.py needs the 'cryptography' package to generate a "
            "certificate.\nInstall it with:  pip install cryptography"
        )

    os.makedirs(CERT_DIR, exist_ok=True)

    hostname = socket.gethostname()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])

    san = [x509.DNSName("localhost"), x509.DNSName(hostname)]
    for ip in _host_ips():
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass  # skip anything that isn't a parseable IP

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)  # self-signed
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    with open(KEY_FILE, "wb") as f:
        f.write(key_pem)
    with open(CERT_FILE, "wb") as f:
        f.write(cert_pem)

    return CERT_FILE, KEY_FILE


if __name__ == "__main__":
    cert_file, key_file = ensure_cert()
    print(f"Certificate: {cert_file}")
    print(f"Private key: {key_file}")
