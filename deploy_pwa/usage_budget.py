"""Offline per-account usage reconciliation and soft-budget decisions.

This module does not call a provider, charge money, or enforce a hard limit.
It deliberately refuses to declare an account under budget when metering
coverage, raw usage, or a dated rate snapshot is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


MEASURES = frozenset({"input_tokens", "output_tokens", "tts_characters"})
SOURCES = frozenset({"provider", "livekit_sdk", "missing"})
LANES = frozenset({"text", "voice"})


@dataclass(frozen=True)
class UsageObservation:
    owner_id: str
    lane: str
    component: str
    provider: str
    model: str
    source: str
    measures: dict[str, int]
    rate_version: str | None = None


@dataclass(frozen=True)
class RateSnapshot:
    provider: str
    model: str
    measure: str
    currency: str
    amount_per_million: Decimal
    version: str
    effective_at: str
    source_url: str


def account_budget_summary(
    observations: list[UsageObservation],
    rates: list[RateSnapshot],
    *,
    owner_id: str,
    soft_limit_usd: Decimal,
    coverage_complete: bool = False,
) -> dict:
    """Summarize one owner without treating SDK metrics as provider invoices.

    ``coverage_complete`` must come from an independently verified provider
    usage export and the text/voice ingestion audit. The default is unknown.
    """
    limit = Decimal(soft_limit_usd)
    if not owner_id or not limit.is_finite() or limit < 0:
        raise ValueError("invalid account or soft limit")
    price_map = {}
    for rate in rates:
        amount = Decimal(rate.amount_per_million)
        if (rate.currency != "USD" or rate.measure not in MEASURES or
                not rate.version or not rate.effective_at or
                not rate.source_url.startswith("https://") or
                not amount.is_finite() or amount < 0):
            raise ValueError("rate requires USD, version, date and valid amount")
        key = (rate.provider, rate.model, rate.measure, rate.version)
        if key in price_map:
            raise ValueError("ambiguous rate snapshots")
        price_map[key] = rate

    lanes = {lane: {"known_estimate_usd": Decimal("0"),
                    "observations": 0, "missing_usage": 0,
                    "unpriced": 0, "sdk_only": 0,
                    "price_versions": set()} for lane in LANES}
    for obs in observations:
        if obs.owner_id != owner_id:
            continue
        if obs.lane not in LANES or obs.source not in SOURCES:
            raise ValueError("invalid observation lane or source")
        row = lanes[obs.lane]
        row["observations"] += 1
        if obs.source == "missing" or not obs.measures:
            row["missing_usage"] += 1
            continue
        if obs.source == "livekit_sdk":
            row["sdk_only"] += 1
        if any(k not in MEASURES or isinstance(v, bool) or
               not isinstance(v, int) or v < 0 for k, v in obs.measures.items()):
            raise ValueError("invalid raw usage measurement")
        priced = Decimal("0")
        missing_rate = False
        versions = set()
        for measure, units in obs.measures.items():
            rate = price_map.get((obs.provider, obs.model, measure, obs.rate_version))
            if rate is None:
                missing_rate = True
                break
            priced += Decimal(units) * Decimal(rate.amount_per_million) / Decimal(1_000_000)
            versions.add(rate.version)
        if missing_rate:
            row["unpriced"] += 1
            continue
        row["known_estimate_usd"] += priced
        row["price_versions"].update(versions)

    total = sum((row["known_estimate_usd"] for row in lanes.values()), Decimal("0"))
    complete = bool(coverage_complete) and all(
        row["missing_usage"] == row["unpriced"] == row["sdk_only"] == 0
        for row in lanes.values()
    )
    # A known subtotal can cross the threshold despite incomplete coverage.
    status = "alert" if total >= limit else "below" if complete else "unknown"
    for row in lanes.values():
        row["price_versions"] = sorted(row["price_versions"])
    return {"owner_id": owner_id, "lanes": lanes,
            "known_estimate_usd": total, "soft_limit_usd": limit,
            "coverage_complete": complete, "soft_limit_status": status,
            "estimate_not_invoice": True}
