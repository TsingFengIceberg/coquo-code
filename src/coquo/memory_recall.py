"""Host-owned, bounded recall for confirmed workspace memory facts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from coquo.core.contracts import MemoryEvidence
from coquo.memory import MemoryAccessContext, MemoryStatus
from coquo.memory_config import MemoryConfigStore
from coquo.memory_provider import MemoryProvider, local_memory_provider
from coquo.memory_observability import MemoryObservationLedger
from coquo.memory_retrieval import retriever_for
from coquo.session_records import workspace_fingerprint

MAX_RECALL_ITEMS = 8
MAX_RECALL_ITEM_BYTES = 4096
MAX_RECALL_TOTAL_BYTES = 16 * 1024
_TOKEN = re.compile(r"[A-Za-z0-9_:-]{3,}")


class MemoryRecallService:
    """Resolve one prompt into frozen, untrusted evidence without transcript writes."""

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
        self._retrievers: dict[str, object] = {}

    def _retriever(self, mode: str):
        retriever = self._retrievers.get(mode)
        if retriever is None:
            retriever = retriever_for(mode)
            self._retrievers[mode] = retriever
        return retriever

    def recall(self, prompt: str) -> tuple[MemoryEvidence, ...]:
        config = self._config.load()
        if not config.enabled or config.effective_recall.value != "on":
            self._observations.record("recall", "disabled", actor="host")
            return ()
        if not isinstance(prompt, str) or not prompt.strip():
            self._observations.record("recall", "empty", actor="host")
            return ()
        access = self._access_factory()
        if not isinstance(access, MemoryAccessContext):
            self._observations.record("recall", "failed", actor="host", reason="invalid_access")
            raise ValueError("memory access context is invalid")
        queries = _queries(prompt)
        found: dict[str, object] = {}
        degraded = False
        candidate_count = 0
        cache_hits = 0
        cache_misses = 0
        for query in queries:
            for scope, scope_id in access.read_scopes:
                result = self._retriever(config.retrieval.value).retrieve(
                    self._provider,
                    query,
                    scope=scope,
                    scope_id=scope_id,
                    limit=MAX_RECALL_ITEMS,
                    statuses=frozenset({MemoryStatus.CONFIRMED}),
                    touch=False,
                )
                degraded = degraded or result.degraded
                candidate_count += result.candidate_count
                cache_hits += result.cache_hits
                cache_misses += result.cache_misses
                for record in result.records:
                    if record.status is MemoryStatus.CONFIRMED and access.permits(
                        record.scope, record.scope_id
                    ):
                        found[record.memory_id] = record
        records = sorted(
            found.values(), key=lambda item: (item.created_at, item.memory_id), reverse=True
        )
        evidence: list[MemoryEvidence] = []
        total = 0
        for record in records:
            item = MemoryEvidence(
                memory_id=record.memory_id,
                scope=record.scope.value,
                content=record.content,
                category=record.category,
                confidence=record.confidence,
                source_session_id=record.source_session_id,
                source_turn=record.source_turn,
            )
            size = len(item.rendered.encode("utf-8"))
            if size > MAX_RECALL_ITEM_BYTES or total + size > MAX_RECALL_TOTAL_BYTES:
                continue
            evidence.append(item)
            total += size
            if len(evidence) >= MAX_RECALL_ITEMS:
                break
        if evidence:
            self._provider.mark_recalled(tuple(item.memory_id for item in evidence))
        self._observations.record(
            "recall",
            "degraded" if degraded else "completed",
            actor=access.actor,
            scope_kinds=tuple(scope.value for scope, _ in access.read_scopes),
            record_count=len(evidence),
            degraded=degraded,
            reason=(
                f"strategy={config.retrieval.value};queries={len(queries)};records={len(found)};"
                f"candidates={candidate_count};cache_hits={cache_hits};cache_misses={cache_misses}"
            ),
        )
        return tuple(evidence)

    @property
    def observations(self) -> tuple:
        return self._observations.snapshot()


def _queries(prompt: str) -> tuple[str, ...]:
    bounded = prompt.strip()
    values: list[str] = []
    if len(bounded) <= 512:
        values.append(bounded)
    for token in _TOKEN.findall(bounded):
        if token.casefold() not in {value.casefold() for value in values}:
            values.append(token)
        if len(values) >= 12:
            break
    return tuple(values)


def empty_memory_recall(_prompt: str) -> tuple[MemoryEvidence, ...]:
    """Return no evidence for isolated runtimes and unit-test defaults."""

    return ()
