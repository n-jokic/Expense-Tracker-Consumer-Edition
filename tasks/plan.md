# Implementation Plan: desktop reliability repairs

## Task list

- [x] Loans: normalize payment record date use and add a regression test.
- [x] Local AI: preserve CPU setting, add diagnostics, and retry GPU failure on CPU.
- [x] Lists: replace encoded sortable labels with a shared validated card board.
- [x] Packaging: add onedir PyInstaller + Inno Setup, per-user state, and migration.
- [ ] Verify on a working Windows Python 3.12 build environment.

## Risk

The checked-in virtual environments target a missing base interpreter, so runtime and installer verification must run where Python 3.12 and Inno Setup 6 are available.
