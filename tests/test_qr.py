"""
Tests for QR code generation (utils.qr_png).
"""

from utils import qr_png


def test_qr_png_returns_png_bytes():
    data = qr_png("http://192.168.1.5:8501")
    assert isinstance(data, bytes)
    assert len(data) > 200
    assert data[:4] == b"\x89PNG"  # PNG magic bytes


def test_qr_png_decodes_to_an_image():
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(qr_png("http://localhost:8501")))
    assert img.format == "PNG"
    assert img.width > 100
