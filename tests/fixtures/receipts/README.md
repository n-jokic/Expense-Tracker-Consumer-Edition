# OCR receipt fixtures

`manifest.json` is the metadata contract for the real receipt corpus. Image
files are intentionally not checked in yet; benchmark runs must report a
missing image instead of inventing an accuracy result.

Each case records the expected merchant, total, date, and currency plus the
receipt conditions that matter to the OCR pipeline.
