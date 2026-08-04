# gs-harness — workspace rules

Workflow process is defined by **gs-harness**, not duplicated here.

**Authority** (read and follow; do not restate Iron Laws in agent responses):

1. Platform `harness.yaml` (or project root `harness.yaml`) — stages, gates, discipline_profile, fast_track, batch_tdd, evidence
2. `harness-router` skill — invoke first for gs-harness/OpenSpec workflow-managed work
3. Stage skills: `harness-propose`, `harness-implement`, `harness-verify`, `harness-review`, `harness-archive`
4. CLI: `harness snapshot <change> --json`, `harness transition <change>` — never bypass gates via manual `state.json` edits

Discipline is defined in `harness.yaml` → `superpowers` and the `harness-implement` skill. See gs-harness README for install and sync.
