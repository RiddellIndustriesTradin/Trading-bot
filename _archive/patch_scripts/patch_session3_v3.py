#!/usr/bin/env python3
"""
Session 3 patch v3: fix Bugs B and C in main.py close-path bookkeeping.

(Bug A in kraken_api.py was already applied successfully by v2.)

v3 changes vs v2:
- Bug B1 pattern reduced to avoid an em-dash character in the surrounding line
- Bug C restructured: remove broken log_trade kwarg call entirely, add
  missing keys (timestamp, exit_type) to the EXISTING enrichment block,
  and insert log_trade(trade) call AFTER the enrichment block (before alerter dispatch).
- Atomic apply with verification, backup, ASCII-only patterns.
"""
import shutil
import sys
from datetime import datetime

OK = "[OK]"
FAIL = "[FAIL]"


def patch_file(path, edits, label):
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
    backup = f"{path}.bak_session3v3_{stamp}"
    shutil.copy(path, backup)
    with open(path, 'w') as f:
        f.write(content)
    print(f"{OK}   {label}: written. Backup at {backup}")
    return True


# ============================================================
# main.py edits - Bugs B1, B2, and C (3 sub-edits)
# ============================================================
main_edits = [
    # ---- BUG B1: first ticker fallback (in 'SL already triggered' branch) ----
    # Match only the inner try/except, not the surrounding lines that contain
    # the em-dash. This block is unique enough on its own (16-space indent).
    (
        "                # Use last ticker price as exit price\n"
        "                try:\n"
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                except:\n"
        "                    exit_price = trade['entry_price']\n",

        "                # Use last ticker price as exit price\n"
        "                try:\n"
        "                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                    if ticker_ok:\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    else:\n"
        "                        exit_price = trade['entry_price']\n"
        "                except Exception:\n"
        "                    exit_price = trade['entry_price']\n",

        "Bug B1: unpack get_ticker tuple in 'SL already triggered' branch"
    ),

    # ---- BUG B2: second ticker fallback (general 'fill price unavailable') ----
    (
        "            if not exit_price or exit_price == 0:\n"
        "                try:\n"
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    logger.warning(f\"Exit: fill price unavailable for {symbol}, using last price: {exit_price}\")\n"
        "                except Exception as e:\n"
        "                    logger.error(f\"Exit: failed to get fallback price: {e}, using entry price\")\n"
        "                    exit_price = trade['entry_price']\n",

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
        "                    exit_price = trade['entry_price']\n",

        "Bug B2: unpack get_ticker tuple in general fill-price fallback"
    ),

    # ---- BUG C part 1: REMOVE the broken 9-kwarg log_trade call ----
    # Replace with a comment marker so we know where to put the new call.
    # Matched block includes the leading "# Log trade" comment.
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
        "            )\n",

        "            # Log trade - moved to after trade-dict enrichment (see below)\n",

        "Bug C.1: remove broken 9-kwarg log_trade call"
    ),

    # ---- BUG C part 2: extend the existing enrichment block to include
    # timestamp and exit_type, then call log_trade(trade) before alerter dispatch.
    (
        "            trade['exit_price'] = exit_price\n"
        "            trade['p&l_usd'] = pnl_usd\n"
        "            trade['p&l_pct'] = pnl_pct\n"
        "            trade['bars_held'] = bars_held\n"
        "            if exit_type == 'CLOSE_HARDSTOP':\n",

        "            trade['timestamp'] = datetime.now().isoformat()\n"
        "            trade['exit_price'] = exit_price\n"
        "            trade['exit_type'] = exit_type\n"
        "            trade['p&l_usd'] = pnl_usd\n"
        "            trade['p&l_pct'] = pnl_pct\n"
        "            trade['bars_held'] = bars_held\n"
        "\n"
        "            # Log trade to CSV (now that trade dict has all required keys)\n"
        "            self.logger.log_trade(trade)\n"
        "\n"
        "            if exit_type == 'CLOSE_HARDSTOP':\n",

        "Bug C.2: enrich trade dict with timestamp + exit_type, then call log_trade(trade) before alerter dispatch"
    ),
]

# ============================================================
# Run patch
# ============================================================
print("Session 3 patch script v3 (main.py only)")
print("=" * 60)

print("\nPatching main.py (Bugs B1, B2, C)...")
ok_main = patch_file("main.py", main_edits, "main.py")

if not ok_main:
    print(f"\n{FAIL} main.py patch failed")
    sys.exit(1)

print("\n" + "=" * 60)
print(f"{OK} ALL PATCHES APPLIED")
print("\nNext steps:")
print("  1. Verify: python3 -c \"import ast; ast.parse(open('main.py').read()); print('main.py OK')\"")
print("  2. Confirm datetime import is present:")
print("     grep -n 'from datetime\\|import datetime' main.py | head -3")
print("  3. Eyeball: git diff main.py")
print("  4. If all good: git add main.py kraken_api.py && git commit -m 'Fix close-path bookkeeping: get_ticker normalize, tuple unpack, log_trade signature' && git push")
