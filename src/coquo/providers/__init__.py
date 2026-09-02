"""Public provider profile and runtime management APIs."""

from coquo.providers.definitions import (
    ADAPTER_CONTRACT_VERSION,
    route_fingerprint,
)
from coquo.providers.manager import (
    CompactionRuntimeSnapshot,
    ContextTransitionRuntimeSnapshot,
    CurrentTargetContextAssessment,
    RuntimeProviderManager,
    RuntimeProviderStateError,
    RuntimeStatus,
    RuntimeSwitchAuditError,
    RuntimeSwitchContextError,
    RuntimeSwitchResult,
    TurnRuntimeSnapshot,
)
from coquo.providers.model_context import (
    ModelContextCapability,
    ModelContextCapabilityResolver,
    ModelContextSource,
    ModelContextTarget,
)
from coquo.providers.profile import (
    LEGACY_PROFILE_NAMESPACE,
    NamedProviderProfile,
    ProviderProfileError,
    ProviderProfileSpec,
    legacy_profile_id,
    profile_fingerprint,
)
from coquo.providers.profile_store import (
    ActiveProfileSelection,
    ProviderProfileStore,
)
from coquo.providers.request_context import (
    ContextFitDecision,
    ContextFitReport,
    ContextPreflightError,
    ContextPreflightErrorKind,
    RequestTokenCount,
    RequestTokenCountMethod,
    estimate_serialized_input_tokens,
    evaluate_context_fit,
)
from coquo.providers.stability import (
    ProviderSoakReport,
    StreamLatencyClass,
    StreamSample,
    aggregate_soak,
    classify_stream,
)

__all__ = [
    "ADAPTER_CONTRACT_VERSION",
    "ActiveProfileSelection",
    "CompactionRuntimeSnapshot",
    "ContextTransitionRuntimeSnapshot",
    "ContextFitDecision",
    "ContextFitReport",
    "ContextPreflightError",
    "ContextPreflightErrorKind",
    "CurrentTargetContextAssessment",
    "LEGACY_PROFILE_NAMESPACE",
    "ModelContextCapability",
    "ModelContextCapabilityResolver",
    "ModelContextSource",
    "ModelContextTarget",
    "NamedProviderProfile",
    "ProviderProfileError",
    "ProviderProfileSpec",
    "ProviderProfileStore",
    "RequestTokenCount",
    "RequestTokenCountMethod",
    "RuntimeProviderManager",
    "RuntimeProviderStateError",
    "RuntimeStatus",
    "RuntimeSwitchAuditError",
    "RuntimeSwitchContextError",
    "RuntimeSwitchResult",
    "TurnRuntimeSnapshot",
    "estimate_serialized_input_tokens",
    "evaluate_context_fit",
    "legacy_profile_id",
    "profile_fingerprint",
    "route_fingerprint",
    "ProviderSoakReport",
    "StreamLatencyClass",
    "StreamSample",
    "aggregate_soak",
    "classify_stream",
]
