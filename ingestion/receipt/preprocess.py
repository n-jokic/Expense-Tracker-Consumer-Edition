"""
ingestion/receipt/preprocess.py — deterministic receipt preprocessing (O2 + O3 prep).

Stage 1: ImageOps.exif_transpose (fix phone orientation)
Stage 2: validate dimensions/pixel count/file size/aspect ratio
Stage 3: receipt boundary (OpenCV grayscale→blur→Canny→findContours→approxPolyDP→warp) if strong quadrilateral
Stage 4 (fallback): orientation/unwarp via Paddle DocPreprocessor is heavy-path only (not here).

Gracefully degrades when opencv not installed (just exif + validate).
"""

from __future__ import annotations

import io
from typing import Tuple

# Tunables
MAX_DIMENSION = 4000
MAX_PIXELS = 16_000_000  # ~16 MP
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
MIN_DIMENSION = 50
MAX_ASPECT_RATIO = 10.0  # reject absurd strips

PREPROCESSING_VERSION = "preprocess-v1"


def _validate_image(img) -> None:
    w, h = img.size
    if w < MIN_DIMENSION or h < MIN_DIMENSION:
        raise ValueError(f"image too small: {w}x{h}")
    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        raise ValueError(f"image dimension too large: {w}x{h} (max {MAX_DIMENSION})")
    if w * h > MAX_PIXELS:
        raise ValueError(f"pixel count too large: {w*h} (max {MAX_PIXELS})")
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > MAX_ASPECT_RATIO:
        raise ValueError(f"aspect ratio too extreme: {aspect:.1f} (max {MAX_ASPECT_RATIO})")


def _try_boundary_warp(img):
    """Try OpenCV receipt boundary detection; return warped PIL Image or original.

    Only warps when 4 strong corners, receipt occupies meaningful fraction, geometry plausible.
    """
    try:
        import cv2  # type: ignore
        import numpy as np
        from PIL import Image  # noqa: F401
    except Exception:
        return img
    try:
        import numpy as np  # noqa: F811
        # Convert to numpy
        arr = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edged = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return img
        # largest contour by area
        cnt = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(cnt)
        img_area = float(img.size[0] * img.size[1])
        if area < img_area * 0.15:
            return img
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) != 4:
            return img
        # geometry plausible: check convexity
        if not cv2.isContourConvex(approx):
            return img
        # Warp
        pts = approx.reshape(4, 2).astype("float32")
        # order: tl, tr, br, bl by sum/diff heuristic
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1).ravel()
        rect = np.zeros((4, 2), dtype="float32")
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        (tl, tr, br, bl) = rect
        wA = float(np.linalg.norm(br - bl))
        wB = float(np.linalg.norm(tr - tl))
        hA = float(np.linalg.norm(tr - br))
        hB = float(np.linalg.norm(tl - bl))
        maxW = max(int(wA), int(wB))
        maxH = max(int(hA), int(hB))
        if maxW < 100 or maxH < 100:
            return img
        dst = np.array([[0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]], dtype="float32")
        M = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(arr, M, (maxW, maxH))
        from PIL import Image as PILImage
        return PILImage.fromarray(warped)
    except Exception:
        return img


def preprocess_image(image_bytes: bytes, stage: str = "pass1"):
    """Preprocess receipt image; returns PIL Image.

    Raises ValueError on absurd uploads.
    """
    from PIL import Image, ImageOps

    if not image_bytes or len(image_bytes) > MAX_FILE_BYTES:
        if image_bytes and len(image_bytes) > MAX_FILE_BYTES:
            raise ValueError(f"file too large: {len(image_bytes)} bytes (max {MAX_FILE_BYTES})")
        if not image_bytes:
            raise ValueError("empty image")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Fix phone orientation metadata (O2 step 1)
        img = ImageOps.exif_transpose(img)
        # Validate (O2 step 2)
        _validate_image(img)
        if stage == "pass1":
            # Try boundary warp on pass1 only
            img = _try_boundary_warp(img)
        elif stage == "pass2":
            # Pass2: light CLAHE/denoise handled in engine; just return as RGB
            pass
        # Ensure RGB
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        elif img.mode == "RGBA":
            # composite over white
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        return img
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"cannot open image: {e}") from e
