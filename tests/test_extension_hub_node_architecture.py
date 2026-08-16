from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from knoa_platform.config import AppConfig
from knoa_platform.configuration.models import ConfigDraft, ConfigValidationResult
from knoa_platform.extensions.import_service import ExtensionImportService
from knoa_platform.extensions.package_store import PackageStore
from knoa_platform.fleet import (
    FleetCandidateService,
    fleet_candidate_digest,
    fleet_signature_payload,
    seal_fleet_candidate,
)
from knoa_platform.gateway.identity import GatewayIdentityRepository
from knoa_platform.hub.relay import RelayBroker, RelayFrame
from knoa_platform.hub.repository import HubRepository
from knoa_platform.hub.service import HubService
from knoa_platform.node_identity import NodeIdentityStore
from knoa_platform.secrets import SecretStore


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _public_key(key: Ed25519PrivateKey) -> str:
    return _encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )


def _write_skill(root: Path, skill_id: str = "research") -> Path:
    package = root / skill_id
    package.mkdir(parents=True)
    (package / "skill.yaml").write_text(
        "\n".join(
            (
                f"id: {skill_id}",
                "version: '1'",
                "name: Research",
                "description: Research workflow",
                "instructions: instructions.md",
                "triggers: [research]",
            )
        ),
        encoding="utf-8",
    )
    (package / "instructions.md").write_text("Verify sources.", encoding="utf-8")
    return package


class _DraftPort:
    def __init__(self, document) -> None:
        self.document = document
        self.drafts: dict[str, ConfigDraft] = {}

    async def create_config_draft(self, principal_id: str) -> ConfigDraft:
        draft = ConfigDraft(
            draft_id=f"draft-{len(self.drafts) + 1}",
            base_revision_id="revision-1",
            document=self.document,
            draft_version=1,
            updated_by=principal_id,
            updated_at=1,
        )
        self.drafts[draft.draft_id] = draft
        return draft

    async def replace_config_draft(
        self, principal_id, draft_id, document, *, expected_version
    ) -> ConfigDraft:
        current = self.drafts[draft_id]
        assert expected_version == current.draft_version
        updated = current.model_copy(
            update={
                "document": document,
                "draft_version": expected_version + 1,
                "updated_by": principal_id,
            }
        )
        self.drafts[draft_id] = updated
        return updated


def test_package_store_is_content_addressed_and_rejects_mutation(tmp_path: Path) -> None:
    source = _write_skill(tmp_path / "source")
    store = PackageStore(tmp_path / "packages", clock=lambda: 10)

    first = store.import_directory("skill", source, imported_by="owner")
    second = store.import_directory("skill", source, imported_by="owner")

    assert first.package_id == second.package_id
    assert first.path.name == "research"
    assert first.path.parent.name == first.package_id
    (first.path / "instructions.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        store.get(first.package_id)


@pytest.mark.asyncio
async def test_skill_import_ends_in_draft_without_activation(tmp_path: Path) -> None:
    source = _write_skill(tmp_path / "source")
    document = AppConfig(fallback_enabled=False).managed_config()
    port = _DraftPort(document)
    service = ExtensionImportService(PackageStore(tmp_path / "packages"), port)

    result = await service.import_skill("owner", str(source))

    assert result.inspection.kind == "skill"
    assert result.draft.document.skills["research"].package_id.startswith("skill-")
    assert result.draft.draft_version == 2


def test_node_identity_has_separate_stable_keys(tmp_path: Path) -> None:
    store = NodeIdentityStore(tmp_path / "node-identity.json", clock=lambda: 5)
    first = store.load_or_create()
    second = store.load_or_create()

    assert first.node_id == second.node_id
    assert first.signing_public_key == second.signing_public_key
    assert first.configuration_public_key == second.configuration_public_key
    assert first.signing_public_key != first.configuration_public_key
    assert (tmp_path / "node-identity.json").stat().st_mode & 0o077 == 0


def test_node_identity_concurrent_creation_converges(tmp_path: Path) -> None:
    path = tmp_path / "node-identity.json"
    with ThreadPoolExecutor(max_workers=8) as executor:
        identities = tuple(
            executor.map(lambda _: NodeIdentityStore(path).load_or_create(), range(32))
        )

    assert len({identity.node_id for identity in identities}) == 1
    assert len({identity.signing_public_key for identity in identities}) == 1


@pytest.mark.asyncio
async def test_sealed_fleet_candidate_checks_owner_binding_and_base_revision(
    tmp_path: Path,
) -> None:
    identity = NodeIdentityStore(tmp_path / "node.json").load_or_create()
    devices = GatewayIdentityRepository(tmp_path / "gateway.db")
    owner_key = Ed25519PrivateKey.generate()
    grant = devices.create_pairing_grant("owner")
    device = devices.register_verified_device(
        grant.grant_id,
        grant.secret,
        display_name="Owner App",
        public_key=_public_key(owner_key),
    )
    document = AppConfig(fallback_enabled=False).managed_config()
    port = _DraftPort(document)

    async def current(_principal):
        return SimpleNamespace(config_digest=document.digest), None, ()

    async def validate(_principal, _draft_id, *, preflight=False):
        assert preflight
        return ConfigValidationResult(valid=True)

    async def publish(_principal, draft_id, *, expected_version, summary=""):
        return SimpleNamespace(draft_id=draft_id, version=expected_version, summary=summary)

    port.get_config_current = current  # type: ignore[attr-defined]
    port.validate_config_draft = validate  # type: ignore[attr-defined]
    port.publish_config_draft = publish  # type: ignore[attr-defined]
    rollout_id = "rollout-1"
    expires_at = 2000.0
    candidate_digest = fleet_candidate_digest(document)
    signature_material = fleet_signature_payload(
        hub_id="hub-1",
        rollout_id=rollout_id,
        node_id=identity.node_id,
        device_id=device.device_id,
        expected_base_revision_digest=document.digest,
        candidate_digest=candidate_digest,
        expires_at=expires_at,
    )
    associated = {
        "hub_id": "hub-1",
        "rollout_id": rollout_id,
        "node_id": identity.node_id,
        "expected_base_revision_digest": document.digest,
        "candidate_digest": candidate_digest,
        "expires_at": expires_at,
        "encryption_key_version": 1,
    }
    envelope = seal_fleet_candidate(
        identity.configuration_public_key,
        {
            "device_id": device.device_id,
            "owner_signature": _encode(owner_key.sign(signature_material)),
            "document": document.model_dump(mode="json"),
        },
        associated,
    )
    service = FleetCandidateService(identity, devices, port, clock=lambda: 1000)

    applied = await service.apply("owner", rollout_id, envelope.as_dict())

    assert applied.summary == "Fleet rollout rollout-1"


def test_hub_enrollment_ticket_and_presence_are_separate_trust_steps(
    tmp_path: Path,
) -> None:
    repository = HubRepository(tmp_path / "hub.db", hub_id="hub-1", clock=lambda: 1000)
    service = HubService(
        repository,
        tmp_path / "hub.key",
        owner_token="o" * 43,
        clock=lambda: 1000,
    )
    node = NodeIdentityStore(tmp_path / "node.json", clock=lambda: 10).load_or_create()
    grant = repository.create_enrollment_grant()
    transcript = {
        "audience": "knoa-node-enrollment-v1",
        "hub_id": "hub-1",
        "grant_id": grant.grant_id,
        "challenge": grant.challenge,
        "node_id": node.node_id,
        "signing_public_key": node.signing_public_key,
        "signing_key_version": 1,
        "configuration_public_key": node.configuration_public_key,
        "configuration_key_version": 1,
    }
    enrolled = service.enroll_node(
        {
            **transcript,
            "grant_secret": grant.secret,
            "display_name": "Desktop",
            "platform": "linux",
            "version": "1",
            "signature": node.sign(json.dumps(transcript, sort_keys=True, separators=(",", ":")).encode()),
        }
    )
    app_key = Ed25519PrivateKey.generate()
    repository.register_installation("subject_owner", "app-1", _public_key(app_key), "Phone")
    ticket = service.issue_ticket("app-1", node.node_id, "relay")

    claims = service.verify_and_consume_ticket(ticket)

    assert enrolled["node_id"] == node.node_id
    assert claims["node_id"] == node.node_id
    with pytest.raises(PermissionError):
        service.verify_and_consume_ticket(ticket)


def test_secret_store_never_exposes_value_in_status(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets", clock=lambda: 7)
    status = store.put("primary_api_key", "super-secret")

    assert store.get("primary_api_key") == "super-secret"
    assert status["configured"] is True
    assert "super-secret" not in json.dumps(status)
    assert (tmp_path / "secrets" / "primary_api_key").stat().st_mode & 0o077 == 0


def test_relay_frame_counts_decoded_ciphertext_bytes() -> None:
    ciphertext = _encode(b"opaque-payload")
    RelayFrame(
        session_id="session-1",
        stream_id=1,
        frame_type="data",
        sequence=0,
        ciphertext_length=len(b"opaque-payload"),
        ciphertext=ciphertext,
    ).validate_bounds()

    with pytest.raises(ValueError, match="length mismatch"):
        RelayFrame(
            session_id="session-1",
            stream_id=1,
            frame_type="data",
            sequence=0,
            ciphertext_length=len(ciphertext),
            ciphertext=ciphertext,
        ).validate_bounds()


@pytest.mark.asyncio
async def test_relay_broker_rejects_cross_node_session_delivery() -> None:
    class _WebSocket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    broker = RelayBroker()
    websocket = _WebSocket()
    await broker.register_client("session-1", "node-a", websocket)  # type: ignore[arg-type]
    frame = RelayFrame(
        session_id="session-1",
        stream_id=1,
        frame_type="data",
        sequence=0,
        ciphertext_length=1,
        ciphertext=_encode(b"x"),
    )

    with pytest.raises(ValueError, match="another Node"):
        await broker.send_to_client("node-b", frame)
    await broker.send_to_client("node-a", frame)

    assert len(websocket.messages) == 1
