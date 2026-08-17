"""Exact, one-shot application of a sealed Team worktree patch.

The service is deliberately Host-owned.  It accepts only a worktree identity and
the sealed patch digest, performs all Git checks with fixed commands, and never
commits, stages, retries, or removes the source worktree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from coquo.git_worktree import (
    AuthorityRepository,
    GitWorktreeError,
    apply_patch,
    authority_ref_head,
    authority_status,
    check_patch,
    commit_is_ancestor,
    inspect_authority_repository,
    inspect_linked_worktree,
    untracked_diff,
    worktree_diff,
    worktree_untracked_paths,
)
from coquo.worktree_records import (
    WorktreeIntegrationEvidence,
    WorktreeOperation,
    WorktreeOperationFinished,
    WorktreeOperationStarted,
    WorktreeOutcome,
    WorktreeState,
    utc_now,
)
from coquo.worktree_service import WorktreeService
from coquo.worktree_store import WorktreeInfo, WorktreeStoreError


class WorktreeIntegrationError(RuntimeError):
    """One bounded, truthful integration rejection or recovery failure."""


@dataclass(frozen=True)
class IntegrationPreflight:
    team_id: str
    assignment_id: str
    worktree_id: str
    patch_sha256: str
    manifest_sha256: str
    target_ref: str
    target_head: str
    base_commit: str
    changed_paths: int
    patch_bytes: int
    precondition_sha256: str


@dataclass(frozen=True)
class IntegrationResult:
    status: str
    result_code: str
    message: str
    worktree_id: str
    team_id: str
    assignment_id: str
    patch_sha256: str
    manifest_sha256: str
    target_ref: str
    target_head: str
    changed_paths: int


class WorktreeIntegrationService:
    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.worktrees = WorktreeService(
            self.workspace,
            uuid_factory=uuid_factory,
            clock=clock,
        )
        self._uuid_factory = uuid_factory
        self._clock = clock

    def prepare(
        self,
        team_id: str,
        assignment_id: str,
        expected_patch_sha256: str,
    ) -> IntegrationPreflight:
        info = self._find_worktree(team_id, assignment_id)
        if info.state != WorktreeState.SEALED_CHANGES.value:
            raise WorktreeIntegrationError("worktree must contain sealed changes")
        sealed = info.sealed
        if sealed is None:
            raise WorktreeIntegrationError("sealed worktree evidence is missing")
        if expected_patch_sha256 != sealed.patch_sha256:
            raise WorktreeIntegrationError("expected patch digest does not match sealed work")
        authority = self._authority()
        if authority.target_ref != info.header.target_ref:
            raise WorktreeIntegrationError("authority target ref changed")
        if authority_status(authority):
            raise WorktreeIntegrationError("authority workspace must be clean before integration")
        target_head = authority_ref_head(authority, info.header.target_ref)
        if not commit_is_ancestor(authority, info.header.base_commit, target_head):
            raise WorktreeIntegrationError("sealed base is not an ancestor of target HEAD")
        binding = self.worktrees.inspect_binding(info.worktree_id)
        self._verify_source_unchanged(info, binding)
        try:
            patch, manifest = info_store_read(info, self.worktrees)
            check_patch(authority, patch)
        except (GitWorktreeError, WorktreeStoreError) as error:
            raise WorktreeIntegrationError(str(error)) from None
        precondition = _precondition_digest(
            {
                "assignment_id": info.header.assignment_id,
                "base_commit": info.header.base_commit,
                "manifest_sha256": sealed.manifest_sha256,
                "patch_sha256": sealed.patch_sha256,
                "target_head": target_head,
                "target_ref": info.header.target_ref,
                "team_id": info.header.team_id,
                "worktree_id": info.worktree_id,
            }
        )
        del manifest
        return IntegrationPreflight(
            team_id=info.header.team_id,
            assignment_id=info.header.assignment_id,
            worktree_id=info.worktree_id,
            patch_sha256=sealed.patch_sha256,
            manifest_sha256=sealed.manifest_sha256,
            target_ref=info.header.target_ref,
            target_head=target_head,
            base_commit=info.header.base_commit,
            changed_paths=sealed.changed_paths,
            patch_bytes=sealed.patch_bytes,
            precondition_sha256=precondition,
        )

    def integrate(
        self,
        prepared: IntegrationPreflight,
        *,
        action_digest: str,
    ) -> IntegrationResult:
        info = self.worktrees.store.inspect(prepared.worktree_id)
        operation_id = self._new_id("integration operation ID")
        with self.worktrees.store.acquire_lease(prepared.worktree_id):
            current = self.prepare(
                prepared.team_id,
                prepared.assignment_id,
                prepared.patch_sha256,
            )
            if current != prepared:
                raise WorktreeIntegrationError("integration identity changed before execution")
            self.worktrees.store.append(
                prepared.worktree_id,
                WorktreeOperationStarted(
                    info.record_count,
                    operation_id,
                    prepared.worktree_id,
                    WorktreeOperation.INTEGRATE,
                    self._clock(),
                ),
            )
            apply_started = False
            try:
                patch, _manifest = info_store_read(
                    self.worktrees.store.inspect(prepared.worktree_id), self.worktrees
                )
                authority = self._authority()
                # Re-run the non-mutating check after the durable start record but
                # before the effectful command.  A failed check is a known,
                # non-mutating rejection, not an ambiguous outcome.
                check_patch(authority, patch)
                apply_started = True
                apply_patch(authority, patch)
                target_head = authority_ref_head(self._authority(), prepared.target_ref)
                self.worktrees.store.append(
                    prepared.worktree_id,
                    WorktreeIntegrationEvidence(
                        info.record_count + 1,
                        operation_id,
                        prepared.worktree_id,
                        action_digest,
                        prepared.patch_sha256,
                        prepared.manifest_sha256,
                        prepared.target_ref,
                        target_head,
                        "applied",
                        self._clock(),
                    ),
                )
                self.worktrees.store.append(
                    prepared.worktree_id,
                    WorktreeOperationFinished(
                        info.record_count + 2,
                        operation_id,
                        prepared.worktree_id,
                        WorktreeOperation.INTEGRATE,
                        WorktreeOutcome.SUCCEEDED,
                        "applied",
                        "sealed patch applied; authority changes remain uncommitted",
                        self._clock(),
                    ),
                )
                return IntegrationResult(
                    "applied",
                    "applied",
                    "sealed patch applied; authority changes remain uncommitted",
                    prepared.worktree_id,
                    prepared.team_id,
                    prepared.assignment_id,
                    prepared.patch_sha256,
                    prepared.manifest_sha256,
                    prepared.target_ref,
                    target_head,
                    prepared.changed_paths,
                )
            except (GitWorktreeError, WorktreeStoreError, WorktreeIntegrationError) as error:
                latest = self.worktrees.store.inspect(prepared.worktree_id)
                outcome = (
                    WorktreeOutcome.OUTCOME_UNKNOWN if apply_started else WorktreeOutcome.FAILED
                )
                try:
                    self.worktrees.store.append(
                        prepared.worktree_id,
                        WorktreeOperationFinished(
                            latest.record_count,
                            operation_id,
                            prepared.worktree_id,
                            WorktreeOperation.INTEGRATE,
                            outcome,
                            "integration_unknown"
                            if outcome is WorktreeOutcome.OUTCOME_UNKNOWN
                            else "integration_failed",
                            str(error)[:4096],
                            self._clock(),
                        ),
                    )
                except WorktreeStoreError:
                    pass
                raise WorktreeIntegrationError(str(error)) from None

    def _find_worktree(self, team_id: str, assignment_id: str) -> WorktreeInfo:
        root = self.worktrees.store.root
        candidates = sorted(root.glob("*.jsonl")) if root.exists() else []
        matches: list[WorktreeInfo] = []
        for path in candidates:
            worktree_id = path.stem
            try:
                info = self.worktrees.store.inspect(worktree_id)
            except WorktreeStoreError:
                continue
            if info.header.team_id == team_id and info.header.assignment_id == assignment_id:
                matches.append(info)
        if len(matches) != 1:
            raise WorktreeIntegrationError("Team assignment does not identify exactly one worktree")
        return matches[0]

    def _authority(self) -> AuthorityRepository:
        try:
            return inspect_authority_repository(self.workspace)
        except GitWorktreeError as error:
            raise WorktreeIntegrationError(str(error)) from None

    def _verify_source_unchanged(self, info: WorktreeInfo, binding) -> None:
        if info.sealed is None:
            raise WorktreeIntegrationError("sealed worktree evidence is missing")
        try:
            checked = inspect_linked_worktree(binding)
            tracked = worktree_diff(checked)
            untracked = worktree_untracked_paths(checked)
            patch = b"".join([tracked, *(untracked_diff(checked, path) for path in untracked)])
        except GitWorktreeError as error:
            raise WorktreeIntegrationError(str(error)) from None
        if hashlib.sha256(patch).hexdigest() != info.sealed.patch_sha256:
            raise WorktreeIntegrationError("source worktree changed after sealing")

    def _new_id(self, label: str) -> str:
        value = self._uuid_factory()
        text = str(value)
        try:
            parsed = UUID(text)
        except (TypeError, ValueError):
            raise WorktreeIntegrationError(f"{label} is invalid") from None
        if parsed.version != 4 or text != str(parsed):
            raise WorktreeIntegrationError(f"{label} is invalid")
        return text


def info_store_read(info: WorktreeInfo, service: WorktreeService) -> tuple[bytes, bytes]:
    if info.sealed is None:
        raise WorktreeStoreError("sealed worktree evidence is missing")
    return service.store.read_verified_artifacts(
        info.worktree_id,
        patch_sha256=info.sealed.patch_sha256,
        manifest_sha256=info.sealed.manifest_sha256,
    )


def _precondition_digest(value: dict[str, object]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
