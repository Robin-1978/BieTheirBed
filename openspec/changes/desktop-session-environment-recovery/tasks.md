# Tasks: desktop-session-environment-recovery

## 0. Regression Contract

- [ ] 0.1 Add failing resolver and verified-boundary tests covering existing environment preservation, late graphical login, current-UID filtering, leader extraction, X11 fallback, authority selection, ambiguity, atomic failure, concurrency, non-Linux behavior, non-desktop bypass, and no execution/post-verification on recovery failure
  - owned_paths: [tests/test_desktop_session.py, tests/test_verified_executor.py]
  - verification: pytest -q tests/test_desktop_session.py tests/test_verified_executor.py
  ← REQ-001 ← REQ-002

## 1. Runtime Recovery

- [ ] 1.1 Implement the concrete current-user desktop-session resolver with allowlisted environment publication, bounded loginctl/session-leader inspection, unambiguous X11 fallback, current-user authority selection, locking, and actionable failures
  - owned_paths: [src/pc_assistant/desktop_session.py, tests/test_desktop_session.py]
  - verification: pytest -q tests/test_desktop_session.py
  ← REQ-001 ← REQ-002

## 2. Verified Commit Integration

- [ ] 2.1 Enforce the desktop-session precondition after authorization and before built-in desktop tool commit, preserve non-desktop execution, and prevent commit/post-verification when recovery fails
  - owned_paths: [src/pc_assistant/harness/executor.py, tests/test_verified_executor.py]
  - verification: pytest -q tests/test_verified_executor.py
  ← REQ-001 ← REQ-002

## 3. Desktop Regression

- [ ] 3.1 Run focused desktop, artifact, window, and schema regressions together with the new recovery contract
  - owned_paths: [src/pc_assistant/desktop_session.py, src/pc_assistant/harness/executor.py, tests/test_desktop_session.py, tests/test_verified_executor.py]
  - verification: pytest -q tests/test_desktop_session.py tests/test_verified_executor.py tests/test_artifacts.py tests/test_layer2_gui.py tests/test_window.py tests/test_schema_drift.py
  ← REQ-001 ← REQ-002
