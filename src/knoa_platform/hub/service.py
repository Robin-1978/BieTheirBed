"""Account, Node enrollment, ticket and opaque Fleet control contracts."""

from __future__ import annotations

import base64
import hashlib
import hmac
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
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from knoa_platform.hub.repository import HubRepository
from knoa_platform.private_files import (
    fsync_directory,
    prepare_private_directory,
    restrict_private_file,
    validate_private_file,
)


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
        grant = self.repository.enrollment(
            str(request["grant_id"]), str(request["grant_secret"])
        )
        if not secrets.compare_digest(
            str(request["challenge"]), str(grant["challenge"])
        ):
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
            Ed25519PublicKey.from_public_bytes(
                _decode(str(request["signing_public_key"]))
            ).verify(_decode(str(request["signature"])), _canonical(transcript))
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
            "version": str(request["version"]),
            "direct_gateway_url": str(request.get("direct_gateway_url", "")),
        }
        display_name = str(request.get("display_name", "")).strip()
        if display_name:
            transcript["display_name"] = display_name
        try:
            Ed25519PublicKey.from_public_bytes(
                _decode(node["signing_public_key"])
            ).verify(_decode(str(request["signature"])), _canonical(transcript))
        except (InvalidSignature, ValueError) as exc:
            raise PermissionError("Node presence signature rejected") from exc
        return self.repository.record_presence(
            node["node_id"],
            str(request["nonce"]),
            version=str(request["version"]),
            direct_gateway_url=str(request.get("direct_gateway_url", "")),
            display_name=display_name,
        )

    def issue_ticket(
        self,
        installation_id: str,
        node_id: str,
        transport: str,
        *,
        scope: str,
        subject_id: str | None = None,
    ) -> str:
        installation = self.repository.installation(installation_id)
        if subject_id is not None and installation["subject_id"] != subject_id:
            raise PermissionError("App installation does not belong to account")
        self.repository.node(node_id)
        if transport not in {"direct", "relay"}:
            raise ValueError("Connection transport is invalid")
        if scope not in {"session", "pairing"}:
            raise ValueError("Connection scope is invalid")
        if scope == "pairing" and transport != "relay":
            raise ValueError("Pairing requires Relay transport")
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
            "scope": scope,
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
        if (
            payload.get("aud") != "knoa-node-session-v1"
            or payload.get("hub_id") != self.hub_id
        ):
            raise PermissionError("Connection ticket rejected")
        self.repository.consume_ticket(
            str(payload["ticket_id"]),
            str(payload["node_id"]),
            str(payload["installation_id"]),
        )
        return payload

    def encrypt_push_token(self, token: str) -> tuple[str, str]:
        normalized = token.strip()
        if not 16 <= len(normalized) <= 4096:
            raise ValueError("Push token length is invalid")
        private = self._signing_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key = hashlib.sha256(b"knoa-hub-push-token-v1\x00" + private).digest()
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, normalized.encode(), self.hub_id.encode())
        return _encode(nonce + ciphertext), hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def decrypt_push_token(self, ciphertext: str) -> str:
        raw = _decode(ciphertext)
        if len(raw) < 29:
            raise ValueError("Push token ciphertext is invalid")
        private = self._signing_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key = hashlib.sha256(b"knoa-hub-push-token-v1\x00" + private).digest()
        return AESGCM(key).decrypt(raw[:12], raw[12:], self.hub_id.encode()).decode()

    def _encrypt_webhook_secret(self, secret: str) -> str:
        private = self._signing_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key = hashlib.sha256(b"knoa-hub-webhook-secret-v1\x00" + private).digest()
        nonce = os.urandom(12)
        return _encode(nonce + AESGCM(key).encrypt(nonce, secret.encode(), self.workspace_id.encode()))

    def _decrypt_webhook_secret(self, ciphertext: str) -> str:
        raw = _decode(ciphertext)
        private = self._signing_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        key = hashlib.sha256(b"knoa-hub-webhook-secret-v1\x00" + private).digest()
        return AESGCM(key).decrypt(raw[:12], raw[12:], self.workspace_id.encode()).decode()

    def provision_webhook_route(self, request: dict) -> dict:
        node = self._verify_node_control(request, "knoa-webhook-route-provision-v1")
        secret = secrets.token_urlsafe(32)
        route_id = f"whr_{secrets.token_urlsafe(24)}"
        item = self.repository.put_webhook_route({
            **request,
            "account_id": self.owner_subject_id,
            "node_id": str(node["node_id"]),
            "route_id": route_id,
            "secret_ciphertext": self._encrypt_webhook_secret(secret),
        })
        # Idempotent route creation must never reveal an existing secret again.
        created = item["route_id"] == route_id
        return {
            "route_id": item["route_id"],
            "secret": secret if created else "",
            "secret_version": int(item["secret_version"]),
        }

    def rotate_webhook_secret(self, request: dict) -> dict:
        node = self._verify_node_control(request, "knoa-webhook-secret-rotate-v1")
        route = self.repository.webhook_route(str(request["route_id"]))
        if route["node_id"] != node["node_id"]:
            raise PermissionError("Webhook route owner rejected")
        secret = secrets.token_urlsafe(32)
        route = self.repository.rotate_webhook_secret(
            str(route["route_id"]),
            secret_ciphertext=self._encrypt_webhook_secret(secret),
            overlap_until=self._clock() + 300,
        )
        return {
            "route_id": route["route_id"],
            "secret": secret,
            "secret_version": int(route["secret_version"]),
            "previous_secret_expires_at": route["previous_secret_expires_at"],
        }

    def delete_webhook_route(self, request: dict) -> None:
        node = self._verify_node_control(request, "knoa-webhook-route-delete-v1")
        self.repository.delete_webhook_route(str(node["node_id"]), str(request["route_id"]))

    def accept_webhook(
        self,
        route_id: str,
        *,
        event_id: str,
        timestamp_text: str,
        signature: str,
        body: bytes,
        payload: dict,
    ) -> tuple[dict, bool]:
        route = self.repository.webhook_route(route_id)
        if route["state"] != "active":
            raise LookupError("Webhook route is inactive")
        timestamp = float(timestamp_text)
        if abs(self._clock() - timestamp) > 300:
            raise PermissionError("Webhook timestamp rejected")
        transcript = event_id.encode() + b"\n" + timestamp_text.encode() + b"\n" + body
        candidates = [self._decrypt_webhook_secret(str(route["secret_ciphertext"]))]
        if (
            route["previous_secret_ciphertext"]
            and route["previous_secret_expires_at"] is not None
            and float(route["previous_secret_expires_at"]) >= self._clock()
        ):
            candidates.append(self._decrypt_webhook_secret(str(route["previous_secret_ciphertext"])))
        valid = any(
            hmac.compare_digest(hmac.new(secret.encode(), transcript, hashlib.sha256).hexdigest(), signature)
            for secret in candidates
        )
        if not valid:
            raise PermissionError("Webhook signature rejected")
        return self.repository.enqueue_webhook_event(route_id, event_id, payload)

    def pull_webhook_events(self, request: dict) -> tuple[dict, ...]:
        node = self._verify_node_control(request, "knoa-webhook-event-pull-v1")
        return self.repository.pull_webhook_events(str(node["node_id"]), limit=int(request.get("limit", 50)))

    def acknowledge_webhook_events(self, request: dict) -> int:
        node = self._verify_node_control(request, "knoa-webhook-event-ack-v1")
        ids = tuple(int(value) for value in request.get("ingress_ids", ()))
        return self.repository.acknowledge_webhook_events(str(node["node_id"]), ids)

    def register_push_token(
        self,
        account_id: str,
        installation_id: str,
        *,
        provider: str,
        token: str,
        locale: str,
        app_version: str,
    ) -> dict:
        if provider != "fcm":
            raise ValueError("Push provider is not supported")
        encrypted, fingerprint = self.encrypt_push_token(token)
        return self.repository.put_push_installation(
            account_id,
            installation_id,
            provider=provider,
            token_ciphertext=encrypted,
            token_fingerprint=fingerprint,
            locale=locale[:32],
            app_version=app_version[:64],
        )

    def publish_notification_intent(self, request: dict) -> dict:
        node = self._verify_node_control(
            request,
            "knoa-notification-intent-v1",
        )
        return self.repository.put_notification_intent(
            self.owner_subject_id,
            str(node["node_id"]),
            request,
        )

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

    def _verify_node_control(self, request: dict, audience: str) -> dict:
        timestamp = float(request["timestamp"])
        if abs(self._clock() - timestamp) > 120:
            raise PermissionError("Node control timestamp rejected")
        transcript = {
            key: value
            for key, value in request.items()
            if key != "signature"
        }
        if transcript.get("audience", audience) != audience:
            raise PermissionError("Node control audience rejected")
        transcript["audience"] = audience
        transcript["workspace_id"] = self.workspace_id
        node = self.verify_node_signed_request(
            str(request["node_id"]), transcript, str(request["signature"])
        )
        self.repository.consume_node_nonce(
            str(request["node_id"]), str(request["nonce"])
        )
        return node

    def node_control_state(self, request: dict) -> dict:
        node = self._verify_node_control(request, "knoa-node-control-state-v1")
        node_id = str(node["node_id"])
        own_deployments = tuple(
            item
            for item in self.repository.list_deployments(node_id=node_id)
            if item["target_node_id"] == node_id
        )
        all_grants = self.repository.list_resource_grants()
        incoming_deployment_ids = {
            str(item["target_deployment_id"])
            for item in all_grants
            if str(item["caller_node_id"]) == node_id
            and item["revoked_at"] is None
            and float(item["expires_at"]) > self._clock()
        }
        incoming_deployments = tuple(
            item
            for item in self.repository.list_deployments()
            if str(item["deployment_id"]) in incoming_deployment_ids
            and bool(item["enabled"])
        )
        deployments = own_deployments + tuple(
            item
            for item in incoming_deployments
            if str(item["deployment_id"])
            not in {str(value["deployment_id"]) for value in own_deployments}
        )
        own_deployment_ids = {
            str(item["deployment_id"]) for item in own_deployments
        }
        deployment_ids = {str(item["deployment_id"]) for item in deployments}
        resource_ids = {str(item["resource_id"]) for item in deployments}
        resources = tuple(
            item
            for item in self.repository.list_workspace_resources()
            if str(item["resource_id"]) in resource_ids
        )
        grants = tuple(
            item
            for item in all_grants
            if str(item["target_deployment_id"]) in own_deployment_ids
            or str(item["caller_node_id"]) == node_id
        )
        observations = tuple(
            item
            for item in self.repository.list_deployment_observations()
            if str(item["deployment_id"]) in deployment_ids
        )
        now = self._clock()
        nodes = [
            {
                "node_id": item["node_id"],
                "display_name": item["display_name"],
                "platform": item["platform"],
                "version": item["version"],
                "direct_gateway_url": item.get("direct_gateway_url", ""),
                "last_seen": item.get("last_seen"),
                "online": item.get("last_seen") is not None
                and now - float(item["last_seen"]) <= 90,
            }
            for item in self.repository.list_nodes()
        ]
        return {
            "workspace_id": self.workspace_id,
            "node_id": node_id,
            "nodes": nodes,
            "resources": list(resources),
            "deployments": list(deployments),
            "grants": list(grants),
            "observations": list(observations),
        }

    def publish_node_model_share(self, request: dict) -> dict:
        node = self._verify_node_control(request, "knoa-node-model-share-v1")
        node_id = str(node["node_id"])
        deployment_id = str(request["deployment_id"])
        resource_id = str(request["resource_id"])
        enabled = bool(request["enabled"])
        allowed_node_ids = tuple(str(value) for value in request["allowed_node_ids"])
        if len(set(allowed_node_ids)) != len(allowed_node_ids):
            raise ValueError("Model share allowed Nodes must be unique")
        if node_id in allowed_node_ids:
            raise ValueError("A Node cannot grant its shared model to itself")
        for caller_node_id in allowed_node_ids:
            self.repository.node(caller_node_id)

        try:
            existing_deployment = self.repository.deployment(deployment_id)
        except LookupError:
            existing_deployment = None
        if existing_deployment is not None and str(
            existing_deployment["target_node_id"]
        ) != node_id:
            raise PermissionError("Node cannot replace another Node deployment")

        spec = {
            "provider_protocol": str(request["provider_protocol"]),
            "model_identity": str(request["model_identity"]),
            "declared_capabilities": {
                "streaming": True,
                "tools": True,
                "vision": bool(request["supports_vision"]),
            },
        }
        canonical_digest = hashlib.sha256(
            _canonical({"kind": "model", "spec": spec})
        ).hexdigest()
        try:
            current_resource = self.repository.workspace_resource(resource_id)
        except LookupError:
            current_resource = None
        resource_creator = (
            "" if current_resource is None else str(current_resource["created_by"])
        )
        node_owned_resource = current_resource is None or resource_creator == f"node:{node_id}"
        if current_resource is not None and not node_owned_resource:
            if (
                resource_creator.startswith("node:")
                or existing_deployment is None
                or str(existing_deployment["resource_id"]) != resource_id
                or str(current_resource["kind"]) != "model"
                or current_resource["spec"] != spec
            ):
                raise PermissionError(
                    "Node cannot replace another owner's Workspace Resource"
                )
        resource_generation = 1
        if current_resource is not None:
            resource_generation = int(current_resource["generation"])
            if node_owned_resource and (
                str(current_resource["canonical_digest"]) != canonical_digest
                or current_resource["spec"] != spec
            ):
                resource_generation += 1
        if node_owned_resource:
            resource = self.repository.put_workspace_resource(
                {
                    "resource_id": resource_id,
                    "kind": "model",
                    "generation": resource_generation,
                    "canonical_digest": canonical_digest,
                    "display_name": str(request["display_name"]),
                    "spec": spec,
                    "enabled": enabled,
                },
                created_by=f"node:{node_id}",
            )
        else:
            resource = current_resource
        resource_digest = str(resource["canonical_digest"])

        deployment_spec = {
            "max_remote_concurrency": int(request["max_remote_concurrency"]),
            "materialized_digest": str(request["materialized_digest"]),
        }
        desired_generation = 1
        if existing_deployment is not None:
            desired_generation = int(existing_deployment["desired_generation"])
            if any(
                (
                    str(existing_deployment["resource_id"]) != resource_id,
                    int(existing_deployment["resource_generation"])
                    != resource_generation,
                    str(existing_deployment["resource_digest"]) != resource_digest,
                    existing_deployment["spec"] != deployment_spec,
                    bool(existing_deployment["enabled"]) != enabled,
                )
            ):
                desired_generation += 1
        deployment = self.repository.put_deployment(
            {
                "deployment_id": deployment_id,
                "kind": "model",
                "resource_id": resource_id,
                "resource_generation": resource_generation,
                "resource_digest": resource_digest,
                "target_node_id": node_id,
                "desired_generation": desired_generation,
                "spec": deployment_spec,
                "enabled": enabled,
            }
        )

        allowed = set(allowed_node_ids if enabled else ())
        existing_grants = [
            item
            for item in self.repository.list_resource_grants()
            if str(item["target_deployment_id"]) == deployment_id
            and item["revoked_at"] is None
        ]
        for grant in existing_grants:
            if str(grant["caller_node_id"]) not in allowed:
                self.repository.revoke_resource_grant(str(grant["grant_id"]))
        expires_at = self._clock() + 10 * 365 * 24 * 60 * 60
        for caller_node_id in sorted(allowed):
            digest = hashlib.sha256(
                f"{deployment_id}:{caller_node_id}:model_inference".encode()
            ).hexdigest()
            self.repository.put_resource_grant(
                {
                    "grant_id": f"grant_{digest[:40]}",
                    "caller_node_id": caller_node_id,
                    "target_deployment_id": deployment_id,
                    "capability": "model_inference",
                    "max_request_deadline": 600,
                    "expires_at": expires_at,
                }
            )
        return {
            "resource": resource,
            "deployment": deployment,
            "allowed_node_ids": sorted(allowed),
        }

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

    def reconcile_work_projections(self, request: dict) -> int:
        observed_at = float(request["observed_at"])
        if abs(observed_at - self._clock()) > 120:
            raise PermissionError("Work projection reconciliation timestamp rejected")
        transcript = {
            "audience": "knoa-work-projection-reconcile-v1",
            "workspace_id": self.workspace_id,
            "node_id": request["node_id"],
            "entity_kind": request["entity_kind"],
            "principal_id": request.get("principal_id", ""),
            "active_entity_ids": list(request.get("active_entity_ids", [])),
            "observed_at": observed_at,
        }
        self.verify_node_signed_request(
            str(request["node_id"]), transcript, str(request["signature"])
        )
        return self.repository.prune_work_projections(
            str(request["node_id"]),
            str(request["entity_kind"]),
            str(request.get("principal_id", "")),
            tuple(str(value) for value in request.get("active_entity_ids", [])),
        )

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
        deployment = self.repository.deployment(str(request["target_deployment_id"]))
        if deployment["kind"] != "model":
            raise PermissionError("Resource ticket requires a Model Deployment")
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
            "target_direct_gateway_url": str(target.get("direct_gateway_url", "")),
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
        self.repository.record_invocation_observation(str(request["node_id"]), request)

    @staticmethod
    def _load_or_create_key(path: Path) -> Ed25519PrivateKey:
        path = path.expanduser().resolve()

        def load_existing() -> Ed25519PrivateKey:
            try:
                validate_private_file(path, label="Hub identity")
            except RuntimeError as exc:
                raise PermissionError(str(exc)) from exc
            return Ed25519PrivateKey.from_private_bytes(
                _decode(path.read_text().strip())
            )

        if path.exists():
            return load_existing()
        prepare_private_directory(path.parent, label="Hub identity directory")
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
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
        restrict_private_file(path)
        fsync_directory(path.parent)
        return key


__all__ = ["HubService"]
