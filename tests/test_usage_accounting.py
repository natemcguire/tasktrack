"""Accounting outcomes and regressions found during review; no helper/constant tests."""

import unittest
from decimal import Decimal, localcontext
from itertools import permutations

from tasktrack.usage_accounting import (
    MAX_SUPPORTED_TOKENS,
    AllocationSplit,
    CostKind,
    CumulativeTracker,
    PriceVersion,
    SubscriptionCost,
    TokenCounts,
    allocate_integer_units,
    allocate_tokens,
    calculate_token_cost,
    derive_effective_subscription_rate,
)


class PricingTests(unittest.TestCase):
    def test_cache_categories_are_disjoint_and_reasoning_is_not_billed_twice(self):
        tokens = TokenCounts(1000, 200, 100, 50, 30)
        rates = {"input": "2", "cached_input": "1", "cache_write": "3", "output": "4"}
        for read, write, expected, prompt in [
            ("included", "included", 2100, 1000),
            ("separate", "separate", 2700, 1300),
            ("included", "separate", 2300, 1100),
            ("separate", "included", 2500, 1200),
        ]:
            with self.subTest(read=read, write=write):
                self.assertEqual(
                    calculate_token_cost(tokens, rates, read, write).cost_micros,
                    expected,
                )
                self.assertEqual(
                    tokens.normalize(read, write).total_prompt_tokens, prompt
                )
        for read, write in [
            (None, "included"),
            ("included", None),
            ("unknown", "included"),
        ]:
            with self.subTest(read=read, write=write), self.assertRaises(ValueError):
                tokens.normalize(read, write)
        with self.assertRaises(ValueError):
            TokenCounts(10, 6, 5).normalize("included", "included")

    def test_missing_prices_remain_unknown_but_unused_categories_need_no_price(self):
        cost = calculate_token_cost(TokenCounts(100, output_tokens=50), {"input": "2"})
        self.assertIsNone(cost.cost_micros)
        self.assertEqual(cost.missing_categories, ("output",))
        self.assertEqual(calculate_token_cost(TokenCounts(0), {}).cost_micros, 0)
        self.assertEqual(
            calculate_token_cost(TokenCounts(100), {"input": "0"}).cost_micros, 0
        )

    def test_rounding_is_exact_independent_of_decimal_context_and_per_category(self):
        cases = [
            ("0.4" + "9" * 80, "1", 0),
            ("0.5", "1", 1),
            ("0.5" + "0" * 80 + "1", "1", 1),
            ("1", "0.4" + "9" * 80, 0),
            ("0.25", "2", 1),
        ]
        for precision in (1, 28, 200):
            with localcontext() as ctx:
                ctx.prec = precision
                for rate, multiplier, expected in cases:
                    with self.subTest(
                        precision=precision, rate=rate, multiplier=multiplier
                    ):
                        self.assertEqual(
                            calculate_token_cost(
                                TokenCounts(1), {"input": rate}, multiplier=multiplier
                            ).cost_micros,
                            expected,
                        )
                # Each category rounds separately; aggregate rounding would give 1.
                self.assertEqual(
                    calculate_token_cost(
                        TokenCounts(1, output_tokens=1),
                        {"input": "0.4", "output": "0.4"},
                    ).cost_micros,
                    0,
                )

    def test_malformed_counts_and_prices_cannot_produce_a_bill(self):
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
            "reasoning_tokens",
        ):
            for value in (True, -1, 1.5, "1", MAX_SUPPORTED_TOKENS + 1):
                with (
                    self.subTest(field=field, value=value),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    TokenCounts(**{"input_tokens": 0, field: value})
        for rate in (True, 1.5, "-1", "NaN", "Infinity", "invalid"):
            with self.subTest(rate=rate), self.assertRaises((TypeError, ValueError)):
                calculate_token_cost(TokenCounts(1), {"input": rate})
        with self.assertRaises(ValueError):
            TokenCounts(0, output_tokens=1, reasoning_tokens=2)

    def test_conflicting_rate_versions_are_rejected(self):
        base = PriceVersion("provider-a", "model-a", "input", Decimal("2"))
        bad_rates = [
            {"unknown": "1"},
            [base, base],
            {"output": base},
            [base, PriceVersion("provider-b", "model-a", "output", Decimal("3"))],
            [base, PriceVersion("provider-a", "model-b", "output", Decimal("3"))],
            [
                PriceVersion(
                    "provider-a", "model-a", "input", Decimal("2"), currency="EUR"
                )
            ],
        ]
        for rates in bad_rates:
            with self.subTest(rates=rates), self.assertRaises(ValueError):
                calculate_token_cost(TokenCounts(1), rates)

    def test_subscription_spend_is_distinct_from_api_equivalent_price(self):
        args = ("2026-09", "account-a", 20000, "USD", "receipt-a")
        self.assertEqual(SubscriptionCost(*args).kind, CostKind.SUBSCRIPTION_ACTUAL)
        with self.assertRaises(ValueError):
            SubscriptionCost(*args, kind=CostKind.API_EQUIVALENT)
        with localcontext() as ctx:
            ctx.prec = 1
            self.assertEqual(
                derive_effective_subscription_rate("200", 10_000_000), Decimal("20")
            )
        self.assertIsNone(derive_effective_subscription_rate("200", 0))


class AllocationTests(unittest.TestCase):
    def test_conservation_and_ties_do_not_depend_on_input_order(self):
        splits = [
            AllocationSplit("b", 3333),
            AllocationSplit("a", 3333),
            AllocationSplit(None, 3334),
        ]
        for amount in (0, 1, 2, 7, 101, 10**80 + 7):
            results = []
            for order in permutations(splits):
                result = {
                    x.destination_id: x.allocated_units
                    for x in allocate_integer_units(amount, order)
                }
                self.assertEqual(sum(result.values()), amount)
                results.append(result)
            self.assertTrue(all(result == results[0] for result in results))
        tied = allocate_integer_units(
            1, [AllocationSplit("b", 5000), AllocationSplit("a", 5000)]
        )
        self.assertEqual(
            {x.destination_id: x.allocated_units for x in tied}, {"a": 1, "b": 0}
        )

    def test_invalid_destinations_weights_and_amounts_are_rejected(self):
        for destination, weight in [
            ("", 10000),
            (123, 10000),
            ("a", True),
            ("a", -1),
            ("a", 10001),
        ]:
            with (
                self.subTest(destination=destination, weight=weight),
                self.assertRaises((TypeError, ValueError)),
            ):
                AllocationSplit(destination, weight)
        for splits in [
            [],
            [AllocationSplit("a", 9999)],
            [AllocationSplit("a", 5000)] * 2,
            [AllocationSplit(None, 5000)] * 2,
        ]:
            with self.subTest(splits=splits), self.assertRaises(ValueError):
                allocate_integer_units(10, splits)
        for amount in (True, -1):
            with self.assertRaises((TypeError, ValueError)):
                allocate_integer_units(amount, [AllocationSplit("a", 10000)])

    def test_token_allocation_keeps_unassigned_usage_and_all_categories(self):
        rows = allocate_tokens(
            TokenCounts(100, 20, 10, 5),
            [AllocationSplit("task", 5000), AllocationSplit(None, 5000)],
            "included",
            "included",
        )
        self.assertEqual(len(rows), 2)
        for field, expected in [
            ("uncached_input_tokens", 70),
            ("cached_input_tokens", 20),
            ("cache_write_tokens", 10),
            ("output_tokens", 5),
            ("total_tokens", 105),
        ]:
            self.assertEqual(sum(row[field] for row in rows), expected)
        self.assertTrue(
            next(row for row in rows if row["destination_id"] is None)["is_unassigned"]
        )


class CounterTests(unittest.TestCase):
    def test_conflicting_replay_and_failed_validation_leave_state_unchanged(self):
        tracker = CumulativeTracker()
        original = {"input": 100, "output": 10}
        tracker.ingest_snapshot("s", original, "a")
        original["input"] = 999  # caller mutation must not rewrite the receipt
        for counters, epoch in [(original, 0), ({"input": 100, "output": 10}, 1)]:
            with self.assertRaises(ValueError):
                tracker.ingest_snapshot("s", counters, "a", epoch=epoch)
        with self.assertRaises(ValueError):
            tracker.ingest_snapshot("s", {"input": 120, "output": 9}, "b")
        result = tracker.ingest_snapshot("s", {"input": 120, "output": 12}, "b")
        self.assertEqual(result.deltas, {"input": 20, "output": 2})
        self.assertTrue(
            tracker.ingest_snapshot("s", {"input": 100, "output": 10}, "a").is_duplicate
        )

    def test_missing_categories_are_preserved_without_recounting(self):
        tracker = CumulativeTracker()
        tracker.ingest_snapshot("s", {"input": 100}, "a")
        tracker.ingest_snapshot("s", {"output": 10}, "b")
        self.assertEqual(
            tracker.ingest_snapshot("s", {"input": 100, "output": 10}, "c").deltas,
            {"input": 0, "output": 0},
        )

    def test_reset_retry_late_events_and_streams_do_not_double_count(self):
        tracker = CumulativeTracker()
        tracker.ingest_snapshot("s", {"input": 100}, "a")
        self.assertEqual(
            tracker.ingest_snapshot("s", {"input": 10}, "reset", epoch=1).deltas,
            {"input": 10},
        )
        self.assertTrue(
            tracker.ingest_snapshot("s", {"input": 10}, "reset", epoch=1).is_duplicate
        )
        self.assertTrue(tracker.ingest_snapshot("s", {"input": 100}, "a").is_duplicate)
        with self.assertRaises(ValueError):
            tracker.ingest_snapshot("s", {"input": 110}, "late", epoch=0)
        self.assertEqual(
            tracker.ingest_snapshot("s", {"input": 12}, "next", epoch=1).deltas,
            {"input": 2},
        )
        self.assertEqual(
            tracker.ingest_snapshot("other", {"input": 100}, "a").deltas, {"input": 100}
        )
        for event_id in ("", " ", None):
            with self.assertRaises(ValueError):
                tracker.ingest_snapshot("s", {"input": 12}, event_id, epoch=1)
