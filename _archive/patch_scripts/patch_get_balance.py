#!/usr/bin/env python3
"""
Patch script for kraken_api.py — fix get_balance() return shape and currency handling.

v2: byte-accurate match — accounts for trailing whitespace on "blank" lines in source.

Bugs fixed:
  A. Shape mismatch: function returned a Dict but main.py unpacks (success, balance, error).
  B. Hardcoded 'USDT' lookup: bot was funded in USD, so lookup returned {} and equity = 0.

USAGE:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_get_balance.py --dry-run
  python3 patch_get_balance.py --apply
"""

import sys
from pathlib import Path

TARGET = Path("kraken_api.py")

# Construct OLD_FUNCTION programmatically so trailing whitespace on blank lines
# is preserved exactly (otherwise editors strip it from string literals).
# Whitespace on each "blank" line is per the file dump:
#   line 62: 8 spaces (inside docstring)
#   line 68: 12 spaces (inside try block)
#   line 71: 12 spaces (inside try block)
#   line 77: 12 spaces (inside try block)
#   line 80: 8 spaces (between try and except)

W8 = " " * 8
W12 = " " * 12

OLD_FUNCTION_LINES = [
    '    def get_balance(self) -> Dict:',
    '        """',
    '        Get account balance.',
    W8,
    '        Returns:',
    '            Dict with available and total balances',
    '        """',
    '        try:',
    '            balance = self.exchange.fetch_balance()',
    W12,
    "            # Extract USDT balance",
    "            usdt_balance = balance.get('USDT', {})",
    W12,
    '            result = {',
    "                'equity': usdt_balance.get('total', 0),",
    "                'available': usdt_balance.get('free', 0),",
    "                'used': usdt_balance.get('used', 0),",
    '            }',
    W12,
    '            logger.debug(f"Balance: ${result[\'equity\']:.2f}")',
    '            return result',
    W8,
    '        except Exception as e:',
    '            logger.error(f"Failed to fetch balance: {str(e)}")',
    '            raise',
]
OLD_FUNCTION = '\n'.join(OLD_FUNCTION_LINES)

NEW_FUNCTION = '''    def get_balance(self) -> Tuple[bool, float, str]:
        """
        Get current account equity.

        Tries USD first (current funding), falls back to USDT (legacy).

        Returns:
            (success, equity, error_msg)
        """
        try:
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
            return False, 0.0, str(e)'''


def patch(content: str):
    results = []

    if 'def get_balance(self) -> Tuple[bool, float, str]' in content:
        results.append(("SKIP", "get_balance already returns Tuple — already patched"))
        return content, results

    if OLD_FUNCTION in content:
        content = content.replace(OLD_FUNCTION, NEW_FUNCTION)
        results.append(("OK", "Replaced get_balance() with Tuple-returning version"))
    else:
        results.append(("FAIL", "Couldn't find original get_balance() block"))
        # Diagnostic: try a partial match to help identify the mismatch
        if 'def get_balance(self) -> Dict:' in content:
            results.append(("INFO", "Function signature found — body bytes mismatch"))

    return content, results


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found in current directory.")
        sys.exit(1)

    mode = None
    if "--apply" in sys.argv:
        mode = "apply"
    elif "--dry-run" in sys.argv:
        mode = "dry-run"
    else:
        print("Usage: python3 patch_get_balance.py [--dry-run | --apply]")
        sys.exit(1)

    original = TARGET.read_text()
    patched, results = patch(original)

    print(f"=== Patch results ({mode}) ===")
    for status, msg in results:
        marker = {"OK": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "INFO": "[INFO]"}[status]
        print(f"  {marker} {msg}")

    n_ok = sum(1 for s, _ in results if s == "OK")
    n_fail = sum(1 for s, _ in results if s == "FAIL")
    print(f"\n{n_ok} fix(es) applied, {n_fail} failure(s).")

    if mode == "dry-run":
        print("\nDRY RUN: file NOT written. Re-run with --apply to write changes.")
        return

    if n_fail > 0:
        print("\nABORTING: at least one fix failed to match. File NOT written.")
        sys.exit(1)

    if patched == original:
        print("\nNo changes needed — file already up to date.")
        return

    TARGET.write_text(patched)
    print(f"\nWrote {TARGET}. Verify with: git diff {TARGET}")


if __name__ == "__main__":
    main()
