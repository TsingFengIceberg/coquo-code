"""Bounded, lease-proven recovery for abandoned Child executions."""

from __future__ import annotations

from dataclasses import dataclass
import os

from coquo.child_run_records import ChildRunStatus
from coquo.child_run_store import ChildRunInfo, ChildRunStore

MAX_CHILD_RECOVERY_SCAN = 100


@dataclass(frozen=True)
class ChildRecoveryDiagnostic:
    child_run_id: str | None
    outcome: str
    message: str


@dataclass(frozen=True)
class ChildRecoveryResult:
    recovered: tuple[ChildRunInfo, ...]
    diagnostics: tuple[ChildRecoveryDiagnostic, ...]


class ChildRunRecoveryService:
    """Recover only running work whose v2 OS lifetime lock is available."""

    def __init__(self, workspace) -> None:
        self.store = ChildRunStore(workspace)

    def recover(
        self,
        *,
        parent_session_id: str | None = None,
        child_run_id: str | None = None,
        limit: int = MAX_CHILD_RECOVERY_SCAN,
    ) -> ChildRecoveryResult:
        if type(limit) is not int or not 1 <= limit <= MAX_CHILD_RECOVERY_SCAN:
            raise ValueError("Child recovery limit is invalid")
        candidates: list[str] = []
        diagnostics: list[ChildRecoveryDiagnostic] = []
        if child_run_id is not None:
            candidates = [child_run_id]
        else:
            root = self.store.root
            if not root.exists() or root.is_symlink():
                return ChildRecoveryResult((), ())
            try:
                entries = sorted(
                    (
                        entry.name.removesuffix(".jsonl")
                        for entry in os.scandir(root)
                        if entry.name.endswith(".jsonl")
                    ),
                )[:limit]
            except OSError as error:
                return ChildRecoveryResult(
                    (), (ChildRecoveryDiagnostic(None, "scan_failed", str(error)[:512]),)
                )
            candidates = list(entries)
        recovered: list[ChildRunInfo] = []
        for candidate in candidates[:limit]:
            try:
                info = self.store.inspect(candidate)
            except BaseException as error:
                diagnostics.append(ChildRecoveryDiagnostic(candidate, "corrupt", str(error)[:512]))
                continue
            if parent_session_id is not None and info.parent_session_id != parent_session_id:
                diagnostics.append(
                    ChildRecoveryDiagnostic(
                        candidate, "wrong_parent", "Child Run belongs to another parent Session"
                    )
                )
                continue
            if info.status not in {ChildRunStatus.RUNNING, ChildRunStatus.CANCELLING}:
                continue
            try:
                lease = self.store.acquire_recovery_lease(candidate)
            except BaseException as error:
                outcome = (
                    "still_owned"
                    if "active execution lease" in str(error)
                    else "legacy_lease_ambiguous"
                    if "legacy_lease_ambiguous" in str(error)
                    else "recovery_rejected"
                )
                diagnostics.append(ChildRecoveryDiagnostic(candidate, outcome, str(error)[:512]))
                continue
            try:
                recovered.append(self.store.finish_interrupted(candidate))
            except BaseException as error:
                diagnostics.append(
                    ChildRecoveryDiagnostic(candidate, "append_failed", str(error)[:512])
                )
            finally:
                lease.close()
        return ChildRecoveryResult(tuple(recovered), tuple(diagnostics))
