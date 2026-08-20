# OCR evaluation — Phase 5

> Operational artifact for Phase 5 (O1–O13).

## Production stack (per spec)

- Normal receipt path: **RapidOCR 3.9.2 + ONNX Runtime + PP-OCRv6 Small**.
- Cyrillic path: PP-OCRv6 detector + PP-OCRv5 Cyrillic recognizer (dual recognition).
- Hard/document path: **PaddleOCR-VL-1.6** (0.9B) as a second-stage fallback.
- Scanned PDFs: `pdfplumber` (native text) → RapidOCR → PP-StructureV3 / VL-1.6.
- Deps: `rapidocr==3.9.2`, `onnxruntime`, `opencv-python-headless`, `PyMuPDF`;
  heavy stack in `requirements-ocr-advanced.txt`.

## Data structures (O1)

`OCRToken(text, confidence, polygon, line_id)`, `OCRDocument(tokens, width, height,
mean_confidence, engine, model_version, preprocessing)`, `FieldCandidate(value,
confidence, reasons[])`, `ReceiptResult(merchant, total, date, time, currency,
alternatives, document)`.

## Benchmark (O13)

Fixtures: `tests/fixtures/receipts/` with `{merchant, total, currency, date}`
metadata. Categories: clean/angled/rotated/warped/dark/shadow/overexposed/long/
crumpled/screen · Serbian Latin/Cyrillic/English · EUR/RSD · cash-change/
subtotal/discount/VAT.

Release metrics: total exact accuracy, merchant canonical accuracy, date exact
accuracy, currency accuracy, all-critical-fields accuracy, low-confidence
detection accuracy. **Critical:** `wrong + high confidence` rate.

Initial gates: Total ≥95%, Currency ≥98%, Date ≥95%, Merchant ≥90% canonical
on supported-quality photos. When wrong, the system must usually know it is
uncertain.

Status: ☐ planned — not yet implemented.
