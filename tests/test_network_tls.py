from __future__ import annotations

from types import SimpleNamespace

from pc_assistant import network_tls


def test_ca_bundle_respects_explicit_configuration(monkeypatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")

    assert network_tls.ensure_default_ca_bundle() == "/custom/ca.pem"


def test_ca_bundle_keeps_usable_interpreter_default(monkeypatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(
        network_tls.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(cafile="/python/ca.pem", capath=None),
    )

    assert network_tls.ensure_default_ca_bundle() is None
    assert "SSL_CERT_FILE" not in network_tls.os.environ


def test_ca_bundle_repairs_missing_relocated_python_default(
    monkeypatch,
    tmp_path,
) -> None:
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_text("test certificate bundle", encoding="utf-8")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setattr(
        network_tls.ssl,
        "get_default_verify_paths",
        lambda: SimpleNamespace(cafile=None, capath=None),
    )
    monkeypatch.setattr(network_tls, "_SYSTEM_CA_BUNDLES", (bundle,))

    assert network_tls.ensure_default_ca_bundle() == str(bundle)
    assert network_tls.os.environ["SSL_CERT_FILE"] == str(bundle)
