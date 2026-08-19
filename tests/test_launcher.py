from pathlib import Path

import launcher


def test_first_launch_migrates_legacy_state_once(tmp_path, monkeypatch):
    project = tmp_path / "bundle"
    legacy = project / "data"
    (legacy / "backups").mkdir(parents=True)
    (legacy / "certs").mkdir()
    for relative, content in (("expense_tracker.db", b"encrypted-db"),
                              (".secret_key", b"key"),
                              ("backups/old.db", b"backup"),
                              ("certs/cert.pem", b"cert")):
        (legacy / relative).write_bytes(content)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    state = Path(launcher._prepare_state(str(project)))
    assert (state / "expense_tracker.db").read_bytes() == b"encrypted-db"
    assert (state / ".secret_key").read_bytes() == b"key"
    assert (state / "backups/old.db").exists()
    assert (state / "certs/cert.pem").exists()

    (state / ".secret_key").write_bytes(b"new-key")
    launcher._prepare_state(str(project))
    assert (state / ".secret_key").read_bytes() == b"new-key"
