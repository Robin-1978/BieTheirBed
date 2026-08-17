"""Account, Node enrollment, ticket and opaque Fleet control contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from knoa_platform.hub.repository import HubRepository


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class HubService:
    def __init__(
        self,
        repository: HubRepository,
        identity_path: str | Path,
        *,
        owner_token: str = "",
        owner_subject_id: str = "subject_owner",
        owner_authenticator: Callable[[str], str] | None = None,
        member_authenticator: Callable[[str], str] | None = None,
        hub_id: str = "",
        clock=time.time,
    ) -> None:
        if owner_authenticator is None and len(owner_token) < 32:
            raise ValueError("Hub owner token must contain at least 32 characters")
        self.repository = repository
        self.hub_id = hub_id or repository.hub_id
        self.owner_subject_id = owner_subject_id
        self._owner_token_hash = (
            hashlib.sha256(owner_token.encode()).digest()
            if owner_authenticator is None
            else b""
        )
        self._owner_authenticator = owner_authenticator
        self._member_authenticator = member_authenticator or owner_authenticator
        self._clock = clock
        self._signing_key = self._load_or_create_key(Path(identity_path))
        repository.initialize_owner(
            owner_subject_id,
            "bootstrap-owner",
            identity_issuer_id=self.hub_id,
        )

    @property
    def signing_public_key(self) -> str:
        return _encode(
            self._signing_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )

    @property
    def workspace_id(self) -> str:
        return self.repository.workspace()["workspace_id"]

    def authenticate_owner(self, token: str) -> str:
        if self._owner_authenticator is not None:
            return self._owner_authenticator(token)
        supplied = hashlib.sha256(token.encode()).digest()
        if not secrets.compare_digest(supplied, self._owner_token_hash):
            raise PermissionError("Hub account authentication rejected")
        return self.owner_subject_id

    def authenticate_member(self, token: str) -> str:
        if self._member_authenticator is not None:
            return self._member_authenticator(token)
        return self.authenticate_owner(token)

    def enroll_node(self, request: dict) -> dict:
        grant = self.repository.enrollment(str(request["grant_id"]), str(request["grant_secret"]))
        if not secrets.compare_digest(str(request["challenge"]), str(grant["challenge"])):
            raise PermissionError("Node enrollment challenge rejected")
        transcript = {
            "audience": "knoa-node-enrollment-v1",
            "hub_id": self.hub_id,
            "grant_id": request["grant_id"],
            "challenge": grant["challenge"],
            "node_id": request["node_id"],
            "signing_public_key": request["signing_public_key"],
            "signing_key_version": request["signing_key_version"],
            "configuration_public_key": request["configuration_public_key"],
            "configuration_key_version": request["configuration_key_version"],
        }
        try:
            Ed25519PublicKey.from_public_bytes(_decode(str(request["signing_public_key"]))).verify(
                _decode(str(request["signature"])), _canonical(transcript)
            )
        except (InvalidSignature, ValueError, KeyError) as exc:
            raise PermissionError("Node enrollment signature rejected") from exc
        return self.repository.consume_enrollment(str(request["grant_id"]), request)

    def record_presence(self, request: dict) -> dict:
        node = self.repository.node(str(request["node_id"]))
        timestamp = float(request["timestamp"])
        if abs(self._clock() - timestamp) > 120:
            raise PermissionError("Node presence timestamp rejected")
        transcript = {
            "audience": "knoa-node-presence-v1",
            "hub_id": self.hub_id,
            "node_id": node["node_id"],
            "timestamp": timestamp,
            "nonce": request["nonce"],
        }
        try:
            Ed25519PublicKey.from_public_bytes(_decode(node["signing_public_key"])).verify(
                _decode(str(request["signature"])), _canonical(transcript)
            )
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Node presence signature rejected") from exc
        return self.repository.record_presence(node["node_id"], str(request["nonce"]))

    def issue_ticket(
        self,
        installation_id: str,
        node_id: str,
        transport: str,
        *,
        subject_id: str | None = None,
    ) -> str:
        installation = self.repository.installation(installation_id)
        if subject_id is not None and installation["subject_id"] != subject_id:
            raise PermissionError("App installation does not belong to account")
        self.repository.node(node_id)
        if transport not in {"direct", "relay"}:
            raise ValueError("Connection transport is invalid")
        now = self._clock()
        payload = {
            "aud": "knoa-node-session-v1",
            "hub_id": self.hub_id,
            "node_id": node_id,
            "installation_id": installation_id,
            "installation_key_digest": hashlib.sha256(
                str(installation["public_key"]).encode()
            ).hexdigest(),
            "ticket_id": f"tkt_{secrets.token_urlsafe(18)}",
            "issued_at": now,
            "expires_at": now + 90,
            "transport": transport,
            "protocol_version": 1,
            "max_session_lifetime": 3600,
        }
        encoded = _encode(_canonical(payload))
        signature = _encode(self._signing_key.sign(encoded.encode()))
        self.repository.create_ticket(
            payload["ticket_id"], node_id, installation_id, payload["expires_at"]
        )
        return f"{encoded}.{signature}"

    def verify_and_consume_ticket(self, token: str) -> dict:
        encoded, separator, signature = token.partition(".")
        if not separator:
            raise PermissionError("Connection ticket rejected")
        try:
            self._signing_key.public_key().verify(_decode(signature), encoded.encode())
            payload = json.loads(_decode(encoded))
        except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("Connection ticket rejected") from exc
        if payload.get("aud") != "knoa-node-session-v1" or payload.get("hub_id") != self.hub_id:
            raise PermissionError("Connection ticket rejected")
        self.repository.consume_ticket(
            str(payload["ticket_id"]), str(payload["node_id"]), str(payload["installation_id"])
        )
        return payload

    def verify_node_signed_request(
        self,
        node_id: str,
        transcript: dict,
        signature: str,
    ) -> dict:
        node = self.repository.node(node_id)
        try:
            Ed25519PublicKey.from_public_bytes(
                _decode(str(node["signing_public_key"]))
            ).verify(_decode(signature), _canonical(transcript))
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Node request signature rejected") from exc
        return node

    def publish_deployment_observation(self, request: dict) -> dict:
        transcript = {
            "audience": "knoa-deployment-observation-v1",
            "workspace_id": self.workspace_id,
            "node_id": request["node_id"],
            "deployment_id": request["deployment_id"],
            "applied_digest": request["applied_digest"],
            "health_epoch": request["health_epoch"],
            "health": request["health"],
            "capabilities": request["capabilities"],
            "available_capacity": request["available_capacity"],
            "observed_at": request["observed_at"],
            "expires_at": request["expires_at"],
        }
        if abs(float(request["observed_at"]) - self._clock()) > 120:
            raise PermissionError("Deployment observation timestamp rejected")
        if float(request["expires_at"]) <= self._clock():
            raise PermissionError("Deployment observation expired")
        self.verify_node_signed_request(
            str(request["node_id"]), transcript, str(request["signature"])
        )
        return self.repository.put_deployment_observation(
            str(request["node_id"]), request
        )

    def publish_work_projection(self, request: dict) -> dict:
        observed_at = float(request["observed_at"])
        if abs(observed_at - self._clock()) > 120:
            raise PermissionError("Work projection timestamp rejected")
        transcript = {
            "audience": "knoa-work-projection-v1",
            "workspace_id": self.workspace_id,
            "node_id": request["node_id"],
            "entity_kind": request["entity_kind"],
            "entity_id": request["entity_id"],
            "principal_id": request.get("principal_id", ""),
            "title": request.get("title", ""),
            "state": request["state"],
            "progress": request.get("progress"),
            "summary": request.get("summary", ""),
            "approval_summary": request.get("approval_summary", ""),
            "artifact_refs": request.get("artifact_refs", []),
            "source_generation": request.get("source_generation", 1),
            "source_digest": request["source_digest"],
            "projection_seq": request["projection_seq"],
            "source_created_at": request["source_created_at"],
            "source_updated_at": request["source_updated_at"],
            "payload": request.get("payload", {}),
            "observed_at": observed_at,
        }
        self.verify_node_signed_request(
            str(request["node_id"]), transcript, str(request["signature"])
        )
        return self.repository.put_work_projection(str(request["node_id"]), request)

    def issue_resource_ticket(self, request: dict) -> str:
        timestamp = float(request["timestamp"])
        if abs(self._clock() - timestamp) > 120:
            raise PermissionError("Resource ticket timestamp rejected")
        transcript = {
            "audience": "knoa-resource-ticket-request-v1",
            "workspace_id": self.workspace_id,
            "invocation_id": request["invocation_id"],
            "caller_node_id": request["caller_node_id"],
            "target_deployment_id": request["target_deployment_id"],
            "max_deadline": request["max_deadline"],
            "timestamp": timestamp,
            "nonce": request["nonce"],
        }
        caller = self.verify_node_signed_request(
            str(request["caller_node_id"]), transcript, str(request["signature"])
        )
        try:
            deployment = self.repository.deployment(
                str(request["target_deployment_id"])
            )
            if deployment["kind"] != "model":
                raise PermissionError("Resource ticket requires a Model Deployment")
        except LookupError:
            deployment = self.repository.model_deployment(
                str(request["target_deployment_id"])
            )
        if not deployment["enabled"]:
            raise PermissionError("Remote model deployment disabled")
        grant = self.repository.active_resource_grant(
            str(request["caller_node_id"]), str(request["target_deployment_id"])
        )
        observation = self.repository.deployment_observation(
            str(request["target_deployment_id"])
        )
        if (
            observation["health"] != "healthy"
            or float(observation["expires_at"]) <= self._clock()
            or int(observation["available_capacity"]) <= 0
        ):
            raise PermissionError("Remote model deployment unavailable")
        target = self.repository.node(str(deployment["target_node_id"]))
        requested_deadline = float(request["max_deadline"])
        max_deadline = min(requested_deadline, float(grant["max_request_deadline"]))
        if max_deadline <= 0:
            raise PermissionError("Remote model deadline rejected")
        now = self._clock()
        payload = {
            "aud": "knoa-resource-invocation-v1",
            "protocol_version": 1,
            "hub_id": self.hub_id,
            "workspace_id": self.workspace_id,
            "ticket_id": f"rit_{secrets.token_urlsafe(18)}",
            "invocation_id": request["invocation_id"],
            "caller_node_id": caller["node_id"],
            "caller_signing_public_key": caller["signing_public_key"],
            "target_node_id": target["node_id"],
            "target_signing_public_key": target["signing_public_key"],
            "target_deployment_id": deployment["deployment_id"],
            "target_materialized_digest": observation["applied_digest"],
            "capability": "model_inference",
            "max_deadline": max_deadline,
            "allowed_transports": ["direct", "relay"],
            "issued_at": now,
            "expires_at": now + 90,
            "nonce": request["nonce"],
        }
        encoded = _encode(_canonical(payload))
        signature = _encode(self._signing_key.sign(encoded.encode()))
        self.repository.record_resource_ticket(payload)
        return f"{encoded}.{signature}"

    def verify_resource_ticket(self, token: str) -> dict:
        encoded, separator, signature = token.partition(".")
        if not separator:
            raise PermissionError("Resource ticket rejected")
        try:
            self._signing_key.public_key().verify(_decode(signature), encoded.encode())
            payload = json.loads(_decode(encoded))
        except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
            raise PermissionError("Resource ticket rejected") from exc
        if (
            payload.get("aud") != "knoa-resource-invocation-v1"
            or payload.get("protocol_version") != 1
            or payload.get("hub_id") != self.hub_id
            or payload.get("workspace_id") != self.workspace_id
            or float(payload.get("expires_at", 0)) <= self._clock()
        ):
            raise PermissionError("Resource ticket rejected")
        return payload

    def record_invocation_observation(self, request: dict) -> None:
        timestamp = float(request["observed_at"])
        if abs(self._clock() - timestamp) > 120:
            raise PermissionError("Invocation observation timestamp rejected")
        transcript = {
            "audience": "knoa-invocation-observation-v1",
            "workspace_id": self.workspace_id,
            "node_id": request["node_id"],
            "invocation_id": request["invocation_id"],
            "reported_state": request["reported_state"],
            "execution_epoch": request.get("execution_epoch", ""),
            "report_seq": request["report_seq"],
            "usage_summary": request.get("usage_summary", {}),
            "observed_at": timestamp,
        }
        self.verify_node_signed_request(
            str(request["node_id"]), transcript, str(request["signature"])
        )
        self.repository.record_invocation_observation(
            str(request["node_id"]), request
        )

    @staticmethod
    def _load_or_create_key(path: Path) -> Ed25519PrivateKey:
        path = path.expanduser().resolve()
        def load_existing() -> Ed25519PrivateKey:
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise PermissionError("Hub identity must be a mode 0600 regular file")
            return Ed25519PrivateKey.from_private_bytes(_decode(path.read_text().strip()))

        if path.exists():
            return load_existing()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return load_existing()
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_encode(raw))
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return key


__all__ = ["HubService"]
