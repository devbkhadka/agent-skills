"""verified-feature-delivery shared library."""

from .primitives import (
    ClosedKeySetResult,
    check_closed_key_set,
    collect_tokens,
    detect_repeatable_trigger,
    is_reason_meaningful,
    is_vacuous_container,
)

__all__ = [
    "ClosedKeySetResult",
    "check_closed_key_set",
    "collect_tokens",
    "detect_repeatable_trigger",
    "is_reason_meaningful",
    "is_vacuous_container",
]
