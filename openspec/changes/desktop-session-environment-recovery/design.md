# Design: Desktop Session Environment Recovery

## Architecture Model

The Change adds one concrete `desktop_session` module owned by the runtime
platform boundary. It resolves only the environment required by the current
set of built-in desktop tools. It is not a plugin, registry, service, or generic
session abstraction.

`VerifiedToolExecutor` remains the sole authorization and commit owner. After a
prepared desktop call has passed schema, safety, and confirmation checks, the
executor asks the resolver to ensure a usable current-user graphical
environment. Only then may it call `ToolRegistry._commit`.

The desktop tool set is explicit and closed for this requirement:
`mouse`, `press_key`, `type_text`, `hotkey`, `ui`, `screen`, `screenshot`, and
`windows`. Non-desktop tools never enter the resolver.

## Data Flow

```text
model tool call
  -> Verifier schema/safety/confirmation
  -> PreparedToolCall
  -> VerifiedToolExecutor.commit
       -> desktop tool? no -> ToolRegistry._commit
       -> desktop tool? yes
            -> existing graphical environment usable? -> keep unchanged
            -> otherwise enumerate current-UID active local loginctl sessions
            -> read selected session leader environment (allowlisted keys only)
            -> if needed, resolve one unambiguous X11 socket + authority candidate
            -> atomically publish complete environment values
            -> ToolRegistry._commit
  -> successful tool result
  -> optional post-action verification
```

If discovery is unavailable, unsafe, or ambiguous, the resolver returns a
desktop-environment error before the registry commit. No GUI action and no
post-action screen verification occurs.

## Boundaries and Interfaces

`src/pc_assistant/desktop_session.py` owns:

- `DESKTOP_TOOL_NAMES`: the exact first-party tool names requiring a graphical
  session.
- `DesktopSessionError`: an actionable environment-resolution failure.
- `ensure_desktop_session(tool_name)`: a concrete idempotent function that is a
  no-op for non-desktop tools and supported non-Linux environments, preserves a
  usable existing environment, or discovers and publishes a Linux candidate.

Only an allowlist may cross from session/process inspection into `os.environ`:
`DISPLAY`, `XAUTHORITY`, `WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`,
`XDG_RUNTIME_DIR`, and `DBUS_SESSION_BUS_ADDRESS`.

`src/pc_assistant/harness/executor.py` calls the function after consuming the
authorized capability and before `_registry._commit`. Resolver exceptions are
converted through the existing structured tool-error path.

The module reuses the repository's `loginctl` subprocess pattern from
`security/totp.py`; it does not introduce a daemon protocol or persistence
interface.

## Invariants

1. Safety authorization and required human confirmation always precede desktop
   environment resolution and action execution.
2. An existing usable graphical environment is authoritative and is not
   overwritten.
3. Only active, local, graphical sessions whose UID equals `os.getuid()` are
   eligible.
4. Session/process environment reads copy only allowlisted keys.
5. Candidate values are published together while holding one process-local
   lock; partial discovery never partially mutates `os.environ`.
6. A single X11 socket may be used as fallback; two or more unresolved sockets
   are ambiguous and fail closed.
7. Authority files are accepted only when they are readable current-user
   candidates. Their absence does not justify borrowing another user's file.
8. No resolver path starts, stops, unlocks, or switches sessions.
9. Non-desktop tool execution is byte-for-byte independent of this precondition.

## Failure and Operations

| Condition | Runtime behavior | Tool execution |
|---|---|---|
| Existing X11/Wayland environment is usable | Keep values unchanged | Continue |
| One current-user active local session resolves | Publish allowlisted candidate atomically | Continue |
| Session leader lacks `DISPLAY`, exactly one X socket exists | Derive the display and use a readable current-user authority candidate when present | Continue |
| No graphical session exists | Return actionable desktop-environment error | Do not commit |
| Multiple candidates remain ambiguous | Return ambiguity error naming no selected display | Do not commit |
| `loginctl` or `/proc` is unavailable | Try the bounded unambiguous socket fallback; otherwise return error | Do not commit |
| Backend still rejects the recovered environment | Preserve the backend's typed tool failure | Commit attempted once; no shell retry |

Recovery is evaluated at desktop-tool commit, so a daemon may start before login
and recover on the first later action. Repeated calls are cheap because a usable
published environment short-circuits discovery. The first recovery is protected
by a process-local lock.

## Production Closure

This Change closes the complete runtime path:

- Producer: current-user graphical session state from `loginctl`, the session
  leader, and bounded platform endpoints.
- Boundary: `desktop_session.ensure_desktop_session` publishes the selected
  environment to the service process.
- Consumer: the existing `mss`, `pyautogui`, and `pywinctl`-backed tools through
  the verified executor.
- Failure consumer: the normal structured tool-result observation returned to
  the model and user.

No second execution path, shell compatibility fallback, new persistence, or
model-visible schema is left open.

## Verification

- Unit tests cover valid environment preservation, current-UID session
  selection, leader environment extraction, unambiguous X11 fallback, authority
  selection, other-user rejection, ambiguity, atomic failure, and non-Linux
  no-op behavior.
- Executor tests prove recovery occurs after authorization and before execution,
  recovery failure prevents execution/post-verification, and non-desktop tools
  bypass recovery.
- Existing artifact, GUI, window, and schema tests guard behavior outside the
  new resolver.
- A live service smoke starts/restarts without graphical variables and executes
  screenshot plus mouse observation/movement against the active desktop.

## Alternatives Considered

1. Service-start recovery is simpler but cannot repair a session that appears
   after daemon startup.
2. A systemd-only fix cannot cover every supported service launch path and
   risks hard-coded display state.
3. Per-tool helpers repeat the same security-sensitive discovery in several
   modules.
4. A general desktop-session service or strategy interface has one consumer and
   no approved extension requirement, so it violates YAGNI.
