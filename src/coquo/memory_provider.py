"""Replaceable Host-owned memory backend boundary.

Only the local append-only implementation is available today.  The protocol
keeps retrieval and lifecycle policy out of Provider adapters and leaves future
backends behind an explicit configuration gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from coquo.memory import MemoryRecord, MemoryScope, MemoryStatus
from coquo.memory_store import MemoryStore
from coquo.memory_observability import MemoryObservationLedger


class MemoryProvider(Protocol):
    """Minimal local/provider-neutral contract used by Host memory services."""

    def search(
        self,
        query: str,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
        statuses: frozenset[MemoryStatus] | None = None,
        touch: bool = True,
    ) -> tuple[MemoryRecord, ...]: ...

    def list(
        self,
        *,
        scope: MemoryScope | None = None,
        scope_id: str | None = None,
        status: MemoryStatus | None = None,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]: ...

    def mark_recalled(self, memory_ids: tuple[str, ...]) -> tuple[MemoryRecord, ...]: ...

    def find_exact(
        self,
        content: str,
        *,
        scope: MemoryScope,
        scope_id: str,
        category: str | None = None,
    ) -> tuple[MemoryRecord, ...]: ...

    def get(self, memory_id: str) -> MemoryRecord: ...

    def create_candidate(self, content: str, **kwargs) -> MemoryRecord: ...

    def confirm(self, memory_id: str) -> MemoryRecord: ...

    def update(self, memory_id: str, **kwargs) -> MemoryRecord: ...

    def transition(self, memory_id: str, status: MemoryStatus, **kwargs) -> MemoryRecord: ...


class LocalMemoryProvider:
    """Adapter exposing the durable local store through ``MemoryProvider``."""

    name = "local"

    def __init__(
        self,
        workspace: Path,
        *,
        observation_ledger: MemoryObservationLedger | None = None,
    ) -> None:
        self.store = MemoryStore(workspace, observation_ledger=observation_ledger)

    def search(self, query: str, **kwargs):
        return self.store.search(query, **kwargs)

    def list(self, **kwargs):
        return self.store.list(**kwargs)

    def find_exact(self, content: str, **kwargs):
        return self.store.find_exact(content, **kwargs)

    def mark_recalled(self, memory_ids: tuple[str, ...]):
        return self.store.mark_recalled(memory_ids)

    def get(self, memory_id: str):
        return self.store.get(memory_id)

    def create_candidate(self, content: str, **kwargs):
        return self.store.create_candidate(content, **kwargs)

    def confirm(self, memory_id: str):
        return self.store.confirm(memory_id)

    def update(self, memory_id: str, **kwargs):
        return self.store.update(memory_id, **kwargs)

    def transition(self, memory_id: str, status: MemoryStatus, **kwargs):
        return self.store.transition(memory_id, status, **kwargs)


def local_memory_provider(
    workspace: Path,
    *,
    observation_ledger: MemoryObservationLedger | None = None,
) -> LocalMemoryProvider:
    """Return the only currently supported backend without network access."""

    return LocalMemoryProvider(workspace, observation_ledger=observation_ledger)
