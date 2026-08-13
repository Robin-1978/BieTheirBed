# Proposal: Desktop Session Environment Recovery

## Motivation

The long-lived service can start without `DISPLAY`, `XAUTHORITY`, or the other
graphical-session variables required by Linux desktop libraries. When that
happens, the model still selects and authorizes the correct built-in tool, but
`mss` cannot capture the desktop and `pyautogui` raises `KeyError('DISPLAY')`
before a mouse or keyboard action reaches the active X11 session.

The failure is persistent for the lifetime of the daemon even when the user
later logs into a graphical session. Users then see screenshot, mouse, keyboard,
semantic UI, and window tools fail while equivalent shell commands work only
when manually prefixed with `DISPLAY=:N`.

## Investigation

- `src/knoa_platform/service/server.py:628-685` daemonizes by forking and retains
  only the environment supplied by its launcher; it does not discover a later
  graphical login.
- `contrib/knoa.service:4-16` starts from the user manager's
  `default.target` and sets only `PYTHONUNBUFFERED`, so graphical variables are
  not part of the unit contract.
- `src/knoa_platform/tools/screenshot.py:22-50` initializes `mss` directly from
  process environment.
- `src/knoa_platform/tools/mouse.py:115-154` imports and calls `pyautogui`
  directly; a missing `DISPLAY` is not an `ImportError` and escapes as a backend
  exception.
- `src/knoa_platform/harness/executor.py:77-94` is the single verified commit
  boundary for every model-proposed built-in tool.
- `src/knoa_platform/security/totp.py:126-149` already establishes the project
  pattern for enumerating the current user's local graphical sessions through
  `loginctl`.
- Current runtime diagnostics found one active local X11 session for the current
  UID, `/tmp/.X11-unix/X1`, and a current-user GDM authority file, while the
  service tool environment reported an empty `DISPLAY`.

## Scope

### In scope

- Add one Linux desktop-session environment resolver.
- Preserve an already usable graphical environment without rewriting it.
- Recover an absent environment from the current UID's active, local
  `loginctl` graphical session and its leader environment.
- Use an unambiguous current-user X11 socket and authority-file fallback when
  the session leader does not expose `DISPLAY`.
- Invoke recovery immediately before committing built-in screenshot, screen,
  mouse, keyboard, semantic UI, and window tools.
- Return a structured, actionable failure without executing the tool when no
  safe desktop session can be resolved.
- Add focused regression tests and retain the existing desktop/tool suites.

### Out of scope

- Hard-coding `DISPLAY=:1`, a username, UID, runtime directory, or authority
  path.
- Executing `xwd`, `xdotool`, or another shell fallback on behalf of a failed
  first-party tool.
- Starting, unlocking, switching, or otherwise mutating a desktop session.
- Changing model-facing tool names, parameters, confirmation policy, artifact
  delivery, service protocol, or non-Linux behavior.
- Selecting a display belonging to another user or guessing between multiple
  ambiguous displays.

## Compatibility

Wire and internal contracts remain backward compatible. No tool schema or
service frame changes are introduced. Existing valid desktop environments keep
their current values, and non-desktop tools retain their current execution path.

## Observable Outcome

A service that starts with `DISPLAY` and `XAUTHORITY` unset can later execute
the normal built-in screenshot and desktop-input tool paths against the current
user's active graphical session without a manual shell fallback or daemon
restart.

## Quality Floor

- Authorization and confirmation still occur before environment recovery and
  tool commit.
- Recovery never uses another UID's session.
- Environment publication is atomic and idempotent.
- Ambiguous or unavailable sessions fail closed without executing or
  post-verifying the desktop action.
- Existing desktop behavior and schema tests remain passing.

## Estimate

Four owned files, approximately 180 lines including tests. This is one coherent
Path A fix below the configured eight-file and 600-line granularity warning.

## Alternatives Considered

1. Resolve the environment only at service startup. Rejected because the daemon
   may start before the graphical login; the same race would remain.
2. Add `Environment=DISPLAY=:1` or only use `ImportEnvironment` in systemd.
   Rejected because it is launcher-specific and does not cover daemon scripts or
   auto-start; display numbers are not stable machine contracts.
3. Patch each GUI tool independently. Rejected because it duplicates session
   discovery and permits desktop tools to diverge.
4. Ask the model to use shell tools after failure. Rejected because environment
   ownership belongs to the deterministic runtime, not prompt behavior.
