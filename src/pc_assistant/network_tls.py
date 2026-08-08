"""Process-wide TLS trust-store normalization."""
from __future__ import annotations

import os
import ssl
from pathlib import Path


_SYSTEM_CA_BUNDLES = (
    Path("/etc/ssl/certs/ca-certificates.crt"),
    Path("/etc/pki/tls/certs/ca-bundle.crt"),
    Path("/etc/ssl/ca-bundle.pem"),
)


def ensure_default_ca_bundle() -> str | None:
    """Repair a missing OpenSSL default CA path without weakening TLS.

    Relocated Python distributions can retain an absolute OpenSSL CA path from
    their original installation prefix.  In that case ``create_default_context``
    silently starts with an empty trust store.  Respect explicit configuration
    and usable interpreter defaults; otherwise point OpenSSL at the host's
    standard CA bundle before any network clients are created.
    """
    configured = os.environ.get("SSL_CERT_FILE", "").strip()
    if configured:
        return configured

    defaults = ssl.get_default_verify_paths()
    if defaults.cafile or defaults.capath:
        return None

    for candidate in _SYSTEM_CA_BUNDLES:
        if candidate.is_file():
            value = str(candidate)
            os.environ["SSL_CERT_FILE"] = value
            return value
    return None
