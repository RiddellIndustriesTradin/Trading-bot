#!/usr/bin/env python3
"""
Session 3 patch: fix three bugs in close-path bookkeeping.

Bug A: kraken_api.get_ticker() doesn't normalize symbol -> CCXT rejects 'ETHUSD'
Bug B: main.py treats get_ticker()'s tuple return as a dict (two locations)
Bug C: main.py calls TradeLogger.log_trade() with 9 kwargs; it takes one trade dict

Strategy: read each file, apply edits in memory with verification, only write
if all edits succeed. Backup original before writing.
"""
import shutil
import sys
from datetime import datetime

OK = "[OK]"
FAIL = "[FAIL]"


def patch_file(path, edits, label):
    """Apply a list of (old, new, description) edits to file at path."""
    with open(path, 'r') as f:
        content = f.read()
    original = content
    for old, new, desc in edits:
        count = content.count(old)
        if count == 0:
            print(f"{FAIL} {label}: '{desc}' - old text NOT FOUND")
            return False
        if count > 1:
            print(f"{FAIL} {label}: '{desc}' - old text appears {count} times (must be unique)")
            return False
        content = content.replace(old, new)
        print(f"{OK}   {label}: {desc}")
    if content == original:
        print(f"{FAIL} {label}: no change after all edits (sanity check)")
        return False
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak_session3_{stamp}"
    shutil.copy(path, backup)
    with open(path, 'w') as f:
        f.write(content)
    print(f"{OK}   {label}: written. Backup at {backup}")
    return True


# ============================================================
# BUG A: kraken_api.py get_ticker - add _normalize_symbol
# ============================================================
kraken_edits = [
    (
        '    def get_ticker(self, symbol: str) -> Tuple[bool, Dict, str]:\n'
        '        """\n'
        '        Get current ticker.\n'
        '\n'
        '        Args:\n'
        '            symbol: Trading pair\n'
        '\n'
        '        Returns:\n'
        '            (success, ticker_dict, error_msg)\n'
        '        """\n'
        '        try:\n'
        '            ticker = self.exchange.fetch_ticker(symbol)',

        '    def get_ticker(self, symbol: str) -> Tuple[bool, Dict, str]:\n'
        '        """\n'
        '        Get current ticker.\n'
        '\n'
        '        Args:\n'
        '            symbol: Trading pair\n'
        '\n'
        '        Returns:\n'
        '            (success, ticker_dict, error_msg)\n'
        '        """\n'
        '        try:\n'
        '            symbol = self._normalize_symbol(symbol)\n'
        '            ticker = self.exchange.fetch_ticker(symbol)',

        "Bug A: get_ticker normalizes symbol before fetch_ticker"
    ),
]

# ============================================================
# BUG B + C: main.py
# Two ticker fallback unpacks + log_trade call replacement
# ============================================================
main_edits = [
    # BUG B - first ticker fallback (in 'SL already triggered' branch)
    (
        "                # Use last ticker price as exit price\n"
        "                try:\n"
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                except:\n"
        "                    exit_price = trade['entry_price']",

        "                # Use last ticker price as exit price\n"
        "                try:\n"
        "                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                    if ticker_ok:\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    else:\n"
        "                        exit_price = trade['entry_price']\n"
        "                except Exception:\n"
        "                    exit_price = trade['entry_price']",

        "Bug B1: unpack get_ticker tuple in 'SL already triggered' branch"
    ),
    # BUG B - second ticker fallback (general 'fill price unavailable' fallback)
    (
        "            if not exit_price or exit_price == 0:\n"
        "                try:\n"
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    logger.warning(f\"Exit: fill price unavailable for {symbol}, using last price: {exit_price}\")\n"
        "                except Exception as e:\n"
        "                    logger.error(f\"Exit: failed to get fallback price: {e}, using entry price\")\n"
        "                    exit_price = trade['entry_price']",

        "            if not exit_price or exit_price == 0:\n"
        "                try:\n"
        "                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                    if ticker_ok:\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                        logger.warning(f\"Exit: fill price unavailable for {symbol}, using last price: {exit_price}\")\n"
        "                    else:\n"
        "                        logger.error(f\"Exit: ticker fallback failed: {ticker_err}, using entry price\")\n"
        "                        exit_price = trade['entry_price']\n"
        "                except Exception as e:\n"
        "                    logger.error(f\"Exit: failed to get fallback price: {e}, using entry price\")\n"
        "                    exit_price = trade['entry_price']",

        "Bug B2: unpack get_ticker tuple in general fill-price fallback"
    ),
    # BUG C - log_trade call: replace 9-kwarg call with single trade-dict call,
    # and pre-enrich trade dict with all keys TradeLogger requires.
    (
        "            # Log trade\n"
        "            self.logger.log_trade(\n"
        "                symbol=symbol,\n"
        "                side=trade['side'],\n"
        "                entry_price=trade['entry_price'],\n"
        "                exit_price=exit_price,\n"
        "                quantity=trade['quantity'],\n"
        "                pnl_usd=pnl_usd,\n"
        "                pnl_pct=pnl_pct,\n"
        "                exit_type=exit_type,\n"
        "                bars_held=bars_held\n"
        "            )",

        "            # Log trade - enrich dict with TradeLogger required keys, then pass\n"
        "            trade['timestamp'] = datetime.now().isoformat()\n"
        "            trade['exit_price'] = exit_price\n"
        "            trade['exit_type'] = exit_type\n"
        "            trade['p&l_usd'] = pnl_usd\n"
        "            trade['p&l_pct'] = pnl_pct\n"
        "            trade['bars_held'] = bars_held\n"
        "            self.logger.log_trade(trade)",

        "Bug C: log_trade single-dict call with all required keys (uses ampersand form)"
    ),
]

# ============================================================
# Run patches atomically
# ============================================================
print("Session 3 patch script")
print("=" * 60)

print("\n[1/2] Patching kraken_api.py (Bug A)...")
ok_kraken = patch_file("kraken_api.py", kraken_edits, "kraken_api.py")

if not ok_kraken:
    print(f"\n{FAIL} kraken_api.py patch failed - aborting before touching main.py")
    sys.exit(1)

print("\n[2/2] Patching main.py (Bugs B + C)...")
ok_main = patch_file("main.py", main_edits, "main.py")

if not ok_main:
    print(f"\n{FAIL} main.py patch failed - kraken_api.py was already written")
    print("       restore from kraken_api.py.bak_session3_* if needed")
    sys.exit(1)

print("\n" + "=" * 60)
print(f"{OK} ALL PATCHES APPLIED")
print("\nNext steps:")
print("  1. Verify both files parse: python3 -c \"import ast; ast.parse(open('main.py').read()); ast.parse(open('kraken_api.py').read()); print('Both files OK')\"")
print("  2. Check datetime import: grep -n 'from datetime\\|import datetime' main.py | head -3")
print("  3. Eyeball changes: git diff main.py kraken_api.py")
print("  4. If all good: git add + commit + push")
