"""Configuration application service and apply coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from knoa_platform.configuration.models import (
    ConfigApplyError,
    ConfigDraft,
    ConfigPublishResult,
    ConfigRevision,
    ConfigValidationIssue,
    ConfigValidationResult,
    ManagedConfig,
)
from knoa_platform.configuration.repository import ConfigRegistry

ConfigValidator = Callable[
    [ManagedConfig],
    Awaitable[tuple[ConfigValidationIssue, ...]],
]
ConfigPreflight = Callable[[ManagedConfig], Awaitable[None]]
ConfigApplier = Callable[[ConfigRevision, ConfigRevision], Awaitable[None]]


async def _valid(_: ManagedConfig) -> tuple[ConfigValidationIssue, ...]:
    return ()


async def _noop(_: ManagedConfig) -> None:
    return None


async def _apply_noop(_: ConfigRevision, __: ConfigRevision) -> None:
    return None


class ConfigurationService:
    """The only managed configuration write boundary."""

    def __init__(
        self,
        registry: ConfigRegistry,
        initial: ManagedConfig,
        *,
        bootstrap_actor: str,
        validator: ConfigValidator = _valid,
        preflight: ConfigPreflight = _noop,
        applier: ConfigApplier = _apply_noop,
    ) -> None:
        self._registry = registry
        self._validator = validator
        self._preflight = preflight
        self._applier = applier
        self._lock = asyncio.Lock()
        self._registry.initialize(initial, actor=bootstrap_actor)

    def current(self) -> ConfigRevision:
        return self._registry.current()

    def state(self):
        return self._registry.state()

    def history(self, *, limit: int = 50) -> tuple[ConfigRevision, ...]:
        return self._registry.history(limit=limit)

    def revision(self, revision_id: str) -> ConfigRevision:
        return self._registry.revision(revision_id)

    def diff(self, from_revision_id: str, to_revision_id: str) -> tuple[dict, ...]:
        before = self.revision(from_revision_id).document.model_dump(mode="json")
        after = self.revision(to_revision_id).document.model_dump(mode="json")
        changes: list[dict] = []

        def walk(left, right, path: str) -> None:
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(left.keys() | right.keys()):
                    child = f"{path}/{key}"
                    if key not in left:
                        changes.append(
                            {"op": "add", "path": child, "value": right[key]}
                        )
                    elif key not in right:
                        changes.append(
                            {"op": "remove", "path": child, "old": left[key]}
                        )
                    else:
                        walk(left[key], right[key], child)
                return
            if left != right:
                changes.append(
                    {"op": "replace", "path": path or "/", "old": left, "value": right}
                )

        walk(before, after, "")
        return tuple(changes)

    def create_draft(self, *, actor: str) -> ConfigDraft:
        return self._registry.create_draft(actor=actor)

    def draft(self, draft_id: str) -> ConfigDraft:
        return self._registry.draft(draft_id)

    def replace_draft(
        self,
        draft_id: str,
        document: ManagedConfig,
        *,
        expected_version: int,
        actor: str,
    ) -> ConfigDraft:
        return self._registry.replace_draft(
            draft_id,
            document,
            expected_version=expected_version,
            actor=actor,
        )

    async def validate(self, draft_id: str) -> ConfigValidationResult:
        draft = self._registry.draft(draft_id)
        issues = await self._validator(draft.document)
        return ConfigValidationResult(valid=not issues, issues=issues)

    async def preflight(self, draft_id: str) -> ConfigValidationResult:
        draft = self._registry.draft(draft_id)
        issues = await self._validator(draft.document)
        if issues:
            return ConfigValidationResult(valid=False, issues=issues)
        try:
            await self._preflight(draft.document)
        except ConfigApplyError as exc:
            return ConfigValidationResult(
                valid=False,
                issues=(
                    ConfigValidationIssue(
                        code=exc.code,
                        path="",
                        message=str(exc),
                    ),
                ),
            )
        return ConfigValidationResult(valid=True)

    async def publish(
        self,
        draft_id: str,
        *,
        expected_version: int,
        actor: str,
        summary: str,
    ) -> ConfigPublishResult:
        async with self._lock:
            validation = await self.validate(draft_id)
            if not validation.valid:
                raise ConfigApplyError("validation_failed", "Configuration is invalid")
            draft = self._registry.draft(draft_id)
            await self._preflight(draft.document)
            previous = self._registry.current()
            revision = self._registry.publish_draft(
                draft_id,
                expected_version=expected_version,
                actor=actor,
                summary=summary,
            )
            try:
                await self._applier(previous, revision)
            except ConfigApplyError as exc:
                state = self._registry.mark_failed(revision.revision_id, exc.code)
                return ConfigPublishResult(revision=revision, state=state)
            except Exception:
                state = self._registry.mark_failed(
                    revision.revision_id,
                    "apply_failed",
                )
                return ConfigPublishResult(revision=revision, state=state)
            state = self._registry.mark_applied(revision.revision_id)
            return ConfigPublishResult(revision=revision, state=state)

    async def rollback(
        self,
        revision_id: str,
        *,
        actor: str,
        summary: str,
    ) -> ConfigPublishResult:
        async with self._lock:
            previous = self._registry.current()
            revision = self._registry.rollback(
                revision_id,
                actor=actor,
                summary=summary,
            )
            try:
                await self._preflight(revision.document)
                await self._applier(previous, revision)
            except ConfigApplyError as exc:
                state = self._registry.mark_failed(revision.revision_id, exc.code)
                return ConfigPublishResult(revision=revision, state=state)
            except Exception:
                state = self._registry.mark_failed(revision.revision_id, "apply_failed")
                return ConfigPublishResult(revision=revision, state=state)
            state = self._registry.mark_applied(revision.revision_id)
            return ConfigPublishResult(revision=revision, state=state)
