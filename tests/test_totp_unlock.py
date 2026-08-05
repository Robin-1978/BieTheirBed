from __future__ import annotations

import os
import stat

from pc_assistant.security.totp import TotpUnlockBroker
import pc_assistant.security.totp as totp_module


def test_totp_code_matches_rfc6238_shape(tmp_path):
    broker = TotpUnlockBroker(tmp_path / "secret", ["u"], enabled=True)
    assert broker._code("JBSWY3DPEHPK3PXP", 0) == "282760"


def test_totp_secret_file_is_owner_only(tmp_path):
    path = tmp_path / "secrets" / "unlock.totp"
    secret, uri = TotpUnlockBroker.write_secret(path)
    assert len(secret) == 32
    assert uri.startswith("otpauth://totp/")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_verify_rejects_unknown_and_reaches_session_check(tmp_path, monkeypatch):
    path = tmp_path / "unlock.totp"
    secret = "JBSWY3DPEHPK3PXP"
    path.write_text(secret + "\n")
    os.chmod(path, 0o600)
    broker = TotpUnlockBroker(path, ["allowed"], enabled=True)
    monkeypatch.setattr(broker, "_current_graphical_session", lambda: "")
    monkeypatch.setattr(totp_module.time, "time", lambda: 0.0)
    code = broker._code(secret, 0)
    assert broker.verify_and_unlock("other", code)[0] is False
    assert "active graphical session" in broker.verify_and_unlock("allowed", code)[1]
    assert broker.verify_and_unlock("allowed", code)[0] is False


def test_verify_requires_exactly_six_ascii_digits(tmp_path):
    path = tmp_path / "unlock.totp"
    path.write_text("JBSWY3DPEHPK3PXP\n")
    os.chmod(path, 0o600)
    broker = TotpUnlockBroker(path, ["allowed"], enabled=True)

    for code in ("12345", "1234567", "abc123456", "１２３４５６"):
        ok, message = broker.verify_and_unlock("allowed", code)
        assert ok is False
        assert message == "TOTP must be a 6-digit code"
