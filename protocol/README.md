# Knoa Protocol Contracts

`protocol/` contains versioned wire contracts shared by Python, Rust and mobile implementations.

Rules:

1. A published field number is never reused.
2. Compatible changes are additive within one package version.
3. Removing a field, changing its meaning or changing framing requires a new protocol package version.
4. Secret-bearing fields must be redacted from traces and golden text fixtures use non-secret placeholders.
5. The checked descriptor digest and text fixtures are updated explicitly with `scripts/check_protocol_contracts.py --update`.
6. Runtime IPC is private to one Node. It does not create an independently deployable Agent Runtime service.

The current framing is defined by the Runtime architecture document: a four-byte big-endian frame length, one flags byte,
then the serialized Protobuf `Envelope` payload.
