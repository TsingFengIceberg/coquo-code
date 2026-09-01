"""Replaceable local retrieval boundary with deterministic local semantics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from threading import RLock
import unicodedata
from typing import Protocol

from coquo.memory import MemoryRecord, MemoryScope, MemoryStatus
from coquo.memory_provider import MemoryProvider


@dataclass(frozen=True)
class MemoryRetrievalResult:
    records: tuple[MemoryRecord, ...]
    strategy: str
    degraded: bool = False
    candidate_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def __post_init__(self) -> None:
        for value, label in (
            (self.candidate_count, "candidate count"),
            (self.cache_hits, "cache hits"),
            (self.cache_misses, "cache misses"),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"memory retrieval {label} is invalid")


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
        records = store.search(
            query,
            scope=scope,
            scope_id=scope_id,
            limit=limit,
            statuses=statuses,
            touch=touch,
        )
        return MemoryRetrievalResult(
            records,
            strategy="text",
            candidate_count=len(records),
        )


class SemanticMemoryRetriever:
    """Deterministic local vector retrieval with no network or model dependency.

    The feature-hashed vector is intentionally small and bounded.  It combines
    normalized coding-domain aliases, Unicode word/character features, and
    character bigrams so that common paraphrases such as "deploy"/"release"
    and Chinese "部署"/"发布" can match without a network call.  A future
    learned embedding backend can replace this class behind the same protocol.
    """

    def __init__(self) -> None:
        self._feature_cache: dict[
            str, tuple[tuple[str, str, str, str], dict[str, float], tuple[float, ...]]
        ] = {}
        self._lock = RLock()

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
        if not isinstance(query, str) or not query.strip():
            raise ValueError("semantic memory query must not be blank")
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("semantic memory limit is out of bounds")
        selected_statuses = statuses or frozenset(
            {MemoryStatus.CANDIDATE, MemoryStatus.CONFIRMED, MemoryStatus.STALE}
        )
        records: dict[str, MemoryRecord] = {}
        for selected in selected_statuses:
            for record in store.list(
                scope=scope,
                scope_id=scope_id,
                status=selected,
                limit=100,
            ):
                records[record.memory_id] = record
        query_features = _semantic_features(query)
        query_vector = _vector(query)
        ranked = []
        cache_hits = 0
        cache_misses = 0
        with self._lock:
            active_ids = set(records)
            for memory_id in tuple(self._feature_cache):
                if memory_id not in active_ids:
                    del self._feature_cache[memory_id]
            for record in records.values():
                fingerprint = (
                    record.content,
                    record.status.value,
                    record.updated_at,
                    record.scope_id,
                )
                cached = self._feature_cache.get(record.memory_id)
                if cached is not None and cached[0] == fingerprint:
                    record_features, record_vector = cached[1], cached[2]
                    cache_hits += 1
                else:
                    record_features = _semantic_features(record.content)
                    record_vector = _vector(record.content)
                    self._feature_cache[record.memory_id] = (
                        fingerprint,
                        record_features,
                        record_vector,
                    )
                    if len(self._feature_cache) > MAX_SEMANTIC_CACHE_ENTRIES:
                        oldest_id = next(iter(self._feature_cache))
                        del self._feature_cache[oldest_id]
                    cache_misses += 1
                score = _cosine(query_vector, record_vector)
                shared_strong_feature = any(
                    feature in record_features
                    for feature in query_features
                    if feature.startswith(("word:", "phrase:"))
                )
                threshold = (
                    SEMANTIC_MIN_SCORE if shared_strong_feature else SEMANTIC_NGRAM_MIN_SCORE
                )
                if score >= threshold:
                    ranked.append((score, record))
        ranked.sort(key=lambda item: (item[0], item[1].created_at, item[1].memory_id), reverse=True)
        matches = tuple(record for _, record in ranked[:limit])
        if matches and touch:
            store.mark_recalled(tuple(record.memory_id for record in matches))
        return MemoryRetrievalResult(
            matches,
            strategy="semantic-local-v1",
            degraded=False,
            candidate_count=len(records),
            cache_hits=cache_hits,
            cache_misses=cache_misses,
        )


def retriever_for(mode: str) -> MemoryRetriever:
    if mode == "semantic":
        return SemanticMemoryRetriever()
    return TextMemoryRetriever()


SEMANTIC_VECTOR_DIMENSIONS = 256
MAX_SEMANTIC_CACHE_ENTRIES = 1024
SEMANTIC_MIN_SCORE = 0.12
SEMANTIC_NGRAM_MIN_SCORE = 0.34
_TOKEN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]")
_COMPACT = re.compile(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_ALIAS_GROUPS = (
    ("release", "deploy", "deployment", "rollout", "发布", "部署"),
    ("rollback", "revert", "恢复", "回滚"),
    ("latency", "delay", "response-time", "响应时间", "延迟"),
    ("retry", "retries", "重试"),
    ("region", "zone", "区域"),
    ("test", "tests", "testing", "验证", "测试"),
    ("incident", "failure", "故障", "异常"),
)
_ALIASES = {term.casefold(): group[0] for group in _ALIAS_GROUPS for term in group}


def _semantic_features(text: str) -> dict[str, float]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    features: dict[str, float] = {}
    for token in _TOKEN.findall(normalized):
        if token in _STOPWORDS:
            continue
        canonical = _ALIASES.get(token, token)
        features[f"word:{canonical}"] = features.get(f"word:{canonical}", 0.0) + 2.0
    compact = "".join(_COMPACT.findall(normalized))
    for index in range(max(0, len(compact) - 1)):
        gram = compact[index : index + 2]
        features[f"char:{gram}"] = features.get(f"char:{gram}", 0.0) + 0.35
    for group in _ALIAS_GROUPS:
        canonical, *aliases = group
        if any(alias.casefold() in normalized for alias in (canonical, *aliases)):
            features[f"phrase:{canonical}"] = features.get(f"phrase:{canonical}", 0.0) + 3.0
    return features


def _vector(text: str) -> tuple[float, ...]:
    vector = [0.0] * SEMANTIC_VECTOR_DIMENSIONS
    for feature, weight in _semantic_features(text).items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % SEMANTIC_VECTOR_DIMENSIONS
        vector[index] += weight if digest[4] & 1 else -weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))
