# knoa-system Specification

## ADDED Requirements

### Requirement: Desktop tool commits SHALL recover the current user's active graphical environment
@id: knoa.desktop.session-environment

- [REQ-001] The verified execution boundary SHALL ensure a usable graphical-session environment immediately before committing a built-in screenshot, screen, mouse, keyboard, semantic UI, or window tool, preserving an already usable environment and otherwise resolving only the current OS user's active local graphical session.

#### Scenario: [REQ-001-S01][normal] Existing graphical environment remains authoritative
- **WHEN** a desktop tool is committed while the service already has a usable X11 or Wayland environment
- **THEN** the system SHALL execute the tool without replacing the existing graphical-session values

#### Scenario: [REQ-001-S02][recovery] Graphical login occurs after daemon startup
- **WHEN** the service started without graphical variables and exactly one eligible current-user graphical session is active when a desktop tool commits
- **THEN** the system SHALL recover the session environment and execute the authorized tool through its normal first-party implementation

#### Scenario: [REQ-001-S03][boundary] Non-desktop tool commits
- **WHEN** an authorized tool does not require a graphical session
- **THEN** the execution boundary SHALL commit it without graphical-session discovery or environment mutation

#### Scenario: [REQ-001-S04][error] No eligible graphical session exists
- **WHEN** a desktop tool commits and no active local graphical session or safe unambiguous platform fallback exists for the current user
- **THEN** the system SHALL return an actionable typed environment failure without executing or post-verifying the desktop action

### Requirement: Desktop environment recovery SHALL fail closed and publish atomically
@id: knoa.desktop.session-environment-integrity

- [REQ-002] Linux desktop environment recovery SHALL select only current-UID active local graphical-session data, copy only approved environment keys, and publish a complete candidate atomically; it SHALL NOT select another user's session, partially mutate process environment, or guess between ambiguous displays.

#### Scenario: [REQ-002-S01][security] Another user's graphical session is visible
- **WHEN** session enumeration includes a graphical session whose UID differs from the service process UID
- **THEN** the resolver SHALL ignore that session and SHALL NOT read or publish its environment

#### Scenario: [REQ-002-S02][normal] Session leader exposes graphical values
- **WHEN** one eligible session leader exposes allowlisted graphical environment values with a usable endpoint
- **THEN** the resolver SHALL publish only those allowlisted values as one candidate

#### Scenario: [REQ-002-S03][fallback] Session display metadata is absent
- **WHEN** one eligible X11 session is active, its leader does not expose `DISPLAY`, and exactly one X11 socket is available
- **THEN** the resolver SHALL derive that display and MAY attach a readable current-user authority candidate without hard-coding either path

#### Scenario: [REQ-002-S04][error] Candidate selection is ambiguous or incomplete
- **WHEN** multiple unresolved display candidates exist or discovery fails before a complete safe candidate is available
- **THEN** the resolver SHALL leave the prior process environment unchanged and return an actionable failure

#### Scenario: [REQ-002-S05][concurrency] Two desktop calls trigger first recovery
- **WHEN** concurrent desktop commits observe a missing environment
- **THEN** recovery SHALL publish at most one complete candidate and both calls SHALL observe either that candidate or the same fail-closed outcome
