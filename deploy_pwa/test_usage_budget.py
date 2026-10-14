import unittest
from decimal import Decimal

from deploy_pwa.usage_budget import RateSnapshot, UsageObservation, account_budget_summary


def rate(measure, amount, version="official-2026-10-14"):
    return RateSnapshot("MiniMax", "MiniMax-M3", measure, "USD",
                        Decimal(amount), version, "2026-10-14",
                        "https://platform.minimax.io/subscribe/token-plan")


def usage(owner, lane, source, **measures):
    return UsageObservation(owner, lane, "llm", "MiniMax", "MiniMax-M3",
                            source, measures, "official-2026-10-14")


class UsageBudgetTests(unittest.TestCase):
    def test_owner_and_lane_separation_and_version(self):
        observations = [usage("account_a", "text", "provider", input_tokens=1_000_000),
                        usage("account_b", "text", "provider", input_tokens=10_000_000),
                        usage("account_a", "voice", "provider", output_tokens=1_000_000)]
        result = account_budget_summary(
            observations, [rate("input_tokens", "0.3"), rate("output_tokens", "1.2")],
            owner_id="account_a", soft_limit_usd=Decimal("1"), coverage_complete=True)
        self.assertEqual(result["lanes"]["text"]["known_estimate_usd"], Decimal("0.3"))
        self.assertEqual(result["lanes"]["voice"]["known_estimate_usd"], Decimal("1.2"))
        self.assertEqual(result["soft_limit_status"], "alert")
        self.assertEqual(result["lanes"]["text"]["price_versions"], ["official-2026-10-14"])
        self.assertEqual(result["lanes"]["text"]["observations"], 1)

    def test_unknown_when_provider_coverage_or_raw_usage_missing(self):
        result = account_budget_summary(
            [usage("account_a", "voice", "livekit_sdk", input_tokens=1000),
             usage("account_a", "text", "missing")], [rate("input_tokens", "0.3")],
            owner_id="account_a", soft_limit_usd=Decimal("1"))
        self.assertEqual(result["soft_limit_status"], "unknown")
        self.assertEqual(result["lanes"]["voice"]["sdk_only"], 1)
        self.assertEqual(result["lanes"]["text"]["missing_usage"], 1)

    def test_no_false_below_without_coverage_proof(self):
        result = account_budget_summary(
            [usage("account_a", "text", "provider", input_tokens=1000)],
            [rate("input_tokens", "0.3")], owner_id="account_a",
            soft_limit_usd=Decimal("1"))
        self.assertEqual(result["soft_limit_status"], "unknown")
        self.assertEqual(result["known_estimate_usd"], Decimal("0.0003"))

    def test_known_subtotal_can_alert_even_when_other_use_is_missing(self):
        result = account_budget_summary(
            [usage("account_a", "text", "provider", input_tokens=1_000_000),
             usage("account_a", "voice", "missing")], [rate("input_tokens", "0.3")],
            owner_id="account_a", soft_limit_usd=Decimal("0.2"))
        self.assertEqual(result["soft_limit_status"], "alert")
        self.assertFalse(result["coverage_complete"])

    def test_unpriced_measure_does_not_invent_cost(self):
        result = account_budget_summary(
            [usage("account_a", "voice", "provider", tts_characters=4000)], [],
            owner_id="account_a", soft_limit_usd=Decimal("1"), coverage_complete=True)
        self.assertEqual(result["lanes"]["voice"]["unpriced"], 1)
        self.assertEqual(result["soft_limit_status"], "unknown")

    def test_rate_version_must_match_observation(self):
        result = account_budget_summary(
            [usage("account_a", "text", "provider", input_tokens=1000)],
            [rate("input_tokens", "0.3", version="outdated")],
            owner_id="account_a", soft_limit_usd=Decimal("1"), coverage_complete=True)
        self.assertEqual(result["known_estimate_usd"], Decimal("0"))
        self.assertEqual(result["lanes"]["text"]["unpriced"], 1)
        self.assertEqual(result["soft_limit_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
