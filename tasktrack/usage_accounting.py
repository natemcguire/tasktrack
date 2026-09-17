"""Pure token usage and cost accounting core for Tasktrack (F10).

This module provides:
- Validated token pricing using Decimal rates and integer micro-currency.
- Explicit cache read and cache write inclusion semantics.
- Clean separation of subscription spend from API-equivalent cost.
- Conserved integer basis-point allocation with stable tie-breaking.
- Stateful cumulative counter tracking with absent-counter preservation,
  monotonically ordered reset epochs, and strict dedup/conflict detection.

In-memory tracking does not provide durable disk persistence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Context, Decimal, InvalidOperation, localcontext
from enum import Enum
from typing import Any, Mapping, Sequence

CALCULATION_VERSION: int = 1
MICROS_PER_CURRENCY: int = 1_000_000
TOKENS_PER_MILLION: Decimal = Decimal("1000000")
TOTAL_ALLOCATION_WEIGHT_BP: int = 10_000
MAX_SUPPORTED_TOKENS: int = 10**18
MAX_SUPPORTED_RATE: Decimal = Decimal("1000000000000")

# Explicit precision context isolating calculations from ambient context
CALCULATION_CONTEXT: Context = Context(prec=50)


class CacheInclusion(str, Enum):
    """Specifies whether input_tokens includes or excludes a cache category."""

    SEPARATE = "separate"
    INCLUDED = "included"


class CostKind(str, Enum):
    API_EQUIVALENT = "api_equivalent"
    SUBSCRIPTION_ACTUAL = "subscription_actual"


class TokenCategory(str, Enum):
    INPUT = "input"
    CACHED_INPUT = "cached_input"
    CACHE_WRITE = "cache_write"
    OUTPUT = "output"


VALID_TOKEN_CATEGORIES: frozenset[str] = frozenset(c.value for c in TokenCategory)


def validate_non_negative_int(value: Any, field_name: str) -> int:
    """Validate non-negative integer, explicitly rejecting booleans."""
    if type(value) is bool or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative ({value})")
    return value


def validate_decimal_rate(value: Any, field_name: str) -> Decimal:
    """Validate non-negative finite Decimal, rejecting booleans and floats."""
    if type(value) is bool or isinstance(value, float):
        raise TypeError(
            f"{field_name} must be Decimal, str, or int, not {type(value).__name__}"
        )
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, (str, int)):
        try:
            dec = Decimal(str(value).strip())
        except InvalidOperation as err:
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from err
    else:
        raise TypeError(
            f"{field_name} must be Decimal, str, or int, got {type(value).__name__}"
        )

    if not dec.is_finite() or dec < 0:
        raise ValueError(f"{field_name} must be finite and non-negative ({dec})")
    if dec > MAX_SUPPORTED_RATE:
        raise ValueError(
            f"{field_name} exceeds maximum supported rate ({MAX_SUPPORTED_RATE})"
        )
    return dec


def validate_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _resolve_inclusion(value: Any, name: str, count: int) -> CacheInclusion:
    if count > 0 and value is None:
        raise ValueError(
            f"Ambiguous {name}: must be explicitly specified when {count} > 0"
        )
    if value is None:
        return CacheInclusion.SEPARATE
    try:
        return CacheInclusion(value)
    except ValueError as err:
        raise ValueError(f"Invalid {name}: {value!r}") from err


@dataclass(frozen=True)
class TokenCounts:
    input_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        for name, val in (
            ("input_tokens", self.input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("cache_write_tokens", self.cache_write_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
        ):
            validate_non_negative_int(val, name)
            if val > MAX_SUPPORTED_TOKENS:
                raise ValueError(
                    f"{name} exceeds maximum supported tokens ({MAX_SUPPORTED_TOKENS})"
                )

        if self.reasoning_tokens > self.output_tokens:
            raise ValueError(
                f"reasoning_tokens ({self.reasoning_tokens}) cannot exceed output_tokens ({self.output_tokens})"
            )

    def normalize(
        self,
        cache_read_semantics: CacheInclusion | str | None = None,
        cache_write_semantics: CacheInclusion | str | None = None,
    ) -> NormalizedTokens:
        read_sem = _resolve_inclusion(
            cache_read_semantics, "cache read semantics", self.cached_input_tokens
        )
        write_sem = _resolve_inclusion(
            cache_write_semantics, "cache write semantics", self.cache_write_tokens
        )

        deduction = (
            self.cached_input_tokens if read_sem == CacheInclusion.INCLUDED else 0
        ) + (self.cache_write_tokens if write_sem == CacheInclusion.INCLUDED else 0)
        if self.input_tokens < deduction:
            raise ValueError(
                f"input_tokens ({self.input_tokens}) cannot be less than included cache tokens ({deduction})"
            )

        return NormalizedTokens(
            uncached_input_tokens=self.input_tokens - deduction,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
        )


@dataclass(frozen=True)
class NormalizedTokens:
    uncached_input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        for name, val in (
            ("uncached_input_tokens", self.uncached_input_tokens),
            ("cached_input_tokens", self.cached_input_tokens),
            ("cache_write_tokens", self.cache_write_tokens),
            ("output_tokens", self.output_tokens),
            ("reasoning_tokens", self.reasoning_tokens),
        ):
            validate_non_negative_int(val, name)
            if val > MAX_SUPPORTED_TOKENS:
                raise ValueError(
                    f"{name} exceeds maximum supported tokens ({MAX_SUPPORTED_TOKENS})"
                )

        if self.reasoning_tokens > self.output_tokens:
            raise ValueError(
                f"reasoning_tokens ({self.reasoning_tokens}) cannot exceed output_tokens ({self.output_tokens})"
            )

    @property
    def total_prompt_tokens(self) -> int:
        return (
            self.uncached_input_tokens
            + self.cached_input_tokens
            + self.cache_write_tokens
        )

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.output_tokens


@dataclass(frozen=True)
class PriceVersion:
    provider: str
    model: str
    category: str
    per_million_decimal: Decimal
    currency: str = "USD"
    effective_from: str | None = None
    effective_to: str | None = None
    source_url: str | None = None
    verified_at: str | None = None

    def __post_init__(self) -> None:
        validate_non_empty_str(self.provider, "provider")
        validate_non_empty_str(self.model, "model")
        validate_non_empty_str(self.category, "category")
        validate_non_empty_str(self.currency, "currency")
        if self.category not in VALID_TOKEN_CATEGORIES:
            raise ValueError(f"Unknown token category: {self.category!r}")
        dec = validate_decimal_rate(self.per_million_decimal, "per_million_decimal")
        object.__setattr__(self, "per_million_decimal", dec)


@dataclass(frozen=True)
class PricedCost:
    is_known: bool
    cost_micros: int | None
    currency: str
    kind: CostKind = CostKind.API_EQUIVALENT
    category_micros: dict[str, int] = field(default_factory=dict)
    missing_categories: tuple[str, ...] = ()
    calculation_version: int = CALCULATION_VERSION

    def __post_init__(self) -> None:
        if self.is_known:
            if self.cost_micros is None:
                raise ValueError("cost_micros cannot be None when is_known is True")
            validate_non_negative_int(self.cost_micros, "cost_micros")
        elif self.cost_micros is not None:
            raise ValueError("cost_micros must be None when is_known is False")


@dataclass(frozen=True)
class SubscriptionCost:
    period: str
    provider_account_ref: str
    amount_minor: int
    currency: str
    evidence_id: str
    kind: CostKind = CostKind.SUBSCRIPTION_ACTUAL

    def __post_init__(self) -> None:
        validate_non_negative_int(self.amount_minor, "amount_minor")
        validate_non_empty_str(self.period, "period")
        validate_non_empty_str(self.provider_account_ref, "provider_account_ref")
        validate_non_empty_str(self.currency, "currency")
        validate_non_empty_str(self.evidence_id, "evidence_id")
        if self.kind != CostKind.SUBSCRIPTION_ACTUAL:
            raise ValueError(
                "Subscription spend cannot be labeled as API-equivalent actual spend"
            )


def derive_effective_subscription_rate(
    subscription_amount_currency: Decimal | str | int,
    total_tokens: int,
    currency: str = "USD",
) -> Decimal | None:
    validate_non_negative_int(total_tokens, "total_tokens")
    if total_tokens > MAX_SUPPORTED_TOKENS:
        raise ValueError(
            f"total_tokens exceeds maximum supported tokens ({MAX_SUPPORTED_TOKENS})"
        )
    amount = validate_decimal_rate(
        subscription_amount_currency, "subscription_amount_currency"
    )
    if total_tokens == 0:
        return None
    prec = max(50, len(amount.as_tuple().digits) + len(str(total_tokens)) + 20)
    with localcontext(Context(prec=prec)):
        return (amount * TOKENS_PER_MILLION) / Decimal(total_tokens)


def calculate_token_cost(
    tokens: TokenCounts,
    rates: Mapping[str, Decimal | PriceVersion | str | int] | Sequence[PriceVersion],
    cache_read_semantics: CacheInclusion | str | None = None,
    cache_write_semantics: CacheInclusion | str | None = None,
    currency: str = "USD",
    multiplier: Decimal | str | int | None = None,
) -> PricedCost:
    """Calculate API-equivalent cost in integer micro-currency with strict rate validation."""
    normalized = tokens.normalize(
        cache_read_semantics=cache_read_semantics,
        cache_write_semantics=cache_write_semantics,
    )

    items: list[tuple[str, Any]] = []
    if isinstance(rates, Mapping):
        items = list(rates.items())
    elif isinstance(rates, Sequence) and not isinstance(rates, (str, bytes)):
        for pv in rates:
            if not isinstance(pv, PriceVersion):
                raise TypeError(
                    f"Sequence items must be PriceVersion, got {type(pv).__name__}"
                )
            items.append((pv.category, pv))
    else:
        raise TypeError(
            f"rates must be Mapping or Sequence, got {type(rates).__name__}"
        )

    rate_map: dict[str, Decimal] = {}
    seen_provider: str | None = None
    seen_model: str | None = None

    for cat_key, entry in items:
        validate_non_empty_str(cat_key, "rate category key")
        if cat_key not in VALID_TOKEN_CATEGORIES:
            raise ValueError(f"Unknown token category: {cat_key!r}")
        if cat_key in rate_map:
            raise ValueError(f"Duplicate rate for category: {cat_key!r}")

        if isinstance(entry, PriceVersion):
            if entry.category != cat_key:
                raise ValueError(
                    f"Mapping key '{cat_key}' does not match PriceVersion category '{entry.category}'"
                )
            if entry.currency != currency:
                raise ValueError(
                    f"Currency mismatch: expected '{currency}', got '{entry.currency}'"
                )
            if seen_provider is None:
                seen_provider = entry.provider
                seen_model = entry.model
            elif entry.provider != seen_provider or entry.model != seen_model:
                raise ValueError(
                    f"Mixed provider/model versions: expected {seen_provider}/{seen_model}, "
                    f"got {entry.provider}/{entry.model}"
                )
            rate_map[cat_key] = entry.per_million_decimal
        else:
            rate_map[cat_key] = validate_decimal_rate(entry, f"rate for '{cat_key}'")

    mult = (
        validate_decimal_rate(multiplier, "multiplier")
        if multiplier is not None
        else Decimal("1")
    )

    category_counts = [
        (TokenCategory.INPUT.value, normalized.uncached_input_tokens),
        (TokenCategory.CACHED_INPUT.value, normalized.cached_input_tokens),
        (TokenCategory.CACHE_WRITE.value, normalized.cache_write_tokens),
        (TokenCategory.OUTPUT.value, normalized.output_tokens),
    ]

    missing = [c for c, count in category_counts if count > 0 and c not in rate_map]
    if missing:
        return PricedCost(
            is_known=False,
            cost_micros=None,
            currency=currency,
            kind=CostKind.API_EQUIVALENT,
            category_micros={},
            missing_categories=tuple(sorted(missing)),
            calculation_version=CALCULATION_VERSION,
        )

    category_micros: dict[str, int] = {}
    total_micros = 0
    mult_num, mult_den = mult.as_integer_ratio()
    for cat, count in category_counts:
        if count == 0:
            category_micros[cat] = 0
            continue
        rate_num, rate_den = rate_map[cat].as_integer_ratio()
        num = count * rate_num * mult_num
        den = rate_den * mult_den
        quotient, remainder = divmod(num, den)
        cat_int_micros = quotient + 1 if 2 * remainder >= den else quotient
        category_micros[cat] = cat_int_micros
        total_micros += cat_int_micros

    return PricedCost(
        is_known=True,
        cost_micros=total_micros,
        currency=currency,
        kind=CostKind.API_EQUIVALENT,
        category_micros=category_micros,
        missing_categories=(),
        calculation_version=CALCULATION_VERSION,
    )


# ---------------------------------------------------------------------------
# Basis-Point Integer Allocations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocationSplit:
    destination_id: str | None
    weight_bp: int
    is_unassigned: bool = False

    def __post_init__(self) -> None:
        validate_non_negative_int(self.weight_bp, "weight_bp")
        if self.weight_bp > TOTAL_ALLOCATION_WEIGHT_BP:
            raise ValueError(
                f"weight_bp ({self.weight_bp}) cannot exceed {TOTAL_ALLOCATION_WEIGHT_BP} basis points"
            )
        if self.destination_id is not None:
            validate_non_empty_str(self.destination_id, "destination_id")

        if type(self.is_unassigned) is not bool:
            raise TypeError("is_unassigned must be a boolean")
        if self.destination_id is not None and self.is_unassigned:
            raise ValueError(
                f"Inconsistent is_unassigned=True for destination_id={self.destination_id!r}"
            )
        if self.destination_id is None and not self.is_unassigned:
            object.__setattr__(self, "is_unassigned", True)


@dataclass(frozen=True)
class AllocatedItem:
    destination_id: str | None
    is_unassigned: bool
    weight_bp: int
    allocated_units: int


def allocate_integer_units(
    total_units: int,
    splits: Sequence[AllocationSplit],
) -> list[AllocatedItem]:
    """Allocate an integer quantity across destinations using integer basis points.

    Conserves 100% of units using Hamilton-Hare Largest Remainder Method,
    tie-breaking deterministically by stable destination identity.
    """
    validate_non_negative_int(total_units, "total_units")
    if not splits:
        raise ValueError("splits cannot be empty")

    seen_destinations: set[str | None] = set()
    total_weight = 0

    for idx, split in enumerate(splits):
        if not isinstance(split, AllocationSplit):
            raise TypeError(
                f"splits[{idx}] must be AllocationSplit, got {type(split).__name__}"
            )
        if split.destination_id in seen_destinations:
            name = (
                "unassigned"
                if split.destination_id is None
                else repr(split.destination_id)
            )
            raise ValueError(f"Duplicate destination rejected: {name}")
        seen_destinations.add(split.destination_id)
        total_weight += split.weight_bp

    if total_weight != TOTAL_ALLOCATION_WEIGHT_BP:
        raise ValueError(
            f"Allocation weights must sum to exactly {TOTAL_ALLOCATION_WEIGHT_BP} bp, got {total_weight}"
        )

    if total_units == 0:
        return [
            AllocatedItem(
                destination_id=s.destination_id,
                is_unassigned=s.is_unassigned,
                weight_bp=s.weight_bp,
                allocated_units=0,
            )
            for s in splits
        ]

    floors: list[int] = []
    # Stable destination identity for tie-breaking: (0, destination_id) for assigned, (1, "") for unassigned
    rem_items: list[tuple[int, tuple[int, str], int]] = []
    sum_floors = 0

    for idx, split in enumerate(splits):
        product = total_units * split.weight_bp
        floor_val = product // TOTAL_ALLOCATION_WEIGHT_BP
        rem = product % TOTAL_ALLOCATION_WEIGHT_BP
        floors.append(floor_val)
        sum_floors += floor_val
        dest_key = (
            (1, "") if split.destination_id is None else (0, split.destination_id)
        )
        rem_items.append((rem, dest_key, idx))

    remainder_units = total_units - sum_floors
    rem_items.sort(key=lambda x: (-x[0], x[1]))

    extra_units = [0] * len(splits)
    for i in range(remainder_units):
        extra_units[rem_items[i][2]] += 1

    results = [
        AllocatedItem(
            destination_id=split.destination_id,
            is_unassigned=split.is_unassigned,
            weight_bp=split.weight_bp,
            allocated_units=floors[idx] + extra_units[idx],
        )
        for idx, split in enumerate(splits)
    ]

    if sum(r.allocated_units for r in results) != total_units:
        raise RuntimeError(
            "Conservation invariant violated: sum of allocated units != total_units"
        )

    return results


def allocate_tokens(
    tokens: TokenCounts,
    splits: Sequence[AllocationSplit],
    cache_read_semantics: CacheInclusion | str | None = None,
    cache_write_semantics: CacheInclusion | str | None = None,
) -> list[dict[str, Any]]:
    """Allocate all token categories of a TokenCounts instance across splits."""
    normalized = tokens.normalize(
        cache_read_semantics=cache_read_semantics,
        cache_write_semantics=cache_write_semantics,
    )

    uncached_alloc = allocate_integer_units(normalized.uncached_input_tokens, splits)
    cached_alloc = allocate_integer_units(normalized.cached_input_tokens, splits)
    write_alloc = allocate_integer_units(normalized.cache_write_tokens, splits)
    output_alloc = allocate_integer_units(normalized.output_tokens, splits)

    results: list[dict[str, Any]] = []
    for i, split in enumerate(splits):
        u = uncached_alloc[i].allocated_units
        c = cached_alloc[i].allocated_units
        w = write_alloc[i].allocated_units
        o = output_alloc[i].allocated_units
        results.append(
            {
                "destination_id": split.destination_id,
                "is_unassigned": split.is_unassigned,
                "weight_bp": split.weight_bp,
                "uncached_input_tokens": u,
                "cached_input_tokens": c,
                "cache_write_tokens": w,
                "output_tokens": o,
                "total_tokens": u + c + w + o,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Cumulative Counter Deltas and Stream Tracking
# ---------------------------------------------------------------------------


def calculate_counter_delta(previous: int, current: int, is_reset: bool = False) -> int:
    """Calculate a non-negative delta between cumulative counter values."""
    validate_non_negative_int(previous, "previous")
    validate_non_negative_int(current, "current")
    if type(is_reset) is not bool:
        raise TypeError("is_reset must be a boolean")
    if is_reset:
        return current
    if current < previous:
        raise ValueError(
            f"Unexplained cumulative counter decrease from {previous} to {current}"
        )
    return current - previous


@dataclass(frozen=True)
class CumulativeDeltaResult:
    stream_id: str
    deltas: dict[str, int]
    current_counters: dict[str, int]
    is_duplicate: bool
    is_reset: bool
    epoch: int = 0


class CumulativeTracker:
    """In-memory stream tracker for cumulative provider usage counters.

    Invariants & Semantics:
    - Omitted categories: Absent counters in snapshots are preserved from previous readings,
      preventing double-counting when streams report partial category sets.
    - Stable event identity: Replayed event with identical payload/metadata returns zero deltas.
      Conflicting payload or metadata for an existing event ID raises ValueError and leaves
      tracker state untouched.
    - Explicit reset epochs: Resets advance stream epoch (epoch > current_epoch). Reset replays
      return zero deltas. Late replays (epoch < current_epoch) are rejected.
    - Atomic updates: Failed validation leaves tracker state completely unchanged.
    """

    def __init__(self) -> None:
        self._streams: dict[str, dict[str, int]] = {}
        self._epochs: dict[str, int] = {}
        self._seen_events: dict[
            tuple[str, str], tuple[int, tuple[tuple[str, int], ...]]
        ] = {}

    def ingest_snapshot(
        self,
        stream_id: str,
        counters: Mapping[str, int],
        source_event_id: str,
        *,
        epoch: int = 0,
    ) -> CumulativeDeltaResult:
        validate_non_empty_str(stream_id, "stream_id")
        validate_non_empty_str(source_event_id, "source_event_id")
        validate_non_negative_int(epoch, "epoch")

        if not counters:
            raise ValueError("counters cannot be empty")

        validated_counters: dict[str, int] = {}
        for cat, val in counters.items():
            validate_non_empty_str(cat, "counter category")
            validated_counters[cat] = validate_non_negative_int(val, f"counter '{cat}'")

        fingerprint = (epoch, tuple(sorted(validated_counters.items())))
        event_key = (stream_id, source_event_id)

        if event_key in self._seen_events:
            if self._seen_events[event_key] == fingerprint:
                return CumulativeDeltaResult(
                    stream_id=stream_id,
                    deltas={cat: 0 for cat in validated_counters},
                    current_counters=dict(self._streams.get(stream_id, {})),
                    is_duplicate=True,
                    is_reset=False,
                    epoch=epoch,
                )
            raise ValueError(
                f"Conflicting payload or metadata for event '{source_event_id}' on stream '{stream_id}'"
            )

        current_epoch = self._epochs.get(stream_id, 0)
        is_initialized = stream_id in self._streams

        if is_initialized:
            if epoch < current_epoch:
                raise ValueError(
                    f"Late event with epoch {epoch} < stream epoch {current_epoch} for stream '{stream_id}'"
                )
            is_reset = epoch > current_epoch
        else:
            is_reset = epoch > 0

        prev_counters = self._streams.get(stream_id, {})
        new_stream_counters = (
            dict(prev_counters) if (not is_reset and is_initialized) else {}
        )
        deltas: dict[str, int] = {}

        for cat, curr_val in validated_counters.items():
            if is_reset or not is_initialized or cat not in prev_counters:
                delta = curr_val
            else:
                prev_val = prev_counters[cat]
                if curr_val < prev_val:
                    raise ValueError(
                        f"Unexplained cumulative counter decrease for category '{cat}' "
                        f"from {prev_val} to {curr_val} in stream '{stream_id}'"
                    )
                delta = curr_val - prev_val
            deltas[cat] = delta
            new_stream_counters[cat] = curr_val

        # State updates only after full validation
        self._streams[stream_id] = new_stream_counters
        self._epochs[stream_id] = epoch
        self._seen_events[event_key] = fingerprint

        return CumulativeDeltaResult(
            stream_id=stream_id,
            deltas=deltas,
            current_counters=dict(new_stream_counters),
            is_duplicate=False,
            is_reset=is_reset,
            epoch=epoch,
        )
