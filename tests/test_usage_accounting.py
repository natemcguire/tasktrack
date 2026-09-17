"""Comprehensive regression and unit tests for tasktrack.usage_accounting.

Covers all acceptance criteria and verified defects:
1. CumulativeTracker conflicting event reuse (payload/metadata changes rejected, state preserved).
2. Omitted categories (absent counters preserved, no double counting, failed validation preserves state).
3. Reset replay & epoch ordering (stable source/event identity required, monotonic epochs, late old replay rejected).
4. Cache write inclusion semantics & total_prompt_tokens (exact formulas, ambiguous write rejection).
5. Price validation & ambient Decimal precision (unknown/duplicate/mismatched/mixed/currency validation, isolated precision).
6. Allocation destination IDs, unassigned consistency, stable identity tie-breaking, huge integers.
"""

import decimal
import unittest
from decimal import Decimal

from tasktrack.usage_accounting import (
    CALCULATION_VERSION,
    MAX_SUPPORTED_RATE,
    MAX_SUPPORTED_TOKENS,
    TOTAL_ALLOCATION_WEIGHT_BP,
    AllocatedItem,
    AllocationSplit,
    CacheInclusion,
    CostKind,
    CumulativeTracker,
    NormalizedTokens,
    PricedCost,
    PriceVersion,
    SubscriptionCost,
    TokenCategory,
    TokenCounts,
    allocate_integer_units,
    allocate_tokens,
    calculate_counter_delta,
    calculate_token_cost,
    derive_effective_subscription_rate,
    validate_decimal_rate,
    validate_non_negative_int,
)


class TestValidationHelpers(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(CALCULATION_VERSION, 1)
        self.assertEqual(TOTAL_ALLOCATION_WEIGHT_BP, 10_000)
        self.assertEqual(CacheInclusion.SEPARATE, "separate")
        self.assertEqual(CacheInclusion.INCLUDED, "included")

    def test_validate_non_negative_int_success(self):
        self.assertEqual(validate_non_negative_int(0, "count"), 0)
        self.assertEqual(validate_non_negative_int(42, "count"), 42)

    def test_validate_non_negative_int_rejects_bool(self):
        with self.assertRaises(TypeError):
            validate_non_negative_int(True, "count")
        with self.assertRaises(TypeError):
            validate_non_negative_int(False, "count")

    def test_validate_non_negative_int_rejects_non_int_and_negative(self):
        with self.assertRaises(TypeError):
            validate_non_negative_int(3.14, "count")
        with self.assertRaises(TypeError):
            validate_non_negative_int("42", "count")
        with self.assertRaises(TypeError):
            validate_non_negative_int(None, "count")
        with self.assertRaises(ValueError):
            validate_non_negative_int(-1, "count")

    def test_validate_decimal_rate_success(self):
        self.assertEqual(
            validate_decimal_rate(Decimal("3.00"), "rate"), Decimal("3.00")
        )
        self.assertEqual(validate_decimal_rate("0.15", "rate"), Decimal("0.15"))
        self.assertEqual(validate_decimal_rate(10, "rate"), Decimal("10"))

    def test_validate_decimal_rate_rejects_bool_and_float(self):
        with self.assertRaises(TypeError):
            validate_decimal_rate(True, "rate")
        with self.assertRaises(TypeError):
            validate_decimal_rate(3.14, "rate")

    def test_validate_decimal_rate_rejects_negative_and_non_finite_and_overflow(self):
        with self.assertRaises(ValueError):
            validate_decimal_rate(Decimal("-0.01"), "rate")
        with self.assertRaises(ValueError):
            validate_decimal_rate(Decimal("NaN"), "rate")
        with self.assertRaises(ValueError):
            validate_decimal_rate(Decimal("Infinity"), "rate")
        with self.assertRaises(ValueError):
            validate_decimal_rate("not-a-number", "rate")
        with self.assertRaises(ValueError):
            validate_decimal_rate(MAX_SUPPORTED_RATE + 1, "rate")


class TestTokenCountsAndCacheSemantics(unittest.TestCase):
    def test_valid_token_counts(self):
        tc = TokenCounts(
            input_tokens=1000,
            cached_input_tokens=200,
            cache_write_tokens=100,
            output_tokens=500,
            reasoning_tokens=50,
        )
        self.assertEqual(tc.input_tokens, 1000)
        self.assertEqual(tc.cached_input_tokens, 200)
        self.assertEqual(tc.cache_write_tokens, 100)
        self.assertEqual(tc.output_tokens, 500)
        self.assertEqual(tc.reasoning_tokens, 50)

    def test_token_counts_rejects_bool_negative_overflow(self):
        with self.assertRaises(TypeError):
            TokenCounts(input_tokens=True)
        with self.assertRaises(ValueError):
            TokenCounts(input_tokens=-1)
        # Test all token fields above MAX_SUPPORTED_TOKENS, including both cache fields
        with self.assertRaises(ValueError) as ctx_in:
            TokenCounts(input_tokens=MAX_SUPPORTED_TOKENS + 1)
        self.assertIn(
            "input_tokens exceeds maximum supported tokens", str(ctx_in.exception)
        )

        with self.assertRaises(ValueError) as ctx_read:
            TokenCounts(input_tokens=0, cached_input_tokens=MAX_SUPPORTED_TOKENS + 1)
        self.assertIn(
            "cached_input_tokens exceeds maximum supported tokens",
            str(ctx_read.exception),
        )

        with self.assertRaises(ValueError) as ctx_write:
            TokenCounts(input_tokens=0, cache_write_tokens=MAX_SUPPORTED_TOKENS + 1)
        self.assertIn(
            "cache_write_tokens exceeds maximum supported tokens",
            str(ctx_write.exception),
        )

        with self.assertRaises(ValueError) as ctx_out:
            TokenCounts(input_tokens=0, output_tokens=MAX_SUPPORTED_TOKENS + 1)
        self.assertIn(
            "output_tokens exceeds maximum supported tokens", str(ctx_out.exception)
        )

        with self.assertRaises(ValueError) as ctx_reas:
            TokenCounts(
                input_tokens=0,
                output_tokens=MAX_SUPPORTED_TOKENS,
                reasoning_tokens=MAX_SUPPORTED_TOKENS + 1,
            )
        self.assertIn(
            "reasoning_tokens exceeds maximum supported tokens", str(ctx_reas.exception)
        )

        # Test reasoning_tokens exceeding output_tokens
        with self.assertRaises(ValueError):
            TokenCounts(input_tokens=100, output_tokens=50, reasoning_tokens=51)

        # Test NormalizedTokens enforcement on cache fields above bound
        with self.assertRaises(ValueError):
            NormalizedTokens(
                uncached_input_tokens=0,
                cached_input_tokens=MAX_SUPPORTED_TOKENS + 1,
                cache_write_tokens=0,
                output_tokens=0,
            )
        with self.assertRaises(ValueError):
            NormalizedTokens(
                uncached_input_tokens=0,
                cached_input_tokens=0,
                cache_write_tokens=MAX_SUPPORTED_TOKENS + 1,
                output_tokens=0,
            )

        # Test derive_effective_subscription_rate total_tokens bound
        with self.assertRaises(ValueError):
            derive_effective_subscription_rate("200.00", MAX_SUPPORTED_TOKENS + 1)

    def test_cache_inclusion_separate_and_included_repro(self):
        # Spec repro: input 1000, read 200, write 100
        tc = TokenCounts(
            input_tokens=1000, cached_input_tokens=200, cache_write_tokens=100
        )

        # Both INCLUDED: ordinary input = 1000 - 200 - 100 = 700; prompt tokens = 1000
        norm_inc = tc.normalize(
            cache_read_semantics=CacheInclusion.INCLUDED,
            cache_write_semantics=CacheInclusion.INCLUDED,
        )
        self.assertEqual(norm_inc.uncached_input_tokens, 700)
        self.assertEqual(norm_inc.cached_input_tokens, 200)
        self.assertEqual(norm_inc.cache_write_tokens, 100)
        self.assertEqual(norm_inc.total_prompt_tokens, 1000)

        # Both SEPARATE: ordinary input = 1000; prompt tokens = 1000 + 200 + 100 = 1300
        norm_sep = tc.normalize(
            cache_read_semantics=CacheInclusion.SEPARATE,
            cache_write_semantics=CacheInclusion.SEPARATE,
        )
        self.assertEqual(norm_sep.uncached_input_tokens, 1000)
        self.assertEqual(norm_sep.cached_input_tokens, 200)
        self.assertEqual(norm_sep.cache_write_tokens, 100)
        self.assertEqual(norm_sep.total_prompt_tokens, 1300)

    def test_ambiguous_write_inclusion_rejected(self):
        tc = TokenCounts(
            input_tokens=1000, cached_input_tokens=200, cache_write_tokens=100
        )
        with self.assertRaises(ValueError) as ctx:
            tc.normalize(
                cache_read_semantics=CacheInclusion.INCLUDED, cache_write_semantics=None
            )
        self.assertIn("Ambiguous cache write semantics", str(ctx.exception))

    def test_ambiguous_read_inclusion_rejected(self):
        tc = TokenCounts(
            input_tokens=1000, cached_input_tokens=200, cache_write_tokens=0
        )
        with self.assertRaises(ValueError) as ctx:
            tc.normalize(cache_read_semantics=None)
        self.assertIn("Ambiguous cache read semantics", str(ctx.exception))

    def test_zero_cached_tokens_does_not_require_semantics(self):
        tc = TokenCounts(input_tokens=1000, cached_input_tokens=0, cache_write_tokens=0)
        norm = tc.normalize()
        self.assertEqual(norm.uncached_input_tokens, 1000)
        self.assertEqual(norm.total_prompt_tokens, 1000)

    def test_included_cache_exceeding_input_tokens_rejected(self):
        tc = TokenCounts(
            input_tokens=200, cached_input_tokens=150, cache_write_tokens=100
        )
        with self.assertRaises(ValueError) as ctx:
            tc.normalize(
                cache_read_semantics=CacheInclusion.INCLUDED,
                cache_write_semantics=CacheInclusion.INCLUDED,
            )
        self.assertIn("cannot be less than included cache tokens", str(ctx.exception))


class TestTokenPricingAndValidation(unittest.TestCase):
    def setUp(self):
        # Rates: input 2, cached_input (read) 1, cache_write 3, output 10
        self.rates = {
            TokenCategory.INPUT.value: Decimal("2.00"),
            TokenCategory.CACHED_INPUT.value: Decimal("1.00"),
            TokenCategory.CACHE_WRITE.value: Decimal("3.00"),
            TokenCategory.OUTPUT.value: Decimal("10.00"),
        }

    def test_pricing_repro_included_vs_separate(self):
        # Repro: input1000 including read200 and write100 -> ordinary input 700
        # at rates input2/read1/write3 => 700*2 + 200*1 + 100*3 = 1900 micros
        tokens = TokenCounts(
            input_tokens=1000, cached_input_tokens=200, cache_write_tokens=100
        )
        cost_inc = calculate_token_cost(
            tokens,
            self.rates,
            cache_read_semantics=CacheInclusion.INCLUDED,
            cache_write_semantics=CacheInclusion.INCLUDED,
        )
        self.assertIsInstance(cost_inc, PricedCost)
        self.assertEqual(cost_inc.calculation_version, CALCULATION_VERSION)
        self.assertTrue(cost_inc.is_known)
        self.assertEqual(cost_inc.cost_micros, 1900)
        self.assertEqual(cost_inc.category_micros["input"], 1400)
        self.assertEqual(cost_inc.category_micros["cached_input"], 200)
        self.assertEqual(cost_inc.category_micros["cache_write"], 300)

        # Separate: 1000*2 + 200*1 + 100*3 = 2500 micros
        cost_sep = calculate_token_cost(
            tokens,
            self.rates,
            cache_read_semantics=CacheInclusion.SEPARATE,
            cache_write_semantics=CacheInclusion.SEPARATE,
        )
        self.assertTrue(cost_sep.is_known)
        self.assertEqual(cost_sep.cost_micros, 2500)
        self.assertEqual(cost_sep.category_micros["input"], 2000)

    def test_price_validation_rejects_unknown_category(self):
        bad_rates = {"input": Decimal("2.00"), "invalid_category": Decimal("1.00")}
        tokens = TokenCounts(input_tokens=100)
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, bad_rates)
        self.assertIn("Unknown token category", str(ctx.exception))

    def test_price_validation_rejects_duplicate_category(self):
        pvs = [
            PriceVersion("anthropic", "claude-3-5-sonnet", "input", Decimal("3.00")),
            PriceVersion("anthropic", "claude-3-5-sonnet", "input", Decimal("3.50")),
        ]
        tokens = TokenCounts(input_tokens=100)
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, pvs)
        self.assertIn("Duplicate rate for category", str(ctx.exception))

    def test_price_validation_rejects_mismatched_mapping_key(self):
        mapping = {
            "input": PriceVersion(
                "anthropic", "claude-3-5-sonnet", "output", Decimal("15.00")
            )
        }
        tokens = TokenCounts(input_tokens=100)
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, mapping)
        self.assertIn("does not match PriceVersion category", str(ctx.exception))

    def test_price_validation_rejects_mixed_provider_or_model(self):
        pvs_provider = [
            PriceVersion("anthropic", "claude-3-5-sonnet", "input", Decimal("3.00")),
            PriceVersion("openai", "claude-3-5-sonnet", "output", Decimal("15.00")),
        ]
        tokens = TokenCounts(input_tokens=100, output_tokens=100)
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, pvs_provider)
        self.assertIn("Mixed provider/model versions", str(ctx.exception))

        pvs_model = [
            PriceVersion("anthropic", "claude-3-5-sonnet", "input", Decimal("3.00")),
            PriceVersion("anthropic", "claude-3-haiku", "output", Decimal("1.25")),
        ]
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, pvs_model)
        self.assertIn("Mixed provider/model versions", str(ctx.exception))

    def test_price_validation_rejects_currency_mismatch(self):
        pvs = [
            PriceVersion(
                "anthropic",
                "claude-3-5-sonnet",
                "input",
                Decimal("3.00"),
                currency="EUR",
            )
        ]
        tokens = TokenCounts(input_tokens=100)
        with self.assertRaises(ValueError) as ctx:
            calculate_token_cost(tokens, pvs, currency="USD")
        self.assertIn("Currency mismatch", str(ctx.exception))

    def test_ambient_precision_independence(self):
        tokens = TokenCounts(input_tokens=12345, output_tokens=6789)
        rates = {"input": Decimal("3.14159265"), "output": Decimal("15.98765432")}

        orig_context = decimal.getcontext().copy()
        try:
            # Degrade ambient precision drastically to 2 digits
            decimal.getcontext().prec = 2
            cost = calculate_token_cost(tokens, rates)
            self.assertTrue(cost.is_known)
            # 12345 * 3.14159265 = 38782.96126425 -> 38783 micros
            # 6789 * 15.98765432 = 108540.18517848 -> 108540 micros
            # Total = 147323 micros
            self.assertEqual(cost.category_micros["input"], 38783)
            self.assertEqual(cost.category_micros["output"], 108540)
            self.assertEqual(cost.cost_micros, 147323)

            sub_rate = derive_effective_subscription_rate("200.00", 10_000_000)
            self.assertEqual(sub_rate, Decimal("20.00"))
        finally:
            decimal.setcontext(orig_context)

    def test_missing_rates_returns_unknown(self):
        tokens = TokenCounts(input_tokens=100, output_tokens=50)
        cost = calculate_token_cost(tokens, {"input": Decimal("2.00")})
        self.assertFalse(cost.is_known)
        self.assertIsNone(cost.cost_micros)
        self.assertEqual(cost.missing_categories, ("output",))

    def test_deterministic_rounding_half_up(self):
        rates = {"input": Decimal("0.15")}
        self.assertEqual(
            calculate_token_cost(TokenCounts(input_tokens=1), rates).cost_micros, 0
        )
        self.assertEqual(
            calculate_token_cost(TokenCounts(input_tokens=3), rates).cost_micros, 0
        )
        self.assertEqual(
            calculate_token_cost(TokenCounts(input_tokens=4), rates).cost_micros, 1
        )

        rates_half = {"input": Decimal("0.50")}
        self.assertEqual(
            calculate_token_cost(TokenCounts(input_tokens=1), rates_half).cost_micros, 1
        )
        self.assertEqual(
            calculate_token_cost(TokenCounts(input_tokens=3), rates_half).cost_micros, 2
        )

    def test_high_precision_rounding_below_exact_above_half_micro(self):
        # Repro case from prompt: 0.4 followed by 80 nines is strictly < 0.5, must round half-up to 0
        rate_below = Decimal("0." + "4" + "9" * 80)
        cost_below = calculate_token_cost(
            TokenCounts(input_tokens=1), {"input": rate_below}
        )
        self.assertEqual(cost_below.cost_micros, 0)
        self.assertEqual(cost_below.category_micros["input"], 0)

        # >80 decimal places: below half
        rate_below_85 = Decimal("0." + "4" + "9" * 85)
        cost_below_85 = calculate_token_cost(
            TokenCounts(input_tokens=1), {"input": rate_below_85}
        )
        self.assertEqual(cost_below_85.cost_micros, 0)
        self.assertEqual(cost_below_85.category_micros["input"], 0)

        # Exactly half a micro with >80 decimal places: 0.5000... rounds half-up to 1
        rate_exact_85 = Decimal("0.5" + "0" * 85)
        cost_exact_85 = calculate_token_cost(
            TokenCounts(input_tokens=1), {"input": rate_exact_85}
        )
        self.assertEqual(cost_exact_85.cost_micros, 1)
        self.assertEqual(cost_exact_85.category_micros["input"], 1)

        # Above half a micro with >80 decimal places: 0.500...01 rounds half-up to 1
        rate_above_85 = Decimal("0.5" + "0" * 84 + "1")
        cost_above_85 = calculate_token_cost(
            TokenCounts(input_tokens=1), {"input": rate_above_85}
        )
        self.assertEqual(cost_above_85.cost_micros, 1)
        self.assertEqual(cost_above_85.category_micros["input"], 1)

    def test_high_precision_rounding_varying_ambient_contexts(self):
        rate_below = Decimal("0." + "4" + "9" * 85)
        rate_exact = Decimal("0.5" + "0" * 85)
        rate_above = Decimal("0.5" + "0" * 84 + "1")
        tokens = TokenCounts(input_tokens=1)

        orig_context = decimal.getcontext().copy()
        try:
            for prec in (1, 2, 5, 28, 50, 80, 100, 200):
                decimal.getcontext().prec = prec
                c_below = calculate_token_cost(tokens, {"input": rate_below})
                self.assertEqual(
                    c_below.cost_micros, 0, f"Failed below for prec={prec}"
                )

                c_exact = calculate_token_cost(tokens, {"input": rate_exact})
                self.assertEqual(
                    c_exact.cost_micros, 1, f"Failed exact for prec={prec}"
                )

                c_above = calculate_token_cost(tokens, {"input": rate_above})
                self.assertEqual(
                    c_above.cost_micros, 1, f"Failed above for prec={prec}"
                )
        finally:
            decimal.setcontext(orig_context)

    def test_high_precision_rounding_multiplier_cases(self):
        # Multiplier scaling rate below half: 0.499... * 0.5 < 0.5 -> 0
        rate_below = Decimal("0." + "4" + "9" * 85)
        c1 = calculate_token_cost(
            TokenCounts(1), {"input": rate_below}, multiplier=Decimal("0.5")
        )
        self.assertEqual(c1.cost_micros, 0)

        # Multiplier lifting rate above half: 0.499... * 2 = 0.999... >= 0.5 -> 1
        c2 = calculate_token_cost(
            TokenCounts(1), {"input": rate_below}, multiplier=Decimal("2")
        )
        self.assertEqual(c2.cost_micros, 1)

        # Multiplier itself having >80 decimal places:
        # 0.5 * 0.999...85 = 0.4999... < 0.5 -> 0
        mult_below = Decimal("0." + "9" * 85)
        c3 = calculate_token_cost(
            TokenCounts(1), {"input": Decimal("0.5")}, multiplier=mult_below
        )
        self.assertEqual(c3.cost_micros, 0)

        # 0.5 * 1.000...01 = 0.500...05 > 0.5 -> 1
        mult_above = Decimal("1." + "0" * 84 + "1")
        c4 = calculate_token_cost(
            TokenCounts(1), {"input": Decimal("0.5")}, multiplier=mult_above
        )
        self.assertEqual(c4.cost_micros, 1)

        # 0.5 * 1.000...00 = 0.5 -> 1
        mult_exact = Decimal("1." + "0" * 85)
        c5 = calculate_token_cost(
            TokenCounts(1), {"input": Decimal("0.5")}, multiplier=mult_exact
        )
        self.assertEqual(c5.cost_micros, 1)

    def test_documented_per_category_rounding_preserved(self):
        # 4 categories, each 1 token at 0.499... (>80 digits)
        # If rounded per category: 0 + 0 + 0 + 0 = 0 micros
        # If summed before rounding: 4 * 0.499... = 1.999... -> 2 micros
        rate_below = Decimal("0." + "4" + "9" * 85)
        tokens = TokenCounts(
            input_tokens=1,
            cached_input_tokens=1,
            cache_write_tokens=1,
            output_tokens=1,
        )
        rates = {
            "input": rate_below,
            "cached_input": rate_below,
            "cache_write": rate_below,
            "output": rate_below,
        }
        cost = calculate_token_cost(
            tokens,
            rates,
            cache_read_semantics=CacheInclusion.SEPARATE,
            cache_write_semantics=CacheInclusion.SEPARATE,
        )
        self.assertEqual(cost.cost_micros, 0)
        self.assertEqual(cost.category_micros["input"], 0)
        self.assertEqual(cost.category_micros["cached_input"], 0)
        self.assertEqual(cost.category_micros["cache_write"], 0)
        self.assertEqual(cost.category_micros["output"], 0)

        # Mixed categories: one below half (0.499... -> 0) and one above half (0.500...1 -> 1)
        rate_above = Decimal("0.5" + "0" * 84 + "1")
        rates_mixed = {
            "input": rate_below,
            "cached_input": rate_above,
            "cache_write": Decimal("0"),
            "output": Decimal("0"),
        }
        cost_mixed = calculate_token_cost(
            tokens,
            rates_mixed,
            cache_read_semantics=CacheInclusion.SEPARATE,
            cache_write_semantics=CacheInclusion.SEPARATE,
        )
        self.assertEqual(cost_mixed.cost_micros, 1)
        self.assertEqual(cost_mixed.category_micros["input"], 0)
        self.assertEqual(cost_mixed.category_micros["cached_input"], 1)
        self.assertEqual(cost_mixed.category_micros["cache_write"], 0)
        self.assertEqual(cost_mixed.category_micros["output"], 0)


class TestSubscriptionCost(unittest.TestCase):
    def test_subscription_cost_valid(self):
        sub = SubscriptionCost("2026-09", "acc_1", 20000, "USD", "ev_1")
        self.assertEqual(sub.amount_minor, 20000)
        self.assertEqual(sub.kind, CostKind.SUBSCRIPTION_ACTUAL)

    def test_subscription_cost_rejects_api_equivalent(self):
        with self.assertRaises(ValueError):
            SubscriptionCost(
                "2026-09", "acc_1", 20000, "USD", "ev_1", kind=CostKind.API_EQUIVALENT
            )


class TestAllocation(unittest.TestCase):
    def test_destination_id_validation_and_unassigned_consistency(self):
        # Empty string rejected
        with self.assertRaises(ValueError):
            AllocationSplit("", 5000)
        with self.assertRaises(ValueError):
            AllocationSplit("   ", 5000)

        # Inconsistent is_unassigned=True when destination_id is provided
        with self.assertRaises(ValueError):
            AllocationSplit("task-1", 5000, is_unassigned=True)

        # destination_id=None implies unassigned
        s_un = AllocationSplit(None, 5000)
        self.assertTrue(s_un.is_unassigned)
        self.assertIsNone(s_un.destination_id)

    def test_stable_tie_break_by_destination_identity(self):
        # Two destinations with equal remainder:
        # Permuting the split input order MUST NOT change who gets the residual unit!
        splits_order_1 = [
            AllocationSplit("task-b", 5000),
            AllocationSplit("task-a", 5000),
        ]
        splits_order_2 = [
            AllocationSplit("task-a", 5000),
            AllocationSplit("task-b", 5000),
        ]

        res1 = allocate_integer_units(1, splits_order_1)
        res2 = allocate_integer_units(1, splits_order_2)
        self.assertIsInstance(res1[0], AllocatedItem)

        # "task-a" < "task-b" alphabetically, so "task-a" must win the tie-break in both orders!
        by_dest1 = {r.destination_id: r.allocated_units for r in res1}
        by_dest2 = {r.destination_id: r.allocated_units for r in res2}

        self.assertEqual(by_dest1["task-a"], 1)
        self.assertEqual(by_dest1["task-b"], 0)
        self.assertEqual(by_dest2["task-a"], 1)
        self.assertEqual(by_dest2["task-b"], 0)

    def test_stable_tie_break_with_unassigned(self):
        # Assigned vs Unassigned: assigned (task-a) sorts before unassigned
        s1 = [AllocationSplit(None, 5000), AllocationSplit("task-a", 5000)]
        s2 = [AllocationSplit("task-a", 5000), AllocationSplit(None, 5000)]

        r1 = {
            x.destination_id: x.allocated_units for x in allocate_integer_units(1, s1)
        }
        r2 = {
            x.destination_id: x.allocated_units for x in allocate_integer_units(1, s2)
        }

        self.assertEqual(r1["task-a"], 1)
        self.assertEqual(r1[None], 0)
        self.assertEqual(r2["task-a"], 1)
        self.assertEqual(r2[None], 0)

    def test_conserve_huge_integer_units(self):
        splits = [
            AllocationSplit("t1", 3333),
            AllocationSplit("t2", 3333),
            AllocationSplit("t3", 3334),
        ]
        huge = 10**30 + 1
        res = allocate_integer_units(huge, splits)
        self.assertEqual(sum(r.allocated_units for r in res), huge)

        huge_50 = 10**50
        res_50 = allocate_integer_units(huge_50, splits)
        self.assertEqual(sum(r.allocated_units for r in res_50), huge_50)

    def test_reject_bool_negative_duplicate_destinations_and_bad_weights(self):
        with self.assertRaises(TypeError):
            AllocationSplit("t1", True)
        with self.assertRaises(ValueError):
            AllocationSplit("t1", -10)
        with self.assertRaises(ValueError):
            AllocationSplit("t1", 10001)

        with self.assertRaises(TypeError):
            allocate_integer_units(True, [AllocationSplit("t1", 10000)])
        with self.assertRaises(ValueError):
            allocate_integer_units(-5, [AllocationSplit("t1", 10000)])

        # Duplicate destinations rejected
        with self.assertRaises(ValueError) as ctx:
            allocate_integer_units(
                100, [AllocationSplit("t1", 5000), AllocationSplit("t1", 5000)]
            )
        self.assertIn("Duplicate destination", str(ctx.exception))

        # Duplicate unassigned rejected
        with self.assertRaises(ValueError) as ctx:
            allocate_integer_units(
                100, [AllocationSplit(None, 5000), AllocationSplit(None, 5000)]
            )
        self.assertIn("Duplicate destination", str(ctx.exception))

        # Weight sum != 10,000
        with self.assertRaises(ValueError):
            allocate_integer_units(100, [AllocationSplit("t1", 9999)])

    def test_allocate_tokens_all_categories(self):
        tokens = TokenCounts(
            input_tokens=100,
            cached_input_tokens=50,
            cache_write_tokens=20,
            output_tokens=10,
        )
        splits = [AllocationSplit("t1", 6000), AllocationSplit("t2", 4000)]
        res = allocate_tokens(
            tokens,
            splits,
            cache_read_semantics=CacheInclusion.SEPARATE,
            cache_write_semantics=CacheInclusion.SEPARATE,
        )
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["uncached_input_tokens"], 60)
        self.assertEqual(res[1]["uncached_input_tokens"], 40)
        self.assertEqual(res[0]["total_tokens"] + res[1]["total_tokens"], 180)


class TestCumulativeTracker(unittest.TestCase):
    def test_conflicting_event_reuse_rejected_and_state_preserved(self):
        # Repro 1: ingest s {'input':100} event e1, then same e1 {'input':999}
        # Must reject changed payload, including changed reset metadata, and preserve original state.
        tracker = CumulativeTracker()
        s = "stream-1"

        r1 = tracker.ingest_snapshot(s, {"input": 100}, source_event_id="e1", epoch=0)
        self.assertFalse(r1.is_duplicate)
        self.assertEqual(r1.deltas, {"input": 100})
        self.assertEqual(r1.current_counters, {"input": 100})

        # Changed payload with same event ID: MUST REJECT
        with self.assertRaises(ValueError) as ctx:
            tracker.ingest_snapshot(s, {"input": 999}, source_event_id="e1", epoch=0)
        self.assertIn("Conflicting payload", str(ctx.exception))

        # Changed epoch metadata with same event ID: MUST REJECT
        with self.assertRaises(ValueError) as ctx:
            tracker.ingest_snapshot(s, {"input": 100}, source_event_id="e1", epoch=1)
        self.assertIn("Conflicting payload or metadata", str(ctx.exception))

        # Verify state is completely preserved: identical replay still succeeds
        r1_replay = tracker.ingest_snapshot(
            s, {"input": 100}, source_event_id="e1", epoch=0
        )
        self.assertTrue(r1_replay.is_duplicate)
        self.assertEqual(r1_replay.deltas, {"input": 0})
        self.assertEqual(r1_replay.current_counters, {"input": 100})

        # Next valid event proceeds from 100
        r2 = tracker.ingest_snapshot(s, {"input": 150}, source_event_id="e2", epoch=0)
        self.assertEqual(r2.deltas, {"input": 50})
        self.assertEqual(r2.current_counters, {"input": 150})

    def test_omitted_categories_preserved_no_double_counting(self):
        # Repro 2: ingest {'input':100}, then {'output':10}, then {'input':100,'output':10}
        # Must NOT emit input100 again. Failed validation does not alter state.
        tracker = CumulativeTracker()
        s = "stream-2"

        # Step 1: input 100
        r1 = tracker.ingest_snapshot(s, {"input": 100}, source_event_id="e1")
        self.assertEqual(r1.deltas, {"input": 100})
        self.assertEqual(r1.current_counters, {"input": 100})

        # Step 2: output 10 (input omitted)
        r2 = tracker.ingest_snapshot(s, {"output": 10}, source_event_id="e2")
        self.assertEqual(r2.deltas, {"output": 10})
        self.assertEqual(r2.current_counters, {"input": 100, "output": 10})

        # Step 3: input 100, output 10
        r3 = tracker.ingest_snapshot(
            s, {"input": 100, "output": 10}, source_event_id="e3"
        )
        self.assertEqual(r3.deltas, {"input": 0, "output": 0})
        self.assertEqual(r3.current_counters, {"input": 100, "output": 10})

        # Step 4: failed validation (unexplained decrease on input from 100 to 80)
        with self.assertRaises(ValueError) as ctx:
            tracker.ingest_snapshot(s, {"input": 80}, source_event_id="e4")
        self.assertIn("Unexplained cumulative counter decrease", str(ctx.exception))

        # Verify failed validation did not alter state or register e4
        r5 = tracker.ingest_snapshot(s, {"input": 130}, source_event_id="e5")
        self.assertEqual(r5.deltas, {"input": 30})
        self.assertEqual(r5.current_counters, {"input": 130, "output": 10})

    def test_reset_replay_and_epoch_ordering(self):
        # Repro 3: Monotonically ordered epoch reset, repeated reset, late replay, retries
        tracker = CumulativeTracker()
        s = "stream-3"

        # Epoch 0
        r0 = tracker.ingest_snapshot(s, {"input": 50}, source_event_id="e0", epoch=0)
        self.assertFalse(r0.is_reset)
        self.assertEqual(r0.deltas, {"input": 50})

        # Explicit reset: epoch advances to 1
        r_reset = tracker.ingest_snapshot(
            s, {"input": 10}, source_event_id="reset-1", epoch=1
        )
        self.assertTrue(r_reset.is_reset)
        self.assertEqual(r_reset.epoch, 1)
        self.assertEqual(r_reset.deltas, {"input": 10})

        # Reset replay: identical retry of reset-1 MUST NOT emit 10 again!
        r_reset_retry = tracker.ingest_snapshot(
            s, {"input": 10}, source_event_id="reset-1", epoch=1
        )
        self.assertTrue(r_reset_retry.is_duplicate)
        self.assertEqual(r_reset_retry.deltas, {"input": 0})

        # Reset retry with changed payload: MUST REJECT
        with self.assertRaises(ValueError):
            tracker.ingest_snapshot(
                s, {"input": 20}, source_event_id="reset-1", epoch=1
            )

        # Late old replay from epoch 0 after stream is at epoch 1: MUST REJECT
        with self.assertRaises(ValueError) as ctx:
            tracker.ingest_snapshot(s, {"input": 60}, source_event_id="late-e", epoch=0)
        self.assertIn("Late event with epoch 0 < stream epoch 1", str(ctx.exception))

        # Reset retry to next epoch 2
        r_reset2 = tracker.ingest_snapshot(
            s, {"input": 5}, source_event_id="reset-2", epoch=2
        )
        self.assertTrue(r_reset2.is_reset)
        self.assertEqual(r_reset2.epoch, 2)
        self.assertEqual(r_reset2.deltas, {"input": 5})

    def test_stable_identity_required(self):
        tracker = CumulativeTracker()
        with self.assertRaises(ValueError):
            tracker.ingest_snapshot("", {"input": 10}, source_event_id="e1")
        with self.assertRaises(ValueError):
            tracker.ingest_snapshot("s", {"input": 10}, source_event_id="")


class TestCounterDeltaHelper(unittest.TestCase):
    def test_calculate_counter_delta(self):
        self.assertEqual(calculate_counter_delta(100, 150), 50)
        self.assertEqual(calculate_counter_delta(150, 150), 0)
        self.assertEqual(calculate_counter_delta(150, 40, is_reset=True), 40)
        with self.assertRaises(ValueError):
            calculate_counter_delta(150, 149, is_reset=False)


if __name__ == "__main__":
    unittest.main()
