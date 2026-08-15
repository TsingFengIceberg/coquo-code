"""Host-owned worktree lifecycle and bounded change sealing."""

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
    LinkedWorktreeBinding,
    add_linked_worktree,
    authority_status,
    inspect_authority_repository,
    inspect_linked_worktree,
    untracked_diff,
    worktree_diff,
    worktree_untracked_paths,
)
from coquo.worktree_records import (
    MAX_WORKTREE_CHANGED_PATHS,
    MAX_WORKTREE_PATCH_BYTES,
    WorktreeOperation,
    WorktreeOperationFinished,
    WorktreeOperationStarted,
    WorktreeOutcome,
    WorktreeSealed,
    WorktreeState,
    utc_now,
)
from coquo.worktree_store import WorktreeInfo, WorktreeStore, WorktreeStoreError


class WorktreeServiceError(RuntimeError):
    """Raised when a lifecycle transition cannot be proved safe."""


@dataclass(frozen=True)
class SealedChange:
    worktree_id: str
    patch_sha256: str
    patch_bytes: int
    changed_paths: tuple[str, ...]
    manifest_sha256: str
    artifact: Path


class WorktreeService:
    def __init__(
        self,
        workspace: Path,
        *,
        uuid_factory: Callable[[], UUID | str] = uuid4,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self.store = WorktreeStore(self.workspace, clock=clock)
        self._uuid_factory = uuid_factory
        self._clock = clock

    def provision(
        self,
        *,
        team_id: str,
        assignment_id: str,
        child_run_id: str,
        member_id: str,
        role_contract: str,
        worktree_id: str | None = None,
    ) -> WorktreeInfo:
        authority = self._authority()
        if authority_status(authority):
            raise WorktreeServiceError(
                "authority workspace must be clean before worktree provisioning"
            )
        worktree_id = self._new_id() if worktree_id is None else worktree_id
        relative_path = f".coquo/worktrees/{authority.workspace_fingerprint}/{worktree_id}"
        branch = f"coquo/team/{team_id}/{assignment_id}"
        binding = LinkedWorktreeBinding(
            authority,
            authority.root / relative_path,
            worktree_id,
            branch,
            authority.head,
            relative_path,
        )
        self.store.declare(
            worktree_id=worktree_id,
            team_id=team_id,
            assignment_id=assignment_id,
            child_run_id=child_run_id,
            member_id=member_id,
            role_contract=role_contract,
            target_ref=authority.target_ref,
            base_commit=authority.head,
            branch=branch,
            relative_path=relative_path,
            created_at=self._clock(),
        )
        operation_id = self._new_id()
        with self.store.acquire_lease(worktree_id):
            self.store.append(
                worktree_id,
                WorktreeOperationStarted(
                    1, operation_id, worktree_id, WorktreeOperation.PROVISION, self._clock()
                ),
            )
            try:
                add_linked_worktree(authority, binding)
                checked = inspect_linked_worktree(binding)
                if checked.base_commit != authority.head:
                    raise WorktreeServiceError("created worktree HEAD does not match its base")
            except (GitWorktreeError, OSError, WorktreeServiceError) as error:
                outcome = (
                    WorktreeOutcome.OUTCOME_UNKNOWN
                    if self._binding_exists(binding)
                    else WorktreeOutcome.FAILED
                )
                self.store.append(
                    worktree_id,
                    WorktreeOperationFinished(
                        2,
                        operation_id,
                        worktree_id,
                        WorktreeOperation.PROVISION,
                        outcome,
                        "provision_unknown"
                        if outcome is WorktreeOutcome.OUTCOME_UNKNOWN
                        else "provision_failed",
                        str(error)[:4096],
                        self._clock(),
                    ),
                )
                raise WorktreeServiceError(str(error)) from None
            self.store.append(
                worktree_id,
                WorktreeOperationFinished(
                    2,
                    operation_id,
                    worktree_id,
                    WorktreeOperation.PROVISION,
                    WorktreeOutcome.SUCCEEDED,
                    "provisioned",
                    "linked worktree ready",
                    self._clock(),
                ),
            )
        return self.store.inspect(worktree_id)

    def inspect_binding(self, worktree_id: str) -> LinkedWorktreeBinding:
        info = self.store.inspect(worktree_id)
        authority = self._authority()
        binding = LinkedWorktreeBinding(
            authority,
            authority.root / info.header.relative_path,
            worktree_id,
            info.header.branch,
            info.header.base_commit,
            info.header.relative_path,
        )
        return inspect_linked_worktree(binding)

    def seal(self, worktree_id: str) -> SealedChange:
        info = self.store.inspect(worktree_id)
        if info.state != WorktreeState.READY.value:
            raise WorktreeServiceError("worktree is not ready to seal")
        binding = self.inspect_binding(worktree_id)
        operation_id = self._new_id()
        with self.store.acquire_lease(worktree_id):
            self.store.append(
                worktree_id,
                WorktreeOperationStarted(
                    info.record_count,
                    operation_id,
                    worktree_id,
                    WorktreeOperation.SEAL,
                    self._clock(),
                ),
            )
            try:
                tracked_patch = worktree_diff(binding)
                untracked = worktree_untracked_paths(binding)
                if len(untracked) + (1 if tracked_patch else 0) > MAX_WORKTREE_CHANGED_PATHS:
                    raise WorktreeServiceError("worktree change path bound exceeded")
                chunks = [tracked_patch]
                for path in untracked:
                    chunks.append(untracked_diff(binding, path))
                patch = b"".join(chunks)
                if len(patch) > MAX_WORKTREE_PATCH_BYTES:
                    raise WorktreeServiceError("worktree patch bound exceeded")
                changed_paths = tuple(untracked) + (("<tracked>",) if tracked_patch else ())
                manifest = json.dumps(
                    {"paths": changed_paths, "patch_bytes": len(patch)},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                patch_sha = hashlib.sha256(patch).hexdigest()
                manifest_sha = hashlib.sha256(manifest).hexdigest()
                artifact = self.store.write_patch_artifact(worktree_id, patch)
                self.store.append(
                    worktree_id,
                    WorktreeSealed(
                        info.record_count + 1,
                        operation_id,
                        worktree_id,
                        patch_sha,
                        len(patch),
                        len(changed_paths),
                        manifest_sha,
                        self._clock(),
                    ),
                )
                self.store.append(
                    worktree_id,
                    WorktreeOperationFinished(
                        info.record_count + 2,
                        operation_id,
                        worktree_id,
                        WorktreeOperation.SEAL,
                        WorktreeOutcome.SUCCEEDED,
                        "sealed",
                        "bounded patch sealed",
                        self._clock(),
                    ),
                )
                return SealedChange(
                    worktree_id, patch_sha, len(patch), changed_paths, manifest_sha, artifact
                )
            except (GitWorktreeError, WorktreeStoreError, WorktreeServiceError) as error:
                outcome = (
                    WorktreeOutcome.OUTCOME_UNKNOWN
                    if self.store.artifact_path(worktree_id).exists()
                    else WorktreeOutcome.FAILED
                )
                self.store.append(
                    worktree_id,
                    WorktreeOperationFinished(
                        info.record_count + 1,
                        operation_id,
                        worktree_id,
                        WorktreeOperation.SEAL,
                        outcome,
                        "seal_unknown"
                        if outcome is WorktreeOutcome.OUTCOME_UNKNOWN
                        else "seal_failed",
                        str(error)[:4096],
                        self._clock(),
                    ),
                )
                raise WorktreeServiceError(str(error)) from None

    def _authority(self) -> AuthorityRepository:
        try:
            return inspect_authority_repository(self.workspace)
        except GitWorktreeError as error:
            raise WorktreeServiceError(str(error)) from None

    def _binding_exists(self, binding: LinkedWorktreeBinding) -> bool:
        try:
            inspect_linked_worktree(binding)
            return True
        except GitWorktreeError:
            return False

    def _new_id(self) -> str:
        value = self._uuid_factory()
        if isinstance(value, UUID):
            value = str(value)
        if not isinstance(value, str):
            raise WorktreeServiceError("worktree ID factory returned invalid value")
        return value
