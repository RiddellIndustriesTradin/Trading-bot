#!/usr/bin/env python3
"""
Patch script for risk_manager.py — fix pre-funding edge cases.

Bugs fixed:
  A. Daily loss check false-fires on $0 equity.
     `if daily_pnl <= (current_equity * max_daily_loss)` → 0 <= 0 → True.
     Fix: add `daily_pnl < 0` guard so we only check loss when there's actually a loss.

  B. Drawdown defaults to fantasy $100k baseline.
     `peak = self.state.get("peak_equity", 100000)` makes a $0 unfunded account
     look like 100% drawdown vs imaginary $100k peak.
     Fix: default peak to 0; existing `if peak == 0: return 0` guard handles it cleanly.
     Real peak gets set later by record_trade_exit when actual trades happen.

  C. Drawdown display string shows decimal as "1.0%" instead of "100%".
     `{drawdown_pct:.1f}%` shows raw decimal (0.05 displays as "0.0%").
     Fix: multiply by 100 in format string to match the threshold-side formatting.

USAGE:
  cd ~/Desktop/Projects/Proppa_Kraken_Crypto
  python3 patch_risk_manager.py --dry-run
  python3 patch_risk_manager.py --apply
"""

import sys
from pathlib import Path

TARGET = Path("risk_manager.py")

# ---- Fix A: daily loss check ----
OLD_DAILY_LOSS = '''        # Check daily loss limit
        if self.state["daily_pnl"] <= (current_equity * self.max_daily_loss):
            return False, f"❌ Daily loss limit (-3%) reached: ${self.state[\'daily_pnl\']:.2f}"'''

NEW_DAILY_LOSS = '''        # Check daily loss limit (only meaningful if we've actually lost something today)
        if self.state["daily_pnl"] < 0 and self.state["daily_pnl"] <= (current_equity * self.max_daily_loss):
            return False, f"❌ Daily loss limit (-3%) reached: ${self.state[\'daily_pnl\']:.2f}"'''

# ---- Fix C: drawdown display unit ----
OLD_DRAWDOWN_DISPLAY = '''        if drawdown_pct >= self.max_drawdown_hard_stop:
            return False, f"🛑 HARD STOP: Drawdown {drawdown_pct:.1f}% ≥ {self.max_drawdown_hard_stop*100:.0f}%"'''

NEW_DRAWDOWN_DISPLAY = '''        if drawdown_pct >= self.max_drawdown_hard_stop:
            return False, f"🛑 HARD STOP: Drawdown {drawdown_pct*100:.1f}% ≥ {self.max_drawdown_hard_stop*100:.0f}%"'''

# ---- Fix B: drawdown peak default ----
OLD_PEAK_DEFAULT = '''        peak = self.state.get("peak_equity", 100000)
        if peak == 0:
            return 0
        return (peak - current_equity) / peak'''

NEW_PEAK_DEFAULT = '''        # Default peak to 0 so unfunded/uninitialized state returns 0% drawdown.
        # Real peak gets set by record_trade_exit() once equity is actually tracked.
        peak = self.state.get("peak_equity", 0)
        if peak == 0:
            return 0
        return (peak - current_equity) / peak'''


def patch(content: str):
    results = []

    # Fix A
    if OLD_DAILY_LOSS in content:
        content = content.replace(OLD_DAILY_LOSS, NEW_DAILY_LOSS)
        results.append(("OK", "Fix A: daily loss check now requires actual loss"))
    elif 'daily_pnl"] < 0 and self.state["daily_pnl"]' in content:
        results.append(("SKIP", "Fix A: already patched"))
    else:
        results.append(("FAIL", "Fix A: couldn't find daily loss check block"))

    # Fix C
    if OLD_DRAWDOWN_DISPLAY in content:
        content = content.replace(OLD_DRAWDOWN_DISPLAY, NEW_DRAWDOWN_DISPLAY)
        results.append(("OK", "Fix C: drawdown display unit corrected"))
    elif 'drawdown_pct*100:.1f' in content:
        results.append(("SKIP", "Fix C: already patched"))
    else:
        results.append(("FAIL", "Fix C: couldn't find drawdown display block"))

    # Fix B
    if OLD_PEAK_DEFAULT in content:
        content = content.replace(OLD_PEAK_DEFAULT, NEW_PEAK_DEFAULT)
        results.append(("OK", "Fix B: peak_equity default changed from 100000 to 0"))
    elif 'self.state.get("peak_equity", 0)' in content:
        results.append(("SKIP", "Fix B: already patched"))
    else:
        results.append(("FAIL", "Fix B: couldn't find peak default block"))

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
        print("Usage: python3 patch_risk_manager.py [--dry-run | --apply]")
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
        sys.exit(1)

    if patched == original:
        print("\nNo changes needed — file already up to date.")
        return

    TARGET.write_text(patched)
    print(f"\nWrote {TARGET}. Verify with: git diff {TARGET}")


if __name__ == "__main__":
    main()
