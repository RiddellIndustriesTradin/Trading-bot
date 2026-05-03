#!/usr/bin/env python3
"""
Patch script for kraken_api.py — fixes symbol normalization for Kraken SPOT.

Bugs fixed:
  A. place_market_order: ETHUSD wasn't translating to ETH/USD (only handled USDT).
  B. place_stop_loss_order: was using futures suffix ':USDT' on a spot-only bot.
  C. cancel_order: same futures suffix bug.

Adds a single _normalize_symbol() helper and updates the three call sites.

USAGE:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_kraken_api.py --dry-run    # preview only
  python3 patch_kraken_api.py --apply      # actually write the file
"""

import sys
from pathlib import Path

TARGET = Path("kraken_api.py")

HELPER_METHOD = '''class KrakenAPI:
    """Kraken spot exchange wrapper via CCXT."""

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert webhook symbol (ETHUSD, ETHUSDT) to CCXT spot format (ETH/USD, ETH/USDT)."""
        if '/' in symbol:
            return symbol
        for quote in ('USDT', 'USD'):
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}"
        return symbol

'''

OLD_CLASS_START = '''class KrakenAPI:
'''

# NOTE: All three target blocks live at 12-space indent (inside try: blocks within methods).
OLD_MARKET = """            # Standardize symbol format for CCXT
            if '/' not in symbol:
                symbol = symbol.replace('USDT', '/USDT')"""

NEW_MARKET = """            # Normalize symbol to CCXT spot format
            symbol = self._normalize_symbol(symbol)"""

OLD_SL = """            # Format symbol for CCXT (Kraken Futures)
            ccxt_symbol = symbol.replace('USDT', '/USDT:USDT')"""

NEW_SL = """            # Normalize symbol to CCXT spot format (spot-only bot)
            ccxt_symbol = self._normalize_symbol(symbol)"""

OLD_CANCEL = """            # Format symbol for CCXT if needed
            if '/' not in symbol:
                ccxt_symbol = symbol.replace('USDT', '/USDT:USDT')
            else:
                ccxt_symbol = symbol"""

NEW_CANCEL = """            # Normalize symbol to CCXT spot format (spot-only bot)
            ccxt_symbol = self._normalize_symbol(symbol)"""


def patch(content: str):
    """Apply all four fixes. Returns (new_content, results)."""
    results = []

    if '_normalize_symbol' in content:
        results.append(("SKIP", "_normalize_symbol already present"))
    elif OLD_CLASS_START in content:
        content = content.replace(OLD_CLASS_START, HELPER_METHOD, 1)
        results.append(("OK", "Inserted _normalize_symbol helper"))
    else:
        results.append(("FAIL", "Couldn't find 'class KrakenAPI:' line"))

    if OLD_MARKET in content:
        content = content.replace(OLD_MARKET, NEW_MARKET)
        results.append(("OK", "Fixed place_market_order"))
    else:
        results.append(("FAIL", "Couldn't find place_market_order block"))

    if OLD_SL in content:
        content = content.replace(OLD_SL, NEW_SL)
        results.append(("OK", "Fixed place_stop_loss_order (removed futures suffix)"))
    else:
        results.append(("FAIL", "Couldn't find place_stop_loss_order block"))

    if OLD_CANCEL in content:
        content = content.replace(OLD_CANCEL, NEW_CANCEL)
        results.append(("OK", "Fixed cancel_order (removed futures suffix)"))
    else:
        results.append(("FAIL", "Couldn't find cancel_order block"))

    return content, results


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found in current directory.")
        print(f"Run this script from ~/Desktop/Projects/Proppa_Kraken_Crypto")
        sys.exit(1)

    mode = None
    if "--apply" in sys.argv:
        mode = "apply"
    elif "--dry-run" in sys.argv:
        mode = "dry-run"
    else:
        print("Usage: python3 patch_kraken_api.py [--dry-run | --apply]")
        sys.exit(1)

    original = TARGET.read_text()
    patched, results = patch(original)

    print(f"=== Patch results ({mode}) ===")
    for status, msg in results:
        marker = {"OK": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
        print(f"  {marker} {msg}")

    n_ok = sum(1 for s, _ in results if s == "OK")
    n_fail = sum(1 for s, _ in results if s == "FAIL")
    print(f"\n{n_ok} fix(es) applied, {n_fail} failure(s).")

    if mode == "dry-run":
        print("\nDRY RUN: file NOT written. Re-run with --apply to write changes.")
        return

    if n_fail > 0:
        print("\nABORTING: at least one fix failed to match. File NOT written.")
        print("Investigate the failed blocks before applying.")
        sys.exit(1)

    if patched == original:
        print("\nNo changes needed — file already up to date.")
        return

    TARGET.write_text(patched)
    print(f"\nWrote {TARGET}. Verify with: git diff {TARGET}")


if __name__ == "__main__":
    main()
