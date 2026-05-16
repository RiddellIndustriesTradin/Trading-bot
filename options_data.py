"""
options_data.py — Deribit Options Data Client for OSF v1.0
Strategy #2 (Options Settlement Flow) — production module

Fetches BTC options data from Deribit's free public API and computes
max pain levels for upcoming Friday expiries.

Validated in Phase 3a (5 May 2026):
  - Calculation accuracy: PASS (exact match with Deribit's published values)
  - Cross-expiry consistency: PASS (3/3 weekly/monthly/quarterly matches)
  - API reliability: PARTIAL PASS (14/14 successful calls, 0% failure)

Usage:
    from options_data import OptionsDataClient, OptionsDataError

    client = OptionsDataClient()
    try:
        result = client.get_friday_data()
        max_pain = result.max_pain_usd
        oi_billions = result.open_interest_billions
        underlying = result.underlying_price
    except OptionsDataError as e:
        # Bot logs reason and skips trade
        log.error(f"OSF data unavailable: {e}")

Author: Riddell Industries Trading
Created: 10 May 2026
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests


# ─── LOGGING ─────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)


# ─── CONSTANTS ───────────────────────────────────────────────────────────

DERIBIT_API_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"

# Sanity bounds (per Phase 2 spec)
# If returned data falls outside these, treat as garbage and skip trade
SANITY_MAX_PAIN_DEVIATION_PCT = 0.20  # max pain must be within ±20% of current price
SANITY_OI_MIN_BILLIONS = 0.5           # below $0.5B is suspect
SANITY_OI_MAX_BILLIONS = 50.0          # above $50B is suspect

# Network behaviour
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 2  # 2s, 4s, 8s


# ─── EXCEPTIONS ──────────────────────────────────────────────────────────

class OptionsDataError(Exception):
    """Base exception for all options data failures."""
    pass


class APIUnavailableError(OptionsDataError):
    """Deribit API is not reachable or returning errors."""
    pass


class NoOptionsForDateError(OptionsDataError):
    """No options found for the target expiry date."""
    pass


class SanityBoundsError(OptionsDataError):
    """Returned data failed sanity bounds checks (likely garbage)."""
    pass


# ─── DATA STRUCTURES ─────────────────────────────────────────────────────

@dataclass
class FridayExpiryData:
    """Result of a successful options data query for a Friday expiry."""
    expiry_date: datetime          # The Friday expiry datetime (UTC)
    max_pain_usd: float            # The strike where total payout is minimized
    open_interest_btc: float       # Total open interest in BTC
    open_interest_billions: float  # Total open interest in USD billions
    underlying_price: float        # Current BTC price as Deribit sees it
    distance_from_current_pct: float  # (max_pain - current) / current
    instruments_count: int         # How many options went into the calculation
    fetched_at: datetime           # When this data was retrieved

    @property
    def max_pain_above_current(self) -> bool:
        """True if max pain is above current price (long-only entry condition)."""
        return self.max_pain_usd > self.underlying_price

    def __str__(self) -> str:
        direction = "above" if self.distance_from_current_pct > 0 else "below"
        return (
            f"Friday {self.expiry_date.strftime('%Y-%m-%d')}: "
            f"max_pain=${self.max_pain_usd:,.0f} "
            f"({abs(self.distance_from_current_pct)*100:.2f}% {direction} ${self.underlying_price:,.2f}), "
            f"OI=${self.open_interest_billions:.2f}B "
            f"({self.instruments_count} instruments)"
        )


# ─── INSTRUMENT PARSING ──────────────────────────────────────────────────

def parse_instrument_name(name: str) -> Optional[dict]:
    """
    Parse a Deribit option instrument name into components.

    Format: 'BTC-DDMMMYY-STRIKE-C' or 'BTC-DDMMMYY-STRIKE-P'
    Example: 'BTC-15MAY26-79000-C'

    Returns dict with 'expiry_date', 'strike', 'option_type'
    Returns None if parsing fails.
    """
    parts = name.split('-')
    if len(parts) != 4:
        return None

    _, expiry_str, strike_str, option_type = parts

    if option_type not in ('C', 'P'):
        return None

    try:
        expiry_date = datetime.strptime(expiry_str, "%d%b%y").replace(
            hour=8, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
    except ValueError:
        return None

    try:
        strike = float(strike_str)
    except ValueError:
        return None

    return {
        'expiry_date': expiry_date,
        'strike': strike,
        'option_type': option_type,
    }


def find_next_friday(from_date: Optional[datetime] = None) -> datetime.date:
    """Find the next Friday from the given date (or today if None)."""
    if from_date is None:
        from_date = datetime.now(timezone.utc)

    # Python weekday: Monday=0, ..., Friday=4, Saturday=5, Sunday=6
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        # If today IS Friday, take next Friday (7 days)
        days_until_friday = 7

    return (from_date + timedelta(days=days_until_friday)).date()


# ─── MAX PAIN CALCULATION ────────────────────────────────────────────────

def calculate_max_pain(options: list) -> float:
    """
    Calculate max pain strike from a list of option dicts.

    Each option dict must have 'strike', 'option_type', 'open_interest'.

    Max pain = the strike price P that minimizes total option holder payout:
        For each P: sum of max(0, P - K) * OI for calls
                  + sum of max(0, K - P) * OI for puts
    Returns the P value with minimum total pain.
    """
    if not options:
        raise ValueError("Cannot calculate max pain on empty options list")

    strikes = sorted(set(opt['strike'] for opt in options))

    pain_at_strike = {}
    for candidate_price in strikes:
        total_pain = 0.0
        for opt in options:
            strike = opt['strike']
            oi = opt['open_interest']
            if opt['option_type'] == 'C':
                payout = max(0, candidate_price - strike) * oi
            else:  # 'P'
                payout = max(0, strike - candidate_price) * oi
            total_pain += payout
        pain_at_strike[candidate_price] = total_pain

    return min(pain_at_strike, key=pain_at_strike.get)


# ─── SANITY BOUNDS ───────────────────────────────────────────────────────

def validate_sanity_bounds(
    max_pain_usd: float,
    underlying_price: float,
    oi_billions: float,
) -> None:
    """
    Validate that fetched data is within reasonable bounds.
    Raises SanityBoundsError if any check fails.
    """
    if underlying_price <= 0:
        raise SanityBoundsError(
            f"Underlying price invalid: {underlying_price}"
        )

    deviation_pct = abs(max_pain_usd - underlying_price) / underlying_price
    if deviation_pct > SANITY_MAX_PAIN_DEVIATION_PCT:
        raise SanityBoundsError(
            f"Max pain ${max_pain_usd:,.0f} deviates "
            f"{deviation_pct*100:.1f}% from underlying ${underlying_price:,.2f} "
            f"(threshold: ±{SANITY_MAX_PAIN_DEVIATION_PCT*100:.0f}%)"
        )

    if oi_billions < SANITY_OI_MIN_BILLIONS:
        raise SanityBoundsError(
            f"Open interest ${oi_billions:.2f}B below floor "
            f"${SANITY_OI_MIN_BILLIONS}B"
        )

    if oi_billions > SANITY_OI_MAX_BILLIONS:
        raise SanityBoundsError(
            f"Open interest ${oi_billions:.2f}B above ceiling "
            f"${SANITY_OI_MAX_BILLIONS}B (suspicious)"
        )


# ─── MAIN CLIENT CLASS ───────────────────────────────────────────────────

class OptionsDataClient:
    """
    Client for fetching Deribit BTC options data and computing max pain.

    Production-grade with:
      - Retry logic for transient failures
      - Configurable timeouts
      - Sanity bounds validation
      - Typed exceptions for clean bot integration
      - Standalone (no bot dependencies)
    """

    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_seconds: int = DEFAULT_RETRY_BACKOFF_SECONDS,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds

    def _fetch_options_summary(self) -> list:
        """
        Fetch all active BTC option instruments from Deribit.
        Retries on transient failures with exponential backoff.
        Raises APIUnavailableError after all retries exhausted.
        """
        last_exception = None

        for attempt in range(1, self.max_retries + 1):
            try:
                log.debug(
                    f"Fetching Deribit options (attempt {attempt}/{self.max_retries})"
                )
                response = requests.get(
                    DERIBIT_API_URL,
                    params={"currency": "BTC", "kind": "option"},
                    timeout=self.timeout_seconds,
                )

                if response.status_code != 200:
                    last_exception = APIUnavailableError(
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )
                    log.warning(f"Deribit API returned {response.status_code}")
                else:
                    data = response.json()
                    result = data.get('result', [])
                    if not result:
                        last_exception = APIUnavailableError(
                            "API returned empty result array"
                        )
                        log.warning("Deribit API returned empty result")
                    else:
                        log.info(
                            f"Fetched {len(result)} BTC options from Deribit "
                            f"(attempt {attempt})"
                        )
                        return result

            except requests.Timeout:
                last_exception = APIUnavailableError(
                    f"Request timed out after {self.timeout_seconds}s"
                )
                log.warning(f"Deribit API timeout (attempt {attempt})")
            except requests.RequestException as e:
                last_exception = APIUnavailableError(
                    f"Network error: {str(e)[:200]}"
                )
                log.warning(f"Deribit API network error: {e}")

            # Exponential backoff before retry (except after final attempt)
            if attempt < self.max_retries:
                backoff = self.retry_backoff_seconds * (2 ** (attempt - 1))
                log.debug(f"Retrying in {backoff}s...")
                time.sleep(backoff)

        # All retries exhausted
        raise last_exception or APIUnavailableError("Unknown failure")

    def _filter_to_friday_expiry(
        self,
        instruments: list,
        target_friday_date,
    ) -> list:
        """
        Filter raw API instruments to only those expiring on the target Friday.

        Returns list of dicts with:
            strike, option_type, open_interest, underlying_price
        """
        matching = []
        for inst in instruments:
            parsed = parse_instrument_name(inst['instrument_name'])
            if parsed is None:
                continue
            if parsed['expiry_date'].date() != target_friday_date:
                continue
            matching.append({
                'strike': parsed['strike'],
                'option_type': parsed['option_type'],
                'open_interest': inst.get('open_interest', 0.0),
                'underlying_price': inst.get('underlying_price', 0.0),
            })
        return matching

    def get_friday_data(
        self,
        target_friday_date=None,
    ) -> FridayExpiryData:
        """
        Get max pain and open interest for the upcoming Friday expiry.

        Args:
            target_friday_date: Optional date object for specific Friday.
                                If None, uses next Friday from today.

        Returns:
            FridayExpiryData with all relevant fields populated.

        Raises:
            APIUnavailableError: API failure after retries
            NoOptionsForDateError: No options for target date
            SanityBoundsError: Returned data failed validation
        """
        if target_friday_date is None:
            target_friday_date = find_next_friday()

        log.info(
            f"Querying options data for Friday {target_friday_date.strftime('%Y-%m-%d')}"
        )

        # Fetch (with retries)
        all_instruments = self._fetch_options_summary()

        # Filter to target expiry
        friday_options = self._filter_to_friday_expiry(
            all_instruments, target_friday_date
        )

        if not friday_options:
            raise NoOptionsForDateError(
                f"No BTC options found for Friday {target_friday_date}"
            )

        # Get reference values
        underlying_price = friday_options[0]['underlying_price']
        if underlying_price <= 0:
            raise SanityBoundsError(
                f"Underlying price invalid in API response: {underlying_price}"
            )

        # Calculate max pain
        max_pain_usd = calculate_max_pain(friday_options)

        # Compute aggregates
        total_oi_btc = sum(o['open_interest'] for o in friday_options)
        total_oi_billions = (total_oi_btc * underlying_price) / 1e9

        # Sanity bounds check (raises if invalid)
        validate_sanity_bounds(max_pain_usd, underlying_price, total_oi_billions)

        # Compute distance from current
        distance_pct = (max_pain_usd - underlying_price) / underlying_price

        # Construct expiry datetime (Friday 08:00 UTC settlement)
        expiry_dt = datetime.combine(
            target_friday_date,
            datetime.min.time(),
            tzinfo=timezone.utc
        ).replace(hour=8)

        result = FridayExpiryData(
            expiry_date=expiry_dt,
            max_pain_usd=max_pain_usd,
            open_interest_btc=total_oi_btc,
            open_interest_billions=total_oi_billions,
            underlying_price=underlying_price,
            distance_from_current_pct=distance_pct,
            instruments_count=len(friday_options),
            fetched_at=datetime.now(timezone.utc),
        )

        log.info(f"OSF data computed: {result}")
        return result

    def evaluate_entry_conditions(
        self,
        max_pain_threshold_pct: float = 0.02,
        oi_threshold_billions: float = 3.0,
        target_friday_date=None,
    ) -> tuple:
        """
        Convenience method: fetch Friday data and evaluate against thresholds.

        Returns:
            (would_fire: bool, data: FridayExpiryData, reason: str)

        would_fire is True ONLY if:
            - Max pain >= threshold% above current price
            - Open interest >= threshold $B
            - (Bot still must check sequential lockout separately)
        """
        data = self.get_friday_data(target_friday_date)

        meets_max_pain = data.distance_from_current_pct >= max_pain_threshold_pct
        meets_oi = data.open_interest_billions >= oi_threshold_billions

        if meets_max_pain and meets_oi:
            reason = (
                f"FIRE: max pain {data.distance_from_current_pct*100:+.2f}% "
                f"above price (≥{max_pain_threshold_pct*100:.1f}%), "
                f"OI ${data.open_interest_billions:.2f}B "
                f"(≥${oi_threshold_billions:.1f}B)"
            )
            return True, data, reason

        # Skip — explain why
        reasons = []
        if not meets_max_pain:
            reasons.append(
                f"max pain {data.distance_from_current_pct*100:+.2f}% "
                f"below threshold +{max_pain_threshold_pct*100:.1f}%"
            )
        if not meets_oi:
            reasons.append(
                f"OI ${data.open_interest_billions:.2f}B "
                f"below threshold ${oi_threshold_billions:.1f}B"
            )
        reason = "SKIP: " + "; ".join(reasons)

        return False, data, reason


# ─── COMMAND LINE TEST HARNESS ───────────────────────────────────────────

if __name__ == "__main__":
    """
    Run this file directly to test the module against live Deribit API.

    Usage:
        python3 options_data.py
    """
    # Configure logging to console for testing
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    print("=" * 70)
    print("OPTIONS_DATA.PY — TEST HARNESS")
    print("=" * 70)
    print()

    client = OptionsDataClient()

    # Test 1: Get next Friday data
    print("[Test 1] Fetching upcoming Friday data...")
    try:
        data = client.get_friday_data()
        print(f"  ✓ {data}")
        print(f"  Max pain above current? {data.max_pain_above_current}")
    except OptionsDataError as e:
        print(f"  ✗ FAILED: {type(e).__name__}: {e}")
    print()

    # Test 2: Entry condition evaluation with default thresholds
    print("[Test 2] Evaluating entry conditions (X=2.0%, Y=$3B)...")
    try:
        would_fire, data, reason = client.evaluate_entry_conditions()
        marker = "✓ WOULD FIRE" if would_fire else "✓ would skip"
        print(f"  {marker}")
        print(f"  Reason: {reason}")
    except OptionsDataError as e:
        print(f"  ✗ FAILED: {type(e).__name__}: {e}")
    print()

    # Test 3: Cross-check with monthly expiry (29 May 2026)
    print("[Test 3] Cross-checking 29 May 2026 (monthly expiry)...")
    try:
        from datetime import date
        monthly_data = client.get_friday_data(date(2026, 5, 29))
        print(f"  ✓ {monthly_data}")
    except OptionsDataError as e:
        print(f"  ✗ FAILED: {type(e).__name__}: {e}")
    print()

    print("=" * 70)
    print("TEST HARNESS COMPLETE")
    print("=" * 70)
