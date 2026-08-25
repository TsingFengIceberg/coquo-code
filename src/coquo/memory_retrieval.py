"""Replaceable local retrieval boundary with deterministic semantic fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from coquo.memory import MemoryRecord, MemoryScope, MemoryStatus
from coquo.memory_provider import MemoryProvider


@dataclass(frozen=True)
class MemoryRetrievalResult:
    records: tuple[MemoryRecord, ...]
    strategy: str
    degraded: bool = False


class MemoryRetriever(Protocol):
    def retrieve(
        self,
        store: MemoryProvider,
        query: str,
        *,
        scope: MemoryScope,
        scope_id: str,
        limit: int,
        statuses: frozenset[MemoryStatus] | None = None,
        touch: bool = True,
    ) -> MemoryRetrievalResult:
        """Return bounded records without changing the Host policy boundary."""


class TextMemoryRetriever:
    def retrieve(
        self,
        store: MemoryProvider,
        query: str,
        *,
        scope,
        scope_id,
        limit,
        statuses=None,
        touch=True,
    ):
        return MemoryRetrievalResult(
            store.search(
                query,
                scope=scope,
                scope_id=scope_id,
                limit=limit,
                statuses=statuses,
                touch=touch,
            ),
            strategy="text",
        )


class SemanticMemoryRetriever:
    """Reserved semantic strategy; safely falls back without an embedding backend."""

    def retrieve(
        self,
        store: MemoryProvider,
        query: str,
        *,
        scope,
        scope_id,
        limit,
        statuses=None,
        touch=True,
    ):
        result = TextMemoryRetriever().retrieve(
            store,
            query,
            scope=scope,
            scope_id=scope_id,
            limit=limit,
            statuses=statuses,
            touch=touch,
        )
        return MemoryRetrievalResult(
            result.records,
            strategy="text-fallback",
            degraded=True,
        )


def retriever_for(mode: str) -> MemoryRetriever:
    if mode == "semantic":
        return SemanticMemoryRetriever()
    return TextMemoryRetriever()
