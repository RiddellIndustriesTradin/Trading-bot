#!/usr/bin/env python3
"""
patch_session5_retry.py

Session 5 patch #1: Add retry-with-backoff to kraken_api.py for transient API failures.

Problem: Yesterday's first organic SHORT alert failed because the bot's call to
Kraken's /private/Balance endpoint took 7+ seconds and errored out. The bot
tried it ONCE and gave up. No retry. No fallback. Trade missed (well, it was a
SHORT in spot-only mode so no real trade was missed — but the same path is
used for LONG entries).

Fix: wrap the 5 critical Kraken API call sites with a retry helper that
handles transient errors (RequestTimeout, NetworkError, ExchangeNotAvailable)
with exponential backoff. Permanent errors (InsufficientFunds, InvalidOrder,
AuthenticationError) still fail fast — they're not transient.

Backoff schedule: 1s, 2s, 4s. Max 3 attempts. Worst case latency on retry-
success: ~1s. Worst case before giving up: ~7s. Well within Gunicorn's 120s
timeout. Larger than TV's 3-5s timeout — but that's fine because patch 2
(async webhook) addresses TV's complaint separately.

Methods wrapped (return shape unchanged for all):
  - get_balance       -> (success, equity, error)
  - place_market_order -> (success, order_dict, error)
  - place_stop_loss_order -> (success, dict, error)
  - cancel_order      -> (success, error)
  - get_ticker        -> (success, ticker_dict, error)

Methods NOT wrapped (out of hot path or different return shape):
  - get_open_positions (raises instead of returns; not in hot path)
  - get_ohlcv (not in webhook flow)
  - get_leverage / set_leverage (no-op stubs)
  - close_position (legacy; not used by main.py — _handle_exit uses
    place_market_order directly per current code)

Safety:
  - Backs up kraken_api.py to kraken_api.py.bak_session5_retry first
  - Idempotent: detects already-applied state via marker comment
  - Refuses to apply if any expected anchor block is missing
  - Prints unified diff before writing
  - Validates result is parseable Python (ast.parse) before saving

Usage:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_session5_retry.py

Then:
  python3 -c "import ast; ast.parse(open('kraken_api.py').read()); print('syntax OK')"
  git diff kraken_api.py
  # Review carefully — this is the bigger of the two session 5 patches.
  # When ready: git add + commit + push
"""

import sys
import os
import shutil
import difflib
import ast

TARGET = "kraken_api.py"
BACKUP = "kraken_api.py.bak_session5_retry"

# Marker comment to detect already-applied state
ALREADY_APPLIED_MARKER = "# SESSION5_RETRY_APPLIED"

# ---------------------------------------------------------------------------
# Anchor blocks: exact byte sequences we look for in the original file.
# Each anchor is replaced with a new version that wraps the API call in retry.
# Whitespace MUST match the original exactly (4-space indent under method body).
# ---------------------------------------------------------------------------

# 1) Imports — add `time` if not already, add ccxt error class import line.
#    Original file already imports `time` (line 5) and `ccxt` (line 9).
#    No import changes needed.

# 2) Insert the _retry_call helper method right after _normalize_symbol.
#    Anchor: the closing of _normalize_symbol + the duplicate-docstring noise
#    that follows.
INSERT_HELPER_AFTER = '''    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert webhook symbol (ETHUSD, ETHUSDT) to CCXT spot format (ETH/USD, ETH/USDT)."""
        if '/' in symbol:
            return symbol
        for quote in ('USDT', 'USD'):
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}"
        return symbol

    """CCXT-based Kraken Futures trading interface."""
    '''

INSERT_HELPER_NEW = '''    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert webhook symbol (ETHUSD, ETHUSDT) to CCXT spot format (ETH/USD, ETH/USDT)."""
        if '/' in symbol:
            return symbol
        for quote in ('USDT', 'USD'):
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}"
        return symbol

    @staticmethod
    def _retry_call(callable_fn, label: str, max_attempts: int = 3, base_delay: float = 1.0):
        """  # SESSION5_RETRY_APPLIED
        Call `callable_fn` with retry-on-transient-error.

        Retries on: RequestTimeout, NetworkError, ExchangeNotAvailable, DDoSProtection.
        Does NOT retry on: InsufficientFunds, InvalidOrder, AuthenticationError, BadRequest.

        Backoff: base_delay, base_delay*2, base_delay*4 (so 1s, 2s, 4s by default).
        Total worst-case wall time before giving up: sum of sleeps = 7s for default.

        Args:
            callable_fn: zero-arg callable that performs the API call
            label: human-readable label for logging (e.g. "fetch_balance")
            max_attempts: how many tries before giving up (default 3)
            base_delay: seconds to sleep before first retry (default 1.0)

        Returns:
            Whatever `callable_fn` returns on success.

        Raises:
            The last exception if all attempts exhausted, or any non-transient
            exception immediately on first occurrence.
        """
        transient_errors = (
            ccxt.RequestTimeout,
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.DDoSProtection,
        )
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            try:
                return callable_fn()
            except transient_errors as e:
                last_exc = e
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"{label}: transient error on attempt {attempt}/{max_attempts}: "
                        f"{type(e).__name__}: {str(e)[:120]}. Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"{label}: gave up after {max_attempts} attempts. "
                        f"Last error: {type(e).__name__}: {str(e)[:160]}"
                    )
            # Note: non-transient errors propagate out of this function
            # immediately so callers can handle them in their own try/except
            # blocks. Do not catch them here.
        raise last_exc

    """CCXT-based Kraken Futures trading interface."""
    '''

# 3) Wrap get_balance — replace the body's API call.
WRAP_GET_BALANCE_OLD = '''        try:
            balance = self.exchange.fetch_balance()

            # Try USD first (current funding), then USDT (legacy support)
            for quote in ('USD', 'USDT'):
                quote_balance = balance.get(quote, {})
                if quote_balance:
                    equity = float(quote_balance.get('total', 0) or 0)
                    logger.debug(f"Balance: ${equity:.2f} {quote}")
                    return True, equity, ""

            # No matching currency found — return 0 cleanly so caller can decide
            logger.warning("No USD or USDT balance found")
            return True, 0.0, ""

        except Exception as e:
            logger.error(f"Failed to fetch balance: {str(e)}")
            return False, 0.0, str(e)
    '''

WRAP_GET_BALANCE_NEW = '''        try:
            balance = self._retry_call(
                lambda: self.exchange.fetch_balance(),
                label="fetch_balance"
            )

            # Try USD first (current funding), then USDT (legacy support)
            for quote in ('USD', 'USDT'):
                quote_balance = balance.get(quote, {})
                if quote_balance:
                    equity = float(quote_balance.get('total', 0) or 0)
                    logger.debug(f"Balance: ${equity:.2f} {quote}")
                    return True, equity, ""

            # No matching currency found — return 0 cleanly so caller can decide
            logger.warning("No USD or USDT balance found")
            return True, 0.0, ""

        except Exception as e:
            logger.error(f"Failed to fetch balance: {str(e)}")
            return False, 0.0, str(e)
    '''

# 4) Wrap place_market_order
WRAP_PLACE_MARKET_OLD = '''            logger.info(f"Placing {side.upper()} {quantity} {symbol}")
            
            # Place market order
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side.lower(),
                amount=quantity,
            )
            '''

WRAP_PLACE_MARKET_NEW = '''            logger.info(f"Placing {side.upper()} {quantity} {symbol}")
            
            # Place market order (with retry-on-transient)
            order = self._retry_call(
                lambda: self.exchange.create_market_order(
                    symbol=symbol,
                    side=side.lower(),
                    amount=quantity,
                ),
                label=f"create_market_order({side.lower()} {quantity} {symbol})"
            )
            '''

# 5) Wrap place_stop_loss_order
WRAP_PLACE_SL_OLD = '''            order = self.exchange.create_order(
                symbol=ccxt_symbol,
                type='stop-loss',
                side=side,
                amount=quantity,
                price=stop_price,
                params={'trading_agreement': 'agree'}
            )
            '''

WRAP_PLACE_SL_NEW = '''            order = self._retry_call(
                lambda: self.exchange.create_order(
                    symbol=ccxt_symbol,
                    type='stop-loss',
                    side=side,
                    amount=quantity,
                    price=stop_price,
                    params={'trading_agreement': 'agree'}
                ),
                label=f"create_stop_loss_order({side} {quantity} @ {stop_price})"
            )
            '''

# 6) Wrap cancel_order
WRAP_CANCEL_OLD = '''            self.exchange.cancel_order(order_id, ccxt_symbol)
            logger.info(f"✓ Order {order_id} cancelled")
            return True, ""
    '''

WRAP_CANCEL_NEW = '''            self._retry_call(
                lambda: self.exchange.cancel_order(order_id, ccxt_symbol),
                label=f"cancel_order({order_id})"
            )
            logger.info(f"✓ Order {order_id} cancelled")
            return True, ""
    '''

# 7) Wrap get_ticker
WRAP_TICKER_OLD = '''            symbol = self._normalize_symbol(symbol)
            ticker = self.exchange.fetch_ticker(symbol)
            
            result = {
                'symbol': ticker['symbol'],
    '''

WRAP_TICKER_NEW = '''            symbol = self._normalize_symbol(symbol)
            ticker = self._retry_call(
                lambda: self.exchange.fetch_ticker(symbol),
                label=f"fetch_ticker({symbol})"
            )
            
            result = {
                'symbol': ticker['symbol'],
    '''


REPLACEMENTS = [
    ("INSERT_HELPER",     INSERT_HELPER_AFTER, INSERT_HELPER_NEW),
    ("WRAP_GET_BALANCE",  WRAP_GET_BALANCE_OLD, WRAP_GET_BALANCE_NEW),
    ("WRAP_PLACE_MARKET", WRAP_PLACE_MARKET_OLD, WRAP_PLACE_MARKET_NEW),
    ("WRAP_PLACE_SL",     WRAP_PLACE_SL_OLD, WRAP_PLACE_SL_NEW),
    ("WRAP_CANCEL",       WRAP_CANCEL_OLD, WRAP_CANCEL_NEW),
    ("WRAP_TICKER",       WRAP_TICKER_OLD, WRAP_TICKER_NEW),
]


def main():
    if not os.path.exists(TARGET):
        print(f"ERROR: {TARGET} not found in current directory.")
        print(f"  cwd: {os.getcwd()}")
        print(f"  Run this script from the repo root.")
        sys.exit(1)

    with open(TARGET, "r", encoding="utf-8") as f:
        original = f.read()

    # Idempotency check
    if ALREADY_APPLIED_MARKER in original:
        print(f"Already patched. Marker `{ALREADY_APPLIED_MARKER}` found in {TARGET}.")
        print("No changes made.")
        sys.exit(0)

    # Verify each anchor occurs exactly once
    print(f"Checking anchors in {TARGET}...")
    for name, old, _new in REPLACEMENTS:
        count = original.count(old)
        if count != 1:
            print(f"  [{name}] FAIL: expected exactly 1 occurrence, found {count}")
            print(f"  Anchor (first 200 chars):")
            for line in old[:200].splitlines():
                print(f"    {line!r}")
            print()
            print("  This patch was generated against a specific version of kraken_api.py")
            print("  and the file appears to differ. Aborting without changes.")
            sys.exit(2)
        else:
            print(f"  [{name}] OK")

    # Apply all replacements
    patched = original
    for name, old, new in REPLACEMENTS:
        patched = patched.replace(old, new, 1)

    # Verify the result is valid Python
    try:
        ast.parse(patched)
    except SyntaxError as e:
        print(f"ERROR: Patched output is not valid Python: {e}")
        print("  Aborting without writing.")
        sys.exit(3)

    # Backup original
    shutil.copy2(TARGET, BACKUP)
    print(f"\nBackup created: {BACKUP}")

    # Show diff
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"{TARGET} (before)",
        tofile=f"{TARGET} (after)",
        n=2,
    ))
    print("\n--- Diff ---")
    sys.stdout.writelines(diff)
    print("--- End diff ---\n")

    # Write patched file
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(patched)

    print(f"Patched {TARGET} successfully.")
    print()
    print("Lines changed:")
    print(f"  - Added 1 helper method: _retry_call(callable, label, max_attempts, base_delay)")
    print(f"  - Wrapped 5 hot-path API calls: get_balance, place_market_order,")
    print(f"    place_stop_loss_order, cancel_order, get_ticker")
    print()
    print("Verify and commit:")
    print(f"  python3 -c \"import ast; ast.parse(open('{TARGET}').read()); print('syntax OK')\"")
    print(f"  git diff {TARGET}")
    print(f"  git add {TARGET}")
    print(f"  git commit -m \"Add retry-with-backoff to kraken_api.py for transient errors\"")
    print()
    print(f"Rollback: cp {BACKUP} {TARGET}")


if __name__ == "__main__":
    main()
