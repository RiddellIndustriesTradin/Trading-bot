"""
osf_handler.py — OSF Strategy Decision Handler v1.0
Strategy #2 (Options Settlement Flow) — production module

Pure decision module: takes a parsed webhook signal + current bot state,
returns a structured decision telling the bot what to execute (or skip).

DESIGN PRINCIPLES:
  - Pure function: same inputs → same outputs (no hidden state)
  - Bot owns state; handler owns decisions
  - Returns structured OSFDecision dataclass with all info bot needs
  - Single evaluate() entry point handles both entry and exit signals
  - All strategy parameters externalized to OSFConfig

USAGE:
    from osf_handler import OSFHandler, OSFConfig, BotState

    config = OSFConfig()  # Uses spec defaults
    handler = OSFHandler(config)

    state = BotState(
        vc_position_open=False,
        osf_position_open=False,
        account_equity_usd=731.94,
        bot_paused=False,
    )

    decision = handler.evaluate(parsed_signal, state)

    if decision.should_execute:
        # Bot executes via existing kraken_api module
        execute_order(decision)
    else:
        log.info(decision.reason)

DEPENDENCIES:
  - options_data.py (built in previous session)
  - Python 3.9+ standard library

Author: Riddell Industries Trading
Created: 12 May 2026
Spec reference: Proppa_OSF_v1_StrategySpec_2026-05-06.md
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from options_data import (
    OptionsDataClient,
    OptionsDataError,
    APIUnavailableError,
    NoOptionsForDateError,
    SanityBoundsError,
    FridayExpiryData,
    find_next_friday,
)


# ─── LOGGING ─────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)


# ─── EXCEPTIONS ──────────────────────────────────────────────────────────

class OSFHandlerError(Exception):
    """Base exception for handler errors."""
    pass


class MalformedSignalError(OSFHandlerError):
    """Signal payload is missing required fields or has bad types."""
    pass


# ─── CONFIGURATION ───────────────────────────────────────────────────────

@dataclass
class OSFConfig:
    """
    Strategy parameters per spec v1.0.

    All values match the locked Phase 2 decisions. Bot can override
    individual values by passing different OSFConfig instance.
    """
    # Entry condition thresholds (Decision 4, spec v1.0)
    max_pain_threshold_pct: float = 0.02   # X = 2.0% above current price
    oi_threshold_billions: float = 3.0     # Y = $3B BTC open interest

    # Risk parameters (Decision 6, spec v1.0)
    risk_per_trade_pct: float = 0.005      # 0.5% per trade (matches V-C)
    stop_loss_pct: float = 0.03            # 3% below entry

    # Symbol routing
    symbol: str = "BTCUSD"
    strategy_id: str = "OSF_v1"

    # Sanity bounds for signal price validation
    signal_price_max_deviation_pct: float = 0.05  # Pine price within 5% of expected


# ─── INPUT STATE STRUCTURES ──────────────────────────────────────────────

@dataclass
class BotState:
    """
    Current bot state at decision time.
    Bot fetches this from its existing state management and passes in.
    """
    vc_position_open: bool          # V-C strategy currently holding
    osf_position_open: bool         # OSF strategy currently holding
    account_equity_usd: float       # Current account balance for sizing
    bot_paused: bool = False        # Risk manager pause state
    drawdown_active: bool = False   # Drawdown reduction active


@dataclass
class ParsedSignal:
    """
    Webhook signal after signal_parser.py extraction.

    Matches Pine alert JSON structure from proppa_osf_v1.pine:
      {
        "symbol": "BTCUSD",
        "side": "long",
        "signal_type": "OSF_ENTRY_REQUEST" | "OSF_EXIT_TIME",
        "price": 80452.30,
        "timestamp": "2026-05-13T23:00:00Z",
        "strategy": "OSF_v1"
      }
    """
    symbol: str
    side: str
    signal_type: str
    price: float
    timestamp: str
    strategy: str


# ─── OUTPUT DECISION STRUCTURE ───────────────────────────────────────────

@dataclass
class OSFDecision:
    """
    Structured decision returned by handler.

    Bot uses fields to:
      - Execute order (if should_execute=True)
      - Log decision reasoning (always)
      - Send Telegram alert (always)
    """
    should_execute: bool
    action: str                          # "BUY", "SELL", or "SKIP"
    reason: str                          # Human-readable explanation
    signal_type: str                     # "OSF_ENTRY_REQUEST" or "OSF_EXIT_TIME"

    # Execution parameters (populated if should_execute=True)
    symbol: Optional[str] = None
    quantity_btc: Optional[float] = None
    entry_price: Optional[float] = None  # For BUY: Pine ref price
    stop_loss_price: Optional[float] = None

    # Contextual data (for logging / Telegram / debugging)
    context: dict = field(default_factory=dict)

    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        if not self.should_execute:
            return f"OSF DECISION [{self.action}]: {self.reason}"

        # BUY decision — quantity, entry, SL all present
        if self.action == "BUY":
            return (
                f"OSF DECISION [BUY]: "
                f"{self.quantity_btc} {self.symbol} @ ${self.entry_price:,.2f} "
                f"SL=${self.stop_loss_price:,.2f}"
            )

        # SELL decision — quantity tracked by bot, SL not applicable
        if self.action == "SELL":
            ref_price = (
                f" (Pine ref ${self.entry_price:,.2f})"
                if self.entry_price is not None else ""
            )
            return f"OSF DECISION [SELL]: close {self.symbol}{ref_price}"

        # Fallback for any unexpected action value
        return f"OSF DECISION [{self.action}]: {self.reason}"


# ─── POSITION SIZING ─────────────────────────────────────────────────────

def calculate_position_size(
    account_equity_usd: float,
    risk_pct: float,
    entry_price: float,
    sl_pct: float,
) -> float:
    """
    Calculate BTC quantity based on fixed-risk position sizing.

    Risk per trade is a % of equity. SL distance is a % of entry price.
    Quantity = risk_amount / SL_distance_in_dollars.

    Matches V-C sizing logic exactly for operational consistency.
    """
    if account_equity_usd <= 0:
        raise ValueError(f"Invalid account equity: {account_equity_usd}")
    if entry_price <= 0:
        raise ValueError(f"Invalid entry price: {entry_price}")

    risk_amount_usd = account_equity_usd * risk_pct
    sl_distance_per_btc = entry_price * sl_pct

    if sl_distance_per_btc <= 0:
        raise ValueError(f"Invalid SL distance: {sl_distance_per_btc}")

    quantity_btc = risk_amount_usd / sl_distance_per_btc

    # Round to Kraken minimum tick (0.0001 BTC)
    quantity_btc = round(quantity_btc, 4)

    return quantity_btc


# ─── SIGNAL VALIDATION ───────────────────────────────────────────────────

def parse_signal_dict(signal_dict: dict) -> ParsedSignal:
    """
    Validate and extract fields from raw webhook JSON dict.
    Raises MalformedSignalError if any required field is missing/invalid.
    """
    required_fields = ['symbol', 'side', 'signal_type', 'price', 'timestamp', 'strategy']

    for f in required_fields:
        if f not in signal_dict:
            raise MalformedSignalError(f"Missing required field: '{f}'")

    try:
        price = float(signal_dict['price'])
    except (TypeError, ValueError) as e:
        raise MalformedSignalError(f"Invalid price: {signal_dict['price']}") from e

    if price <= 0:
        raise MalformedSignalError(f"Price must be positive: {price}")

    valid_signals = {'OSF_ENTRY_REQUEST', 'OSF_EXIT_TIME'}
    if signal_dict['signal_type'] not in valid_signals:
        raise MalformedSignalError(
            f"Unknown signal_type: {signal_dict['signal_type']}. "
            f"Expected one of {valid_signals}"
        )

    return ParsedSignal(
        symbol=signal_dict['symbol'],
        side=signal_dict['side'],
        signal_type=signal_dict['signal_type'],
        price=price,
        timestamp=signal_dict['timestamp'],
        strategy=signal_dict['strategy'],
    )


# ─── MAIN HANDLER CLASS ──────────────────────────────────────────────────

class OSFHandler:
    """
    Decision module for OSF strategy.

    Receives parsed webhook signal + current bot state.
    Returns structured OSFDecision indicating execute/skip + parameters.

    Bot owns state and execution; handler owns strategy logic.
    """

    def __init__(
        self,
        config: Optional[OSFConfig] = None,
        options_client: Optional[OptionsDataClient] = None,
    ):
        self.config = config or OSFConfig()
        # Allow injection of mocked client for testing
        self.options_client = options_client or OptionsDataClient()

    def evaluate(
        self,
        signal: ParsedSignal,
        state: BotState,
    ) -> OSFDecision:
        """
        Main entry point — route to entry or exit logic based on signal type.

        Args:
            signal: Parsed webhook signal (use parse_signal_dict on raw JSON)
            state: Current bot state (positions, equity, pause status)

        Returns:
            OSFDecision with action and parameters

        Never raises — all error paths return SKIP decisions with clear reasons.
        """
        # Hard prerequisites — apply to both entry and exit signals
        if state.bot_paused:
            return self._skip(
                signal,
                "Bot paused — defensive state active",
            )

        if signal.strategy != self.config.strategy_id:
            return self._skip(
                signal,
                f"Strategy ID mismatch: signal={signal.strategy}, "
                f"expected={self.config.strategy_id}",
            )

        if signal.symbol != self.config.symbol:
            return self._skip(
                signal,
                f"Symbol mismatch: signal={signal.symbol}, "
                f"expected={self.config.symbol}",
            )

        # Route by signal type
        if signal.signal_type == 'OSF_ENTRY_REQUEST':
            return self._evaluate_entry(signal, state)
        elif signal.signal_type == 'OSF_EXIT_TIME':
            return self._evaluate_exit(signal, state)
        else:
            return self._skip(
                signal,
                f"Unknown signal_type: {signal.signal_type}",
            )

    def _evaluate_entry(
        self,
        signal: ParsedSignal,
        state: BotState,
    ) -> OSFDecision:
        """
        Entry signal evaluation. Returns BUY decision if all conditions met.

        Conditions (per spec Decision 3):
          1. V-C not in position (sequential lockout)
          2. OSF not already in position
          3. Max pain >= 2.0% above current price
          4. Open interest >= $3B at upcoming Friday expiry
        """
        # Lockout checks first (cheap, no API call needed)
        if state.vc_position_open:
            return self._skip(
                signal,
                "V-C strategy holding position (sequential lockout)",
            )

        if state.osf_position_open:
            return self._skip(
                signal,
                "OSF strategy already holding position",
            )

        # Drawdown protection
        if state.drawdown_active:
            return self._skip(
                signal,
                "Drawdown reduction state active — no new entries",
            )

        # Query Deribit for max pain + OI
        try:
            next_friday = find_next_friday()
            options_data = self.options_client.get_friday_data(next_friday)
        except APIUnavailableError as e:
            return self._skip(
                signal,
                f"Deribit API unavailable: {e}",
            )
        except NoOptionsForDateError as e:
            return self._skip(
                signal,
                f"No options found for upcoming Friday: {e}",
            )
        except SanityBoundsError as e:
            return self._skip(
                signal,
                f"Options data failed sanity checks: {e}",
            )
        except OptionsDataError as e:
            return self._skip(
                signal,
                f"Options data error: {e}",
            )

        # Build context for logging regardless of outcome
        context = {
            'max_pain_usd': options_data.max_pain_usd,
            'oi_billions': options_data.open_interest_billions,
            'distance_pct': options_data.distance_from_current_pct,
            'underlying_price': options_data.underlying_price,
            'pine_price': signal.price,
            'expiry_date': options_data.expiry_date.isoformat(),
            'instruments_count': options_data.instruments_count,
        }

        # Entry condition checks
        meets_max_pain = (
            options_data.distance_from_current_pct >= self.config.max_pain_threshold_pct
        )
        meets_oi = (
            options_data.open_interest_billions >= self.config.oi_threshold_billions
        )

        if not meets_max_pain or not meets_oi:
            reasons = []
            if not meets_max_pain:
                reasons.append(
                    f"max pain {options_data.distance_from_current_pct*100:+.2f}% "
                    f"below threshold +{self.config.max_pain_threshold_pct*100:.1f}%"
                )
            if not meets_oi:
                reasons.append(
                    f"OI ${options_data.open_interest_billions:.2f}B "
                    f"below threshold ${self.config.oi_threshold_billions:.1f}B"
                )
            return self._skip(
                signal,
                "Entry conditions not met: " + "; ".join(reasons),
                context=context,
            )

        # All conditions met — compute execution parameters
        # Use Pine reference price for sizing (per spec Decision 2 lock)
        # Bot's actual fill price will differ slightly (slippage)
        try:
            quantity = calculate_position_size(
                account_equity_usd=state.account_equity_usd,
                risk_pct=self.config.risk_per_trade_pct,
                entry_price=signal.price,
                sl_pct=self.config.stop_loss_pct,
            )
        except ValueError as e:
            return self._skip(
                signal,
                f"Position sizing failed: {e}",
                context=context,
            )

        if quantity < 0.0001:
            return self._skip(
                signal,
                f"Computed quantity {quantity} below Kraken minimum 0.0001",
                context=context,
            )

        stop_loss_price = signal.price * (1 - self.config.stop_loss_pct)

        reason = (
            f"FIRE: max pain +{options_data.distance_from_current_pct*100:.2f}% "
            f"≥ +{self.config.max_pain_threshold_pct*100:.1f}%, "
            f"OI ${options_data.open_interest_billions:.2f}B "
            f"≥ ${self.config.oi_threshold_billions:.1f}B"
        )

        log.info(reason)

        return OSFDecision(
            should_execute=True,
            action="BUY",
            reason=reason,
            signal_type=signal.signal_type,
            symbol=signal.symbol,
            quantity_btc=quantity,
            entry_price=signal.price,
            stop_loss_price=round(stop_loss_price, 2),
            context=context,
        )

    def _evaluate_exit(
        self,
        signal: ParsedSignal,
        state: BotState,
    ) -> OSFDecision:
        """
        Exit signal evaluation. Returns SELL decision if OSF is in position.

        Per spec Decision 4: Pine fires exit unconditionally, bot filters
        based on whether a position is actually open.
        """
        if not state.osf_position_open:
            return self._skip(
                signal,
                "Exit signal received but no OSF position open — ignoring",
            )

        # Position open — execute exit at market
        reason = "OSF time-based exit (Pine Friday close trigger)"
        log.info(reason)

        return OSFDecision(
            should_execute=True,
            action="SELL",
            reason=reason,
            signal_type=signal.signal_type,
            symbol=signal.symbol,
            entry_price=signal.price,  # Pine ref price for logging
            quantity_btc=None,  # Bot uses its tracked position size
            stop_loss_price=None,  # Not applicable on exit
            context={'pine_price': signal.price},
        )

    def _skip(
        self,
        signal: ParsedSignal,
        reason: str,
        context: Optional[dict] = None,
    ) -> OSFDecision:
        """Build a SKIP decision with clear reason for logging."""
        log.info(f"OSF SKIP: {reason}")
        return OSFDecision(
            should_execute=False,
            action="SKIP",
            reason=reason,
            signal_type=signal.signal_type if signal else "UNKNOWN",
            context=context or {},
        )


# ─── COMMAND LINE TEST HARNESS ───────────────────────────────────────────

if __name__ == "__main__":
    """
    Run this file directly to test handler logic.
    Tests cover signal parsing, lockout, entry conditions, sizing, exit logic.
    Uses live Deribit API for entry condition tests.
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )

    print("=" * 70)
    print("OSF_HANDLER.PY — TEST HARNESS")
    print("=" * 70)
    print()

    handler = OSFHandler()

    # Helper to build test signals
    def make_signal(signal_type="OSF_ENTRY_REQUEST", price=82000.0):
        return ParsedSignal(
            symbol="BTCUSD",
            side="long",
            signal_type=signal_type,
            price=price,
            timestamp="2026-05-13T23:00:00Z",
            strategy="OSF_v1",
        )

    # Test 1: Entry signal blocked by V-C lockout
    print("[Test 1] Entry signal — V-C in position (should SKIP)")
    state = BotState(
        vc_position_open=True,
        osf_position_open=False,
        account_equity_usd=731.94,
    )
    decision = handler.evaluate(make_signal(), state)
    print(f"  Result: {decision}")
    assert not decision.should_execute
    assert "V-C strategy holding" in decision.reason
    print("  ✓ Correctly skipped due to lockout")
    print()

    # Test 2: Entry signal blocked by OSF already in position
    print("[Test 2] Entry signal — OSF already in position (should SKIP)")
    state = BotState(
        vc_position_open=False,
        osf_position_open=True,
        account_equity_usd=731.94,
    )
    decision = handler.evaluate(make_signal(), state)
    print(f"  Result: {decision}")
    assert not decision.should_execute
    print("  ✓ Correctly skipped due to existing OSF position")
    print()

    # Test 3: Entry signal — paused bot
    print("[Test 3] Entry signal — bot paused (should SKIP)")
    state = BotState(
        vc_position_open=False,
        osf_position_open=False,
        account_equity_usd=731.94,
        bot_paused=True,
    )
    decision = handler.evaluate(make_signal(), state)
    print(f"  Result: {decision}")
    assert not decision.should_execute
    assert "paused" in decision.reason.lower()
    print("  ✓ Correctly skipped due to bot paused")
    print()

    # Test 4: Exit signal — no position open
    print("[Test 4] Exit signal — no OSF position (should SKIP)")
    state = BotState(
        vc_position_open=False,
        osf_position_open=False,
        account_equity_usd=731.94,
    )
    decision = handler.evaluate(make_signal("OSF_EXIT_TIME"), state)
    print(f"  Result: {decision}")
    assert not decision.should_execute
    print("  ✓ Correctly skipped exit (no position)")
    print()

    # Test 5: Exit signal — position open (should EXECUTE)
    print("[Test 5] Exit signal — OSF position open (should EXECUTE SELL)")
    state = BotState(
        vc_position_open=False,
        osf_position_open=True,
        account_equity_usd=731.94,
    )
    decision = handler.evaluate(make_signal("OSF_EXIT_TIME"), state)
    print(f"  Result: {decision}")
    assert decision.should_execute
    assert decision.action == "SELL"
    print("  ✓ Correctly executes SELL")
    print()

    # Test 6: Entry signal — clear conditions, live API call
    print("[Test 6] Entry signal — clear lockout, hits live API (depends on market)")
    state = BotState(
        vc_position_open=False,
        osf_position_open=False,
        account_equity_usd=731.94,
    )
    decision = handler.evaluate(make_signal(), state)
    print(f"  Result: {decision}")
    print(f"  Reason: {decision.reason}")
    if decision.context:
        print(f"  Max pain: ${decision.context.get('max_pain_usd', 0):,.0f}")
        print(f"  OI: ${decision.context.get('oi_billions', 0):.2f}B")
        print(f"  Distance: {decision.context.get('distance_pct', 0)*100:+.2f}%")
    if decision.should_execute:
        print(f"  Would buy {decision.quantity_btc} BTC at ${decision.entry_price:,.2f}")
        print(f"  SL at ${decision.stop_loss_price:,.2f}")
    print()

    # Test 7: Malformed signal parsing
    print("[Test 7] Malformed signal raises MalformedSignalError")
    try:
        parse_signal_dict({"symbol": "BTCUSD"})  # missing fields
        print("  ✗ Should have raised")
    except MalformedSignalError as e:
        print(f"  ✓ Correctly raised: {str(e)[:80]}")
    print()

    # Test 8: Position sizing
    print("[Test 8] Position sizing matches V-C pattern")
    qty = calculate_position_size(
        account_equity_usd=731.94,
        risk_pct=0.005,
        entry_price=82000,
        sl_pct=0.03,
    )
    print(f"  At $731.94 equity, $82k entry, 0.5% risk, 3% SL:")
    print(f"  Computed quantity: {qty} BTC")
    print(f"  Expected ~0.0015 BTC (similar to V-C trades)")
    assert 0.0010 < qty < 0.0020
    print("  ✓ Sizing within expected range")
    print()

    print("=" * 70)
    print("TEST HARNESS COMPLETE")
    print("=" * 70)
