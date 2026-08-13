# Tasks: agent-contracts-factory

## 0. Architecture Contract Tests

- [x] 0.1 Add failing contract tests for direct async iteration of run/TurnInvoker, awaited scalar signatures, bare-handle rejection, unscoped health, event shapes, canonical exports, and rejected aliases
  - owned_paths: [tests/test_agent_contracts.py]
  - verification: pytest -q tests/test_agent_contracts.py
  ← REQ-001 ← REQ-002 ← REQ-005

## 1. Runtime Contracts

- [x] 1.1 Implement the non-colliding runtime package and exact Core-owned contract models without service or Agent aliases
  - owned_paths: [src/knoa_platform/agent_runtime/__init__.py, src/knoa_platform/agent_runtime/contracts.py, tests/test_agent_contracts.py]
  - verification: pytest -q tests/test_agent_contracts.py
  ← REQ-001 ← REQ-002 ← REQ-005

## 2. Factory Characterization

- [x] 2.1 Add failing factory tests for default/injected dependencies, fallback and vision selection, durable RuntimePaths and restart readability, partial failures, complete execution-generation publication, override retention/rejection, unchanged config/registry on failure, vision-schema validation failure, and a paused-turn generation-snapshot interleaving across provider/model/cache/vision/schema/registry/verifier/executor
  - owned_paths: [tests/test_agent_factory.py]
  - verification: pytest -q tests/test_agent_factory.py
  ← REQ-003 ← REQ-004

## 3. Production Composition Cutover

- [x] 3.1 Implement AgentFactory typed `AgentDependencies`/`ExecutionDependencies` construction and retained overrides; stage config, model, vision, registry/schema, verifier/executor, and cache candidates; then route Agent construction, factory-controlled tool changes, turn-ingress generation capture, and single-reference execution publication through the live composition owner
  - owned_paths: [src/knoa_platform/agent_runtime/factory.py, src/knoa_platform/agent.py, tests/test_agent_factory.py]
  - verification: pytest -q tests/test_agent_factory.py
  ← REQ-003 ← REQ-004

## 4. C1 Convergence

- [x] 4.1 Verify the exact contracts and factory-driven production construction together
  - owned_paths: [src/knoa_platform/agent_runtime/__init__.py, src/knoa_platform/agent_runtime/contracts.py, src/knoa_platform/agent_runtime/factory.py, src/knoa_platform/agent.py, tests/test_agent_contracts.py, tests/test_agent_factory.py]
  - verification: pytest -q tests/test_agent_contracts.py tests/test_agent_factory.py
  ← REQ-001 ← REQ-002 ← REQ-003 ← REQ-004 ← REQ-005
