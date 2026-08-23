# Browser MCP reference Capability Bundle

This directory is both Knoa's optional Browser capability and a copyable
third-party Capability Bundle example. It installs through the generic
`capability.yaml` transaction; Core contains no Browser, Chromium, DOM, or CDP
branch.

The server launches its own supervised Chrome/Chromium process per session.
The default profile is temporary and is deleted on close, timeout, process
shutdown, or MCP restart. A persistent profile is used only when the caller
passes an explicit safe `profile_name`; it never attaches to the user's normal
Chrome profile. Cookies, local storage, downloaded files, and browser
credentials remain on the Node.

The public tools are semantic: open, navigate, bounded accessibility snapshot,
screenshot, managed download, click, fill, submit, wait, and close. There is no
arbitrary JavaScript, raw CDP, or monolithic “act” tool. Snapshot text and all
page/download content are untrusted evidence and cannot modify Agent policy,
approval, Skills, or the user goal. Private, loopback, link-local, metadata,
credential-bearing, `file:`, and `javascript:` URLs are rejected by default.
For a deliberately local test site, an operator may set an exact comma-separated
origin allowlist in `KNOA_BROWSER_ALLOW_PRIVATE_ORIGINS`; this is local policy,
not a value accepted from a tool call.

`screenshot` and `download` return a generic `managed_file` descriptor with a
relative handle, media type, size, and SHA-256. The Platform validates that
descriptor against its configured managed root before importing an Artifact.
Incomplete, oversized, escaped, or digest-mismatched files are rejected.

Third-party package authors should retain the same structure:

- `capability.yaml` declares compatibility, components, requested permissions,
  allowlisted health checks, setup inputs, and product entry points.
- `mcp.yaml` declares only process startup and local Tool policy. Server MCP
  annotations are useful inspection metadata but never grant permission.
- secrets are named setup inputs/private environment references and must never
  be placed in manifests, logs, tool results, or catalog metadata.
- installation freezes bytes in PackageStore, shows the exact plan, requires a
  matching confirmation digest, publishes one Config Revision, verifies health,
  and restores the prior revision on failure.

Run the independent tests from the repository root with:

```bash
.venv/bin/python -m pytest tests/test_browser_mcp_example.py tests/test_capability_bundle.py -q
```
