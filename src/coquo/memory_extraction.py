"""Safe post-commit extraction of explicit or conservatively stated memory facts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Callable

from coquo.core.contracts import CommittedTurn
from coquo.memory import MemoryAccessContext, MemoryCaptureMode
from coquo.memory import MemoryError as SemanticMemoryError
from coquo.memory import MemoryStatus, MemoryWriteMode
from coquo.memory_config import MemoryConfigStore
from coquo.memory_store import MemoryStoreError
from coquo.memory_provider import MemoryProvider, local_memory_provider
from coquo.memory_observability import MemoryObservationLedger
from coquo.session_records import workspace_fingerprint

_MARKER = re.compile(r"(?:^|\s)(?:remember\s+that|remember:|请记住)\s+(.+?)\s*$", re.IGNORECASE)
_CONSERVATIVE_MARKERS = (
    re.compile(
        r"^\s*(?:I prefer|I always use|I usually use|our project uses|the project requires)\b", re.I
    ),
    re.compile(r"^\s*(?:我偏好|我通常使用|我们使用|项目要求|项目采用).+"),
)
MAX_EXTRACTED_CANDIDATE_BYTES = 2048


@dataclass(frozen=True)
class MemoryExtractionResult:
    """Safe outcome of one post-commit extraction attempt."""

    mode: MemoryWriteMode
    memory_id: str | None = None
    confirmed: bool = False
    reason: str | None = None
    partial: bool = False


@dataclass(frozen=True)
class PreparedMemoryExtraction:
    """Read-only preparation result for one post-commit write."""

    mode: MemoryWriteMode
    content: str
    access: MemoryAccessContext
    capture: MemoryCaptureMode
    explicit: bool


class MemoryCandidateExtractor:
    """Extract bounded user memory facts after durable commit.

    Conservative capture is opt-in and only creates candidates from a small
    allow-list of preference and project-rule sentence forms.  It never
    auto-confirms an implicit candidate, even when ``write=auto`` is selected.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        access_factory: Callable[[], MemoryAccessContext] | None = None,
        provider_factory: Callable[[Path], MemoryProvider] | None = None,
        observation_ledger: MemoryObservationLedger | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve(strict=True)
        self._config = MemoryConfigStore(self.workspace)
        self._observations = observation_ledger or MemoryObservationLedger()
        self._provider = (
            local_memory_provider(self.workspace, observation_ledger=self._observations)
            if provider_factory is None
            else provider_factory(self.workspace)
        )
        self._scope_id = workspace_fingerprint(self.workspace)
        self._access_factory = access_factory or (lambda: MemoryAccessContext.host(self._scope_id))

    def prepare(self, turn: CommittedTurn) -> PreparedMemoryExtraction | None:
        """Resolve policy, marker, and scope without mutating durable memory."""
        try:
            config = self._config.load()
        except SemanticMemoryError:
            self._observations.record(
                "candidate_extraction", "failed", actor="host", reason="config_error"
            )
            return None
        mode = config.effective_write
        if mode is MemoryWriteMode.OFF:
            self._observations.record("candidate_extraction", "disabled", actor="host")
            return None
        captured = _capture_memory_content(turn.user.text, config.capture)
        if captured is None:
            self._observations.record(
                "candidate_extraction",
                "empty",
                actor="host",
                reason="no_accepted_memory_pattern",
            )
            return None
        access = self._access_factory()
        if not isinstance(access, MemoryAccessContext):
            self._observations.record(
                "candidate_extraction", "failed", actor="host", reason="invalid_access"
            )
            return None
        if access.write_target is None:
            self._observations.record(
                "candidate_extraction", "denied", actor=access.actor, reason="scope_denied"
            )
            return None
        content, explicit = captured
        return PreparedMemoryExtraction(mode, content, access, config.capture, explicit)

    def after_commit(
        self,
        turn: CommittedTurn,
        *,
        session_id: str,
        source_turn: int,
        authorized: bool = False,
        prepared: PreparedMemoryExtraction | None = None,
    ) -> MemoryExtractionResult:
        if not authorized:
            self._observations.record(
                "candidate_extraction", "denied", actor="host", reason="permission_required"
            )
            return MemoryExtractionResult(MemoryWriteMode.OFF, reason="permission_required")
        prepared = prepared or self.prepare(turn)
        if prepared is None:
            try:
                config = self._config.load()
            except SemanticMemoryError as error:
                return MemoryExtractionResult(MemoryWriteMode.OFF, reason=f"config_error:{error}")
            reason = (
                "no_explicit_marker"
                if config.capture is MemoryCaptureMode.EXPLICIT
                else "no_accepted_memory_pattern"
            )
            return MemoryExtractionResult(config.effective_write, reason=reason)
        mode = prepared.mode
        access = prepared.access
        content = prepared.content
        captured = _capture_memory_content(turn.user.text, prepared.capture)
        if captured is None or captured[0] != content or captured[1] != prepared.explicit:
            self._observations.record(
                "candidate_extraction", "failed", actor=access.actor, reason="stale_preparation"
            )
            return MemoryExtractionResult(mode, reason="stale_preparation")
        target = access.write_target
        if target is None:
            self._observations.record(
                "candidate_extraction", "denied", actor=access.actor, reason="scope_denied"
            )
            return MemoryExtractionResult(mode, reason="write_scope_denied")
        scope, scope_id = target
        mutation_started = False
        try:
            existing = self._provider.find_exact(
                content,
                scope=scope,
                scope_id=scope_id,
                category=("explicit_user_fact" if prepared.explicit else "conservative_candidate"),
            )
            if existing:
                if (
                    mode is MemoryWriteMode.AUTO
                    and prepared.explicit
                    and existing[0].status is MemoryStatus.CANDIDATE
                ):
                    self._provider.confirm(existing[0].memory_id)
                    self._observations.record(
                        "candidate_extraction",
                        "confirmed",
                        actor=access.actor,
                        scope_kinds=(scope.value,),
                        record_count=1,
                        reason="duplicate_confirmed",
                    )
                    return MemoryExtractionResult(
                        mode,
                        memory_id=existing[0].memory_id,
                        confirmed=True,
                        reason="duplicate_confirmed",
                    )
                self._observations.record(
                    "candidate_extraction",
                    "duplicate",
                    actor=access.actor,
                    scope_kinds=(scope.value,),
                    record_count=1,
                )
                return MemoryExtractionResult(
                    mode, memory_id=existing[0].memory_id, reason="duplicate"
                )
            candidate = self._provider.create_candidate(
                content,
                scope=scope,
                scope_id=scope_id,
                category=("explicit_user_fact" if prepared.explicit else "conservative_candidate"),
                confidence=1.0,
                source_session_id=session_id,
                source_turn=source_turn,
            )
            mutation_started = True
            if mode is MemoryWriteMode.AUTO and prepared.explicit:
                self._provider.confirm(candidate.memory_id)
            result = MemoryExtractionResult(
                mode,
                memory_id=candidate.memory_id,
                confirmed=mode is MemoryWriteMode.AUTO and prepared.explicit,
                reason=(
                    None if prepared.explicit else "conservative_candidate_requires_confirmation"
                ),
            )
            self._observations.record(
                "candidate_extraction",
                "confirmed" if result.confirmed else "candidate",
                actor=access.actor,
                scope_kinds=(scope.value,),
                record_count=1,
            )
            return result
        except (MemoryStoreError, SemanticMemoryError) as error:
            self._observations.record(
                "candidate_extraction", "failed", actor=access.actor, reason="store_error"
            )
            return MemoryExtractionResult(
                mode,
                reason=f"store_error:{error}",
                partial=mutation_started,
            )

    @property
    def observations(self) -> tuple:
        return self._observations.snapshot()


def _explicit_memory_content(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    match = _MARKER.search(text)
    if match is None:
        return None
    content = match.group(1).strip()
    if not content or len(content.encode("utf-8")) > MAX_EXTRACTED_CANDIDATE_BYTES:
        return None
    if "\x00" in content:
        return None
    return content


def _capture_memory_content(text: str, capture: MemoryCaptureMode) -> tuple[str, bool] | None:
    explicit = _explicit_memory_content(text)
    if explicit is not None:
        return explicit, True
    if capture is not MemoryCaptureMode.CONSERVATIVE:
        return None
    if not isinstance(text, str) or "\x00" in text:
        return None
    content = text.strip()
    if not content or len(content.encode("utf-8")) > MAX_EXTRACTED_CANDIDATE_BYTES:
        return None
    if any(pattern.match(content) for pattern in _CONSERVATIVE_MARKERS):
        return content, False
    return None
