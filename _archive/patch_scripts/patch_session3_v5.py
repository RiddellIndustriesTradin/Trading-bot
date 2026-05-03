#!/usr/bin/env python3
"""
Session 3 patch v5: fix Bugs B1, B2, C in main.py close-path bookkeeping.

(Bug A in kraken_api.py was already applied successfully by v2.)

v5 fix: the diagnostic revealed the indentation in main.py is 24sp/20sp,
not 20sp/16sp like I'd been guessing. Patterns now match the actual file.

B1 location (offset ~14560):
    24sp: ticker = self.kraken.get_ticker(symbol)
    24sp: exit_price = ticker.get('last', trade['entry_price'])
    20sp: except:
    24sp: exit_price = trade['entry_price']

B2 location (offset ~15167) needs verification of indents - patches start
at the same get_ticker line which was confirmed to appear twice in file.
For B2, distinguishing line is logger.warning() following the exit_price.
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
    backup = f"{path}.bak_session3v5_{stamp}"
    shutil.copy(path, backup)
    with open(path, 'w') as f:
        f.write(content)
    print(f"{OK}   {label}: written. Backup at {backup}")
    return True


# Indents (verified from byte-level diagnostic):
# B1: try:/get_ticker block sits at 20sp/24sp, except at 20sp
# B2: try:/get_ticker block likely at 16sp/20sp (one level less nested), need to verify
# C.1: log_trade kwarg block - need to verify indent
# C.2: enrichment block - need to verify indent
#
# For B2, C.1, C.2 I'll match smaller, more anchor-y patterns

main_edits = [
    # ---- BUG B1 (24sp content / 20sp except - confirmed by diagnostic) ----
    (
        "                        ticker = self.kraken.get_ticker(symbol)\n"
        "                        exit_price = ticker.get('last', trade['entry_price'])\n"
        "                    except:\n"
        "                        exit_price = trade['entry_price']\n",

        "                        ticker_ok, ticker, ticker_err = self.kraken.get_ticker(symbol)\n"
        "                        if ticker_ok:\n"
        "                            exit_price = ticker.get('last', trade['entry_price'])\n"
        "                        else:\n"
        "                            exit_price = trade['entry_price']\n"
        "                    except Exception:\n"
        "                        exit_price = trade['entry_price']\n",

        "Bug B1: unpack get_ticker tuple in 'SL already triggered' branch (24sp indent)"
    ),

    # ---- BUG B2 ----
    # B2 has logger.warning right after the ticker.get. Try multiple indent levels
    # by anchoring to logger.warning's exact text. We'll match a pattern starting
    # with the exit_price line, since the get_ticker line is identical at both B1 and B2.
    # However we need to be careful since the pattern must be unique.
    # The logger.warning line is unique to B2.
    # Match: 'logger.warning' line + the except block following it.
    # This won't include the get_ticker call itself but B2's whole block doesn't need full replacement
    # if we just insert tuple-unpacking logic at the top.
    # Actually simpler: match just the get_ticker line followed by the warning,
    # then build the right replacement.
    #
    # From earlier sed output, B2 looks like:
    #     ticker = self.kraken.get_ticker(symbol)
    #     exit_price = ticker.get('last', trade['entry_price'])
    #     logger.warning(f"Exit: fill price unavailable for {symbol}, using last price: {exit_price}")
    # at 20sp indent (16sp for the try: above it).
    #
    # We don't know B2's exact indent yet. Try 20sp first (one level less than B1).
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

        "Bug B2: unpack get_ticker tuple in general fill-price fallback (20sp indent)"
    ),

    # ---- BUG C.1: REMOVE the broken 9-kwarg log_trade call ----
    # Indent: probably 12sp (one level inside the close-handler method).
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

    # ---- BUG C.2: extend the existing trade-dict enrichment + insert log_trade(trade) ----
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

print("Session 3 patch script v5 (main.py only, indents corrected from diagnostic)")
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
