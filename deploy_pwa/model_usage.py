"""Capture MiniMax response usage without guessing tokens or invoice cost."""

from __future__ import annotations


_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
    "cache_creation_input_tokens": "cache_creation_input_tokens",
}


def response_usage(payload: dict, requested_model: str) -> dict:
    """Return bounded provider counters, or explicitly mark missing usage.

    These are response counters, not confirmed billed units. The MiniMax
    credit ledger is separate and must be reconciled independently.
    """
    raw = payload.get("usage") if isinstance(payload, dict) else None
    counters = {}
    if isinstance(raw, dict):
        for field, destination in _FIELDS.items():
            value = raw.get(field)
            if type(value) is int and 0 <= value <= 10**12:
                counters[destination] = value
    model = payload.get("model") if isinstance(payload, dict) else None
    if not isinstance(model, str) or not 1 <= len(model) <= 120:
        model = requested_model
    return {"provider": "minimax", "model": model,
            "source_status": "provider_response_reported" if counters else "usage_missing",
            "cost_status": "unknown_no_rate_or_invoice", "counters": counters}
