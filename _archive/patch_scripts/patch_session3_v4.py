#!/usr/bin/env python3
"""
Session 3 patch v4: fix Bugs B1, B2, C in main.py close-path bookkeeping.

(Bug A in kraken_api.py was already applied successfully by v2.)

v4 strategy: smaller match patterns. Match just the lines we know are
EXACTLY right (the broken ticker.get/log_trade calls themselves), avoiding
surrounding lines that contain comments, em-dashes, or other variability.

Each pattern is just 2-4 lines, all definitely pure ASCII with consistent
whitespace, so matching can't fail on hidden characters.
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
    backup = f"{path}.bak_session3v4_{stamp}"
    shutil.copy(path, backup)
    with open(path, 'w') as f:
        f.write(content)
    print(f"{OK}   {label}: written. Backup at {backup}")
    return True


# ============================================================
# main.py edits - Bugs B1, B2, and C (3 sub-edits)
# All patterns are tight, ASCII-only, and disambiguate B1 from B2
# by including the next 1-2 lines after the get_ticker call.
# ============================================================
main_edits = [
    # ---- BUG B1 ----
    # B1 is uniquely the one whose 'except' is bare ('except:' not 'except Exception as e:').
    # Pattern: the get_ticker call + ticker.get + bare except. Indent is 20 spaces.
    (
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                except:\n"
        "                    exit_price = trade['entry_price']\n",

        "                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                    if ticker_ok:\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    else:\n"
        "                        exit_price = trade['entry_price']\n"
        "                except Exception:\n"
        "                    exit_price = trade['entry_price']\n",

        "Bug B1: unpack get_ticker tuple in 'SL already triggered' branch (bare except)"
    ),

    # ---- BUG B2 ----
    # B2 is uniquely the one whose third line is logger.warning(...).
    (
        "                    ticker = self.kraken.get_ticker(symbol)\n"
        "                    exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    logger.warning(f\"Exit: fill price unavailable for {symbol}, using last price: {exit_price}\")\n",

        "                    ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                    if ticker_ok:\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                        logger.warning(f\"Exit: fill price unavailable for {symbol}, using last price: {exit_price}\")\n"
        "                    else:\n"
        "                        logger.error(f\"Exit: ticker fallback failed: {ticker_err}, using entry price\")\n"
        "                        exit_price = trade['entry_price']\n",

        "Bug B2: unpack get_ticker tuple in general fill-price fallback (logger.warning)"
    ),

    # ---- BUG C.1: REMOVE the broken 9-kwarg log_trade call ----
    # The call is structurally unique: starts with "self.logger.log_trade(" on its own line
    # followed by the kwargs. Match the whole multi-line invocation.
    (
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

        "            # log_trade call removed here - moved to after trade-dict enrichment\n",

        "Bug C.1: remove broken 9-kwarg log_trade call"
    ),

    # ---- BUG C.2: extend existing trade-dict enrichment + insert log_trade(trade) ----
    # Match the existing enrichment block that runs before the alerter dispatch.
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

        "Bug C.2: enrich trade dict + insert log_trade(trade) before alerter dispatch"
    ),
]

# ============================================================
print("Session 3 patch script v4 (main.py only, tight patterns)")
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
print("  2. Confirm datetime import: grep -n 'from datetime\\|import datetime' main.py | head -3")
print("  3. Eyeball: git diff main.py")
print("  4. If all good: git add main.py kraken_api.py && git commit + push")
